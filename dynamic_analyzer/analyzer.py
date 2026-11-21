"""Dynamic testing: generated probes for boundary, invalid and failure inputs.

All generated probes run inside the sandbox against a workspace copy.  They
never call functions whose names suggest destructive or external side effects.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from models.domain import (
    Component,
    ComponentType,
    EvidenceStatus,
    Finding,
    FindingSource,
    IssueCategory,
    Severity,
    TestResult,
    TestStatus,
    new_id,
)
from repository_analyzer.analyzer import RepositoryUnderstanding
from sandbox.executor import execute

_DANGEROUS = (
    "delete", "drop", "remove", "destroy", "shutdown", "restart", "execute",
    "exec", "system", "popen", "subprocess", "run", "main", "commit", "rollback",
    "publish", "send", "upload", "download", "reset", "clear", "kill", "write",
    "save", "update", "insert", "create", "install", "uninstall", "deploy",
)

_PROBE_TEMPLATE = r'''
import json, sys, traceback, inspect, importlib, os
targets = json.loads(r"""__TARGETS__""")
results = []
for entry in targets:
    mod, fname = entry["module"], ".__".join(entry["qualname"].split("."))
    rec = {"module": mod, "name": entry["qualname"], "cases": []}
    try:
        obj = __import__(mod, fromlist=["*"])
        for part in entry["qualname"].split("."):
            obj = getattr(obj, part)
    except BaseException as exc:
        rec["import_error"] = type(exc).__name__ + ": " + str(exc)[:400]
        rec["import_trace"] = traceback.format_exc()[-2500:]
        results.append(rec)
        continue
    if not callable(obj):
        rec["skip"] = "not callable"
        results.append(rec)
        continue
    for label, value in [("empty_str", ""), ("zero", 0), ("negative", -1),
                         ("empty_list", []), ("empty_dict", {}), ("none", None)]:
        try:
            nparams = len(inspect.signature(obj).parameters)
        except (TypeError, ValueError):
            nparams = 1
        args = ([value] * min(nparams, 2)) if nparams else []
        try:
            result = obj(*args)
            rec["cases"].append({"case": label, "ok": True,
                                 "result": repr(result)[:200]})
        except BaseException as exc:
            rec["cases"].append({"case": label, "ok": False,
                                 "error": type(exc).__name__,
                                 "message": str(exc)[:400],
                                 "trace": traceback.format_exc()[-2500:]})
    results.append(rec)
print("__RCA_PROBE__" + json.dumps(results))
'''


def _safe_name(name: str) -> bool:
    low = name.lower()
    return not any(word in low for word in _DANGEROUS) and not name.startswith("_")


def _module_path(path: str) -> str:
    """Return the importable dotted module path for a repository-relative file."""
    p = Path(path)
    if p.name == "__init__.py":
        return ".".join(p.parent.parts)
    return ".".join(p.with_suffix("").parts)


def _targets(understanding: RepositoryUnderstanding,
             finding_files: set) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    seen: set = set()
    # Prioritise components in files with deterministic findings.
    comps = sorted(
        understanding.components,
        key=lambda c: (0 if c.path in finding_files else 1,
                       0 if c.type == ComponentType.FUNCTION else 1),
    )
    for comp in comps:
        if comp.type != ComponentType.FUNCTION or not comp.path:
            continue
        # Class methods are discovered as functions, but probing them unbound
        # produces misleading TypeErrors about missing/incorrect `self`.
        if comp.metadata.get("class") or "." in comp.name:
            continue
        if not _safe_name(comp.name.split(".")[-1]):
            continue
        if not comp.path.endswith(".py"):
            continue
        mod = _module_path(comp.path)
        key = (mod, comp.name)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"module": mod, "qualname": comp.name, "file": comp.path})
        if len(targets) >= settings.max_files_in_context:
            break
    return targets


def analyze(workspace: Path, understanding: RepositoryUnderstanding,
            findings: List[Finding]) -> Tuple[List[TestResult], List[Finding], Dict[str, Any]]:
    if not settings.run_dynamic_tests:
        return [], [], {"status": "DISABLED"}
    finding_files = {f.file for f in findings if f.file}
    targets = _targets(understanding, finding_files)
    if not targets:
        return [], [], {"status": "NO_TARGETS", "detail": "No safe callable components found"}

    probe = workspace / ".rca_dynamic_probe.py"
    probe.write_text(
        _PROBE_TEMPLATE.replace("__TARGETS__", json.dumps(
            [{"module": t["module"], "qualname": t["qualname"]} for t in targets]
        )),
        encoding="utf-8",
    )
    import os

    result = execute(
        ["python", str(probe)], cwd=workspace, timeout=settings.command_timeout,
        env_extra={"PYTHONPATH": str(workspace) + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    tests: List[TestResult] = []
    generated: List[Finding] = []
    payload = ""
    for line in result.stdout.splitlines():
        if line.startswith("__RCA_PROBE__"):
            payload = line[len("__RCA_PROBE__"):]
    parsed: List[Dict[str, Any]] = []
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = []
    for entry, target in zip(parsed, targets):
        name = entry.get("name", target["qualname"])
        if entry.get("import_error"):
            msg = entry["import_error"]
            tests.append(TestResult(
                id=new_id("DTEST"), name=f"DYNAMIC::{name}::import",
                component=target["file"], status=TestStatus.ERROR,
                error_message=msg, stack_trace=entry.get("import_trace", ""),
                framework="generated-probe", test_input="import module",
                expected="module imports successfully", actual=msg,
            ))
            generated.append(Finding(
                id=new_id("DYN"), type="DynamicImportFailure",
                severity=Severity.HIGH, category=IssueCategory.RUNTIME,
                file=target["file"], message=f"Dynamic probe failed to import {name}: {msg}",
                evidence=[f"Generated probe import failed for {name}", msg,
                          "Executed in isolated sandbox copy"],
                source=FindingSource.DYNAMIC, status=EvidenceStatus.CONFIRMED,
                reproducible=True, confidence=0.95,
                stack_trace=entry.get("import_trace", ""),
                metadata={"target": name, "target_file": target["file"]},
            ))
            continue
        failures = [c for c in entry.get("cases", []) if not c.get("ok")]
        if failures:
            first = failures[0]
            detail = "; ".join(
                f"{c['case']} -> {c.get('error')}: {c.get('message')}" for c in failures
            )
            tests.append(TestResult(
                id=new_id("DTEST"), name=f"DYNAMIC::{name}::boundary",
                component=target["file"], status=TestStatus.FAIL,
                error_message=detail[:1200],
                stack_trace="\n".join(c.get("trace", "") for c in failures)[:5000],
                framework="generated-probe",
                test_input="empty string, 0, -1, [], {}, None",
                expected="function handles invalid/boundary inputs gracefully",
                actual=detail[:1200],
            ))
            generated.append(Finding(
                id=new_id("DYN"), type="DynamicBoundaryFailure",
                severity=Severity.MEDIUM, category=IssueCategory.RUNTIME,
                file=target["file"], message=f"{name} failed on boundary inputs: {detail[:180]}",
                evidence=[f"Generated probe called {name} with 6 boundary inputs",
                          f"{len(failures)} input(s) raised exceptions",
                          "Executed in isolated sandbox copy"],
                source=FindingSource.DYNAMIC, status=EvidenceStatus.CONFIRMED,
                reproducible=True, confidence=0.85,
                metadata={"target": name, "target_file": target["file"],
                          "cases": failures},
            ))
        else:
            tests.append(TestResult(
                id=new_id("DTEST"), name=f"DYNAMIC::{name}::boundary",
                component=target["file"], status=TestStatus.PASS,
                framework="generated-probe",
                test_input="empty string, 0, -1, [], {}, None",
                expected="no unexpected exceptions", actual="no exceptions",
            ))
    probe.unlink(missing_ok=True)
    return tests, generated, {
        "status": "OK" if parsed else "NO_DATA",
        "targets": len(targets), "results": len(parsed),
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stderr": result.stderr[:600],
    }
