"""Dependency, version and environment validation."""
from __future__ import annotations

import importlib.util
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from models.domain import (
    EvidenceStatus,
    Finding,
    FindingSource,
    IssueCategory,
    Severity,
    new_id,
)
from repository_analyzer.analyzer import RepositoryUnderstanding

_IMPORT_STDLIB_HINT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _finding(**kwargs: Any) -> Finding:
    kwargs.setdefault("source", FindingSource.DETERMINISTIC)
    kwargs.setdefault("id", new_id("DEP"))
    return Finding(**kwargs)


def analyze(workspace: Path, understanding: RepositoryUnderstanding,
            static_findings: List[Finding]) -> Tuple[List[Finding], Dict[str, Any]]:
    findings: List[Finding] = []
    profile = understanding.profile
    details: Dict[str, Any] = {"declared": {}, "missing": []}

    declared: Set[str] = set()
    for values in profile.dependencies.values():
        for dep in values:
            name = re.split(r"[<>=!~\[\s]", dep)[0].strip("-_./")
            if name:
                declared.add(name.lower().replace("_", "-"))
    details["declared"] = sorted(declared)

    local_names = {
        Path(p).stem.lower() for p in understanding.files if p.endswith(".py")
    }
    local_names |= {
        Path(p).parts[0].lower() for p in understanding.files
        if len(Path(p).parts) > 1
    }
    imported: Dict[str, List[str]] = defaultdict(list)
    raw_names: Dict[str, str] = {}
    for edge in understanding.edges:
        if edge.detail in ("external/imported dependency", "import reference"):
            target = edge.target.split(".")[0].lower().replace("_", "-")
            if target and len(target) > 1:
                imported[target].append(edge.source)
                raw_names.setdefault(target, edge.target)

    installed: Set[str] = set()
    for name in declared | set(imported):
        try:
            if importlib.util.find_spec(name.replace("-", "_")) is not None:
                installed.add(name)
        except (ImportError, ValueError, ModuleNotFoundError):
            pass

    for name, files in imported.items():
        raw = raw_names.get(name, "")
        raw_parts = {raw.replace("\\", "/").split("/")[0].lower(),
                     Path(raw.replace("\\", "/")).stem.lower()}
        if (name in declared or name in installed or raw_parts & local_names):
            continue
        if name in {"src", "app", "lib", "utils", "core", "config", "models", "tests", "test"}:
            continue
        findings.append(_finding(
            type="UndeclaredDependency", severity=Severity.MEDIUM,
            category=IssueCategory.DEPENDENCY,
            file=files[0] if files else "",
            message=f"Imported package '{name}' is not declared as a dependency",
            evidence=[f"Import found in {len(files)} component(s)",
                      f"Declared dependencies: {sorted(declared) or 'none'}",
                      f"Not importable in analysis environment"],
            reproducible=False, confidence=0.7,
            status=EvidenceStatus.POTENTIAL,
            metadata={"dependency": name},
        ))
        details["missing"].append(raw_names.get(name, name))

    # Duplicate/conflicting declarations (e.g. two incompatible version specs).
    version_specs: Dict[str, Set[str]] = defaultdict(set)
    req = workspace / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([\w\-]+)\s*([<>=!~].+)?", line)
            if m and m.group(1):
                version_specs[m.group(1).lower()].add((m.group(2) or "").strip())
    for name, specs in version_specs.items():
        if len([s for s in specs if s]) > 1:
            findings.append(_finding(
                type="VersionConflict", severity=Severity.HIGH,
                category=IssueCategory.DEPENDENCY, file="requirements.txt",
                message=f"Conflicting version constraints for '{name}': {sorted(specs)}",
                evidence=[f"Multiple specs declared: {sorted(specs)}"],
                reproducible=True, confidence=0.92,
                status=EvidenceStatus.CONFIRMED,
            ))

    # Environment variables referenced but not defined anywhere.
    config_findings = [f for f in static_findings if f.type == "MissingConfiguration"]
    for f in config_findings:
        findings.append(_finding(
            type="MissingEnvironmentVariable",
            severity=f.severity, category=IssueCategory.CONFIGURATION,
            file=f.file, line=f.line,
            message=f.message, evidence=list(f.evidence),
            reproducible=True, confidence=f.confidence,
            status=f.status, metadata=dict(f.metadata),
        ))
    details["config_findings"] = len(config_findings)
    return findings, details
