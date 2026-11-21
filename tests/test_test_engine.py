from __future__ import annotations

from pathlib import Path

from models.domain import TestStatus
from repository_analyzer.analyzer import analyze_repository
from test_engine.runner import detect_frameworks, run_tests


def test_framework_detection(simple_python_repo: Path) -> None:
    frameworks = detect_frameworks(simple_python_repo, analyze_repository(simple_python_repo))
    assert "pytest" in frameworks


def test_run_passing_tests(sandbox_copy: Path) -> None:
    understanding = analyze_repository(sandbox_copy)
    runs, findings = run_tests(sandbox_copy, understanding)
    assert runs
    tests = [t for r in runs for t in r.tests]
    assert any(t.status == TestStatus.PASS for t in tests)
    assert not [f for f in findings if f.severity.value in ("HIGH", "CRITICAL")]


def test_run_failing_test(tmp_path: Path) -> None:
    root = tmp_path / "failing"
    root.mkdir()
    (root / "test_bad.py").write_text(
        "def test_bad():\n    assert 1 == 2, 'intentional mismatch'\n", encoding="utf-8"
    )
    understanding = analyze_repository(root)
    runs, findings = run_tests(root, understanding, ["pytest"])
    tests = [t for r in runs for t in r.tests]
    assert any(t.status == TestStatus.FAIL for t in tests)
    assert any(f.category.value == "Test Failure" for f in findings)
