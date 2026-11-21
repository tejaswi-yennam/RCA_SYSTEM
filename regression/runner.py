"""Regression testing after a verified fix."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Set

from config.settings import settings
from models.domain import (
    FixRecommendation,
    RegressionResult,
    TestResult,
    TestStatus,
    VerificationResult,
    new_id,
)
from sandbox.executor import execute, make_workspace
from verification.engine import apply_patch, _failing_names


def run_regression(
    fix: FixRecommendation,
    verification: VerificationResult,
    original_workspace: Path,
    baseline_tests: List[TestResult],
) -> RegressionResult:
    result = RegressionResult(id=new_id("REG"), fix_id=fix.id)
    if verification.outcome != "CONFIRMED" or not fix.patch:
        result.details = "Skipped: fix not confirmed or no automatable patch"
        return result

    workspace = make_workspace(original_workspace, prefix="rca_reg_")
    try:
        apply_patch(workspace, fix.patch)
        import os

        run = execute(
            ["python", "-m", "pytest", "--junitxml=.rca_pytest.xml", "-q", "--no-header"],
            cwd=workspace, timeout=settings.command_timeout,
            env_extra={"PYTHONPATH": str(workspace) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
        from test_engine.runner import _parse_junit

        patched, summary = _parse_junit(workspace, "pytest")
        report = workspace / ".rca_pytest.xml"
        if report.exists():
            report.unlink(missing_ok=True)
        baseline_failing = _failing_names(baseline_tests)
        patched_failing = _failing_names(patched)
        result.total_tests = summary["total"]
        result.passed = summary["passed"]
        result.failed = summary["failed"]
        result.new_failures = sorted(patched_failing - baseline_failing)
        result.resolved_failures = sorted(baseline_failing - patched_failing)
        ok = not result.new_failures and result.failed == 0
        result.details = (
            f"{result.passed}/{result.total_tests} passed; "
            f"{len(result.resolved_failures)} resolved; "
            f"{len(result.new_failures)} new failures. "
            + ("Regression check passed." if ok else "Regression check requires review.")
        )
        if run.timed_out:
            result.details += " Test run timed out."
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
