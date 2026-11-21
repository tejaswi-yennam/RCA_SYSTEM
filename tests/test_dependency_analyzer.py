from __future__ import annotations

from pathlib import Path

from dependency_analyzer.analyzer import analyze
from repository_analyzer.analyzer import analyze_repository
from static_analyzer.analyzer import analyze as static_analyze


def test_missing_dependency_detected(tmp_path: Path) -> None:
    root = tmp_path / "deps"
    root.mkdir()
    (root / "app.py").write_text("import totally_missing_pkg_123\n", encoding="utf-8")
    understanding = analyze_repository(root)
    static, _ = static_analyze(root, understanding)
    dep, details = analyze(root, understanding, static)
    assert any(f.type == "MissingDependency" for f in static)
    assert any(f.type == "UndeclaredDependency" for f in dep)
    assert "totally_missing_pkg_123" in details["missing"]


def test_version_conflict(tmp_path: Path) -> None:
    root = tmp_path / "conflict"
    root.mkdir()
    (root / "requirements.txt").write_text("requests>=2.0\nrequests<2.20\n", encoding="utf-8")
    understanding = analyze_repository(root)
    findings, _ = analyze(root, understanding, [])
    assert any(f.type == "VersionConflict" for f in findings)
