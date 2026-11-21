from __future__ import annotations

from pathlib import Path

from dynamic_analyzer.analyzer import analyze
from models.domain import FindingSource, TestStatus
from repository_analyzer.analyzer import analyze_repository


def test_boundary_probe_runs(sandbox_copy: Path) -> None:
    understanding = analyze_repository(sandbox_copy)
    tests, findings, details = analyze(sandbox_copy, understanding, [])
    assert details["status"] == "OK"
    assert tests
    assert all(t.framework == "generated-probe" for t in tests)
    assert all(f.source == FindingSource.DYNAMIC for f in findings)


def test_dangerous_functions_skipped(tmp_path: Path) -> None:
    root = tmp_path / "danger"
    root.mkdir()
    (root / "mod.py").write_text(
        "class Helper:\n"
        "    def safe_method(self, x):\n"
        "        return x\n\n"
        "def delete_all(x):\n    return x\n\ndef safe_func(x):\n    return x\n",
        encoding="utf-8",
    )
    understanding = analyze_repository(root)
    tests, _, _ = analyze(root, understanding, [])
    names = " ".join(t.name for t in tests)
    assert "delete_all" not in names
    assert "safe_method" not in names
    assert "safe_func" in names
