"""Fix verification in an isolated workspace copy (never the user's repo)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings
from models.domain import FixRecommendation, TestResult, TestStatus, VerificationResult, new_id
from sandbox.executor import execute, make_workspace


def apply_patch(workspace: Path, patch: Dict[str, Any]) -> Tuple[bool, str]:
    """Apply a structured, bounded patch. Returns (changed, detail)."""
    changed = False
    notes: List[str] = []
    target = workspace / patch.get("file", "")
    for old, new in patch.get("replacements", []):
        if target.exists() and target.is_file():
            text = target.read_text(encoding="utf-8", errors="replace")
            if old in text:
                target.write_text(text.replace(old, new), encoding="utf-8")
                changed = True
                notes.append(f"replaced '{old[:60]}' in {patch['file']}")
    append = patch.get("append")
    if append:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(append)
        changed = True
        notes.append(f"appended to {patch['file']}")
    for name, content in (patch.get("append_files") or {}).items():
        dest = workspace / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = dest.read_text(encoding="utf-8") if dest.exists() else ""
        if content not in existing:
            with dest.open("a", encoding="utf-8") as fh:
                fh.write(content)
            changed = True
            notes.append(f"updated {name}")
    return changed, "; ".join(notes)


def _failing_names(tests: List[TestResult]) -> Set[str]:
    return {t.name for t in tests if t.status in (TestStatus.FAIL, TestStatus.ERROR)}


def verify_fix(
    fix: FixRecommendation,
    original_workspace: Path,
    baseline_tests: List[TestResult],
) -> VerificationResult:
    result = VerificationResult(id=new_id("VER"), fix_id=fix.id)
    if not settings.enable_fix_verification:
        result.outcome = "SKIPPED"
        result.details = "Fix verification disabled by configuration"
        return result
    if not fix.patch or not fix.automatable:
        result.outcome = "SKIPPED"
        result.details = (
            "No safe automated patch available; recommendation requires a human-reviewed change. "
            "Deterministic hypothesis verification evidence is reported in the RCA chain."
        )
        return result

    workspace = make_workspace(original_workspace, prefix="rca_fix_")
    try:
        applied, detail = apply_patch(workspace, fix.patch)
        result.applied = applied
        if not applied:
            result.outcome = "REJECTED"
            result.details = f"Patch could not be applied: {detail}"
            return result

        import os

        run = execute(
            ["python", "-m", "pytest", "--junitxml=.rca_pytest.xml", "-q", "--no-header"],
            cwd=workspace, timeout=settings.command_timeout,
            env_extra={"PYTHONPATH": str(workspace) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
        from test_engine.runner import _parse_junit

        patched_tests, _ = _parse_junit(workspace, "pytest")
        report = workspace / ".rca_pytest.xml"
        if report.exists():
            report.unlink(missing_ok=True)
        if not patched_tests:
            result.outcome = "REJECTED"
            result.details = (
                f"Could not evaluate patched tests: {run.stderr[:300] or 'no structured results'}"
            )
            result.test_output = run.combined[:3000]
            return result

        baseline_failing = _failing_names(baseline_tests)
        patched_failing = _failing_names(patched_tests)
        resolved = baseline_failing - patched_failing
        new_failures = patched_failing - baseline_failing
        result.original_failure_resolved = bool(resolved) if baseline_failing else True
        result.related_tests_passed = not new_failures
        result.no_new_failures = not new_failures
        result.duration = run.duration
        result.test_output = run.combined[:4000]
        if result.original_failure_resolved and result.no_new_failures:
            result.outcome = "CONFIRMED"
            result.details = (
                f"Applied {detail}; resolved {len(resolved)} failing test(s), no new failures."
            )
        else:
            result.outcome = "REJECTED"
            result.details = (
                f"Applied {detail}; resolved={sorted(resolved)[:3]}, "
                f"new_failures={sorted(new_failures)[:3]}"
            )
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
