from __future__ import annotations

from pathlib import Path

from models.domain import ComponentType
from repository_analyzer.analyzer import analyze_repository


def test_profile_detection(simple_python_repo: Path) -> None:
    understanding = analyze_repository(simple_python_repo)
    assert "Python" in understanding.profile.languages
    assert "pytest" in understanding.profile.test_frameworks
    assert understanding.profile.total_files >= 4
    assert "requirements.txt" in understanding.profile.config_files


def test_component_discovery(simple_python_repo: Path) -> None:
    understanding = analyze_repository(simple_python_repo)
    names = {c.name for c in understanding.components}
    assert "calc" in names
    assert "add" in names
    assert "divide" in names
    assert any(c.type == ComponentType.TEST for c in understanding.components)
    assert any(c.type == ComponentType.FUNCTION for c in understanding.components)


def test_dependency_edges(simple_python_repo: Path) -> None:
    understanding = analyze_repository(simple_python_repo)
    edges = [e for e in understanding.edges if e.detail == "import reference"]
    assert any("test_calc" in e.source and "calc" in e.target for e in edges)


def test_parse_error_recorded(tmp_path: Path) -> None:
    root = tmp_path / "bad"
    root.mkdir()
    (root / "broken.py").write_text("def f(:\n  pass\n", encoding="utf-8")
    understanding = analyze_repository(root)
    assert understanding.parse_errors
    assert understanding.parse_errors[0]["file"] == "broken.py"
