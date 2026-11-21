"""Existing-test execution subsystem with automatic framework detection."""
from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config.settings import settings
from models.domain import Finding, TestResult, TestStatus, new_id
from repository_analyzer.analyzer import RepositoryUnderstanding
from sandbox.executor import SandboxResult, execute


@dataclass
class TestRun:
    framework: str
    command: List[str]
    result: SandboxResult
    tests: List[TestResult] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    available: bool = True
    detail: str = ""


def detect_frameworks(workspace: Path, understanding: RepositoryUnderstanding) -> List[str]:
    profile = understanding.profile
    frameworks: List[str] = list(profile.test_frameworks)
    files = {p.name.lower() for p in workspace.rglob("*") if p.is_file()}
    rels = [p.as_posix().lower() for p in workspace.rglob("*") if p.is_file()]
    if any(n == "conftest.py" or n.startswith("test_") for n in files):
        if "pytest" not in frameworks:
            frameworks.append("pytest")
    if any("unittest" in rel for rel in rels):
        if "unittest" not in frameworks:
            frameworks.append("unittest")
    if any(n in {"jest.config.js", "jest.config.ts"} for n in files):
        if "Jest" not in frameworks:
            frameworks.append("Jest")
    if any(n == "go.mod" for n in files):
        if "go test" not in frameworks:
            frameworks.append("go test")
    if any(n == "cargo.toml" for n in files):
        if "cargo test" not in frameworks:
            frameworks.append("cargo test")
    if any(n == "pom.xml" for n in files):
        if "JUnit/Maven Surefire" not in frameworks:
            frameworks.append("JUnit/Maven Surefire")
    return frameworks or ["none"]


def _pytest_command(workspace: Path, test_paths: Optional[List[str]] = None) -> List[str]:
    cmd = ["python", "-m", "pytest", "--junitxml=.rca_pytest.xml", "-q", "--no-header",
           "-p", "no:cacheprovider"]
    for plugin in os.environ.get("RCA_PYTEST_PLUGINS", "").split(","):
        if plugin.strip():
            cmd.extend(["-p", plugin.strip()])
    if test_paths:
        cmd.extend(test_paths)
    return cmd


def _parse_junit(workspace: Path, framework: str) -> Tuple[List[TestResult], Dict[str, int]]:
    report = workspace / ".rca_pytest.xml"
    tests: List[TestResult] = []
    summary = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "total": 0}
    if not report.exists():
        return tests, summary
    try:
        tree = ET.parse(report)
    except ET.ParseError:
        return tests, summary
    for case in tree.iter("testcase"):
        name = case.get("name", "unknown")
        classname = case.get("classname", "")
        full_name = f"{classname}::{name}" if classname else name
        fail = case.find("failure")
        err = case.find("error")
        skip = case.find("skipped")
        if fail is not None:
            status, message, trace = TestStatus.FAIL, fail.get("message", ""), fail.text or ""
        elif err is not None:
            status, message, trace = TestStatus.ERROR, err.get("message", ""), err.text or ""
        elif skip is not None:
            status, message, trace = TestStatus.SKIPPED, skip.get("message", ""), ""
        else:
            status, message, trace = TestStatus.PASS, "", ""
        tests.append(TestResult(
            id=new_id("TEST"), name=full_name,
            component=classname.split(".")[0] if classname else "",
            status=status, duration=float(case.get("time", 0) or 0),
            error_message=message[:1500], stack_trace=trace[:6000],
            framework=framework,
            expected=(fail.get("type", "") if fail is not None else ""),
            actual=message[:1000],
        ))
        summary[{"PASS": "passed", "FAIL": "failed", "ERROR": "error", "SKIPPED": "skipped"}[status.value]] += 1
        summary["total"] += 1
    return tests, summary


def _parse_console(framework: str, output: str) -> Tuple[List[TestResult], Dict[str, int]]:
    """Fallback parser for frameworks without structured reports."""
    tests: List[TestResult] = []
    summary = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "total": 0}
    pattern = re.compile(r"(?P<name>[\w./:]*(?:test|spec)[\w./:]*)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED)")
    for line in output.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        status = TestStatus(m.group("status"))
        tests.append(TestResult(
            id=new_id("TEST"), name=m.group("name"), status=status,
            error_message=line.strip()[:1000], stack_trace=output[:6000],
            framework=framework,
        ))
        summary[{"PASS": "passed", "FAIL": "failed", "ERROR": "error", "SKIPPED": "skipped"}[status.value]] += 1
        summary["total"] += 1
    if not tests:
        m = re.search(r"(\d+)\s+failed", output, re.I)
        if m:
            summary["failed"] = int(m.group(1))
            summary["total"] += int(m.group(1))
        m = re.search(r"(\d+)\s+passed", output, re.I)
        if m:
            summary["passed"] = int(m.group(1))
            summary["total"] += int(m.group(1))
    return tests, summary


def _findings_from_tests(tests: List[TestResult], run: TestRun) -> List[Finding]:
    findings: List[Finding] = []
    for test in tests:
        if test.status not in (TestStatus.FAIL, TestStatus.ERROR):
            continue
        message = test.error_message or "Test failed"
        from models.domain import EvidenceStatus, FindingSource, IssueCategory, Severity

        category = IssueCategory.TEST_FAILURE
        severity = Severity.HIGH
        low = message.lower()
        if "modulenotfound" in low or "importerror" in low:
            category, severity = IssueCategory.DEPENDENCY, Severity.HIGH
        elif "AssertionError" in message or "assert" in low:
            category = IssueCategory.TEST_FAILURE  # keep reproducible test signal;
            # suspected logic cause is recorded in metadata for the agent
        elif "syntax" in low:
            category = IssueCategory.SYNTAX
        findings.append(Finding(
            id=new_id("TESTF"), type=test.status.value, severity=severity,
            category=category, component=test.component or test.name.split("::")[0],
            message=f"{test.name}: {message[:220]}",
            evidence=[f"Test {test.name} reported {test.status.value}",
                      message[:500], f"framework={run.framework}"],
            reproducible=True, source=FindingSource.TEST,
            status=EvidenceStatus.CONFIRMED, confidence=0.97,
            stack_trace=test.stack_trace[:4000],
            metadata={"test_id": test.id, "framework": run.framework,
                      "suspected_category": (
                          IssueCategory.LOGIC.value
                          if ("AssertionError" in message or "assert" in low) else category.value
                      )},
        ))
    return findings


def run_tests(workspace: Path, understanding: RepositoryUnderstanding,
              frameworks: Optional[List[str]] = None,
              test_paths: Optional[List[str]] = None,
              timeout: Optional[int] = None) -> Tuple[List[TestRun], List[Finding]]:
    frameworks = frameworks or detect_frameworks(workspace, understanding)
    runs: List[TestRun] = []
    findings: List[Finding] = []
    for framework in frameworks:
        if framework == "none":
            continue
        if framework == "pytest":
            cmd = _pytest_command(workspace, test_paths)
        elif framework == "unittest":
            cmd = ["python", "-m", "unittest", "discover", "-v"]
        elif framework in ("Jest", "Mocha"):
            pkg = "jest" if framework == "Jest" else "mocha"
            cmd = ["npm", "test", "--", "--runInBand"] if (workspace / "package.json").exists() else ["npx", pkg]
        elif framework == "go test":
            cmd = ["go", "test", "./..."]
        elif framework == "cargo test":
            cmd = ["cargo", "test"]
        elif framework in ("JUnit/Maven Surefire", "JUnit/Gradle"):
            cmd = ["mvn", "test"] if "Maven" in framework else ["gradle", "test"]
        else:
            continue
        if shutil.which(cmd[0]) is None and cmd[0] != "python":
            runs.append(TestRun(
                framework=framework, command=cmd,
                result=SandboxResult(cmd, str(workspace), 127, "", "", 0.0),
                available=False, detail=f"Runner '{cmd[0]}' not installed",
            ))
            continue
        # Make the repository root importable (pytest inserts only the test
        # directory by default, which breaks src/ package layouts).
        import os

        result = execute(
            cmd, cwd=workspace, timeout=timeout,
            env_extra={"PYTHONPATH": str(workspace) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
        run = TestRun(framework=framework, command=cmd, result=result)
        report = workspace / ".rca_pytest.xml"
        if framework == "pytest" and report.exists():
            run.tests, run.summary = _parse_junit(workspace, framework)
        else:
            run.tests, run.summary = _parse_console(framework, result.combined)
        if result.returncode not in (0, 5) and not run.tests:
            run.detail = result.stderr.strip()[:500] or result.stdout.strip()[:500]
        runs.append(run)
        findings.extend(_findings_from_tests(run.tests, run))
        if report.exists():
            report.unlink(missing_ok=True)
    return runs, findings
