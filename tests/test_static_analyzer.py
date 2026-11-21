from __future__ import annotations

from pathlib import Path

from repository_analyzer.analyzer import analyze_repository
from static_analyzer.analyzer import analyze


def test_syntax_detection(tmp_path: Path) -> None:
    root = tmp_path / "syntax"
    root.mkdir()
    (root / "broken.py").write_text("def add(a, b)\n    return a+b\n", encoding="utf-8")
    findings, details = analyze(root, analyze_repository(root))
    syntax = [f for f in findings if f.category.value == "Syntax Error"]
    assert syntax
    assert syntax[0].file == "broken.py"
    assert syntax[0].confidence >= 0.9
    assert details["syntax"]["status"] == "OK"


def test_missing_config_detection(broken_python_repo: Path) -> None:
    findings, _ = analyze(broken_python_repo, analyze_repository(broken_python_repo))
    missing = [f for f in findings if f.type == "MissingConfiguration"]
    assert missing
    assert missing[0].metadata["env_var"] == "DB_URL"
    assert missing[0].severity.value == "HIGH"


def test_local_c_extension_not_reported_missing(tmp_path: Path) -> None:
    root = tmp_path / "ext"
    root.mkdir()
    (root / "app.py").write_text("import _speedups\n", encoding="utf-8")
    (root / "_speedups.c").write_text("/* local extension source */\n", encoding="utf-8")
    findings, _ = analyze(root, analyze_repository(root))
    assert not [f for f in findings if f.type == "MissingDependency"]


def test_security_patterns(tmp_path: Path) -> None:
    root = tmp_path / "sec"
    root.mkdir()
    (root / "app.py").write_text(
        "api_key = 'abcdef1234567890'\nresult = eval('1+1')\n", encoding="utf-8"
    )
    findings, _ = analyze(root, analyze_repository(root))
    assert any(f.type == "PotentialSecret" for f in findings)
    assert any("eval" in f.type for f in findings)
    assert all("abcdef" not in f.message for f in findings)
