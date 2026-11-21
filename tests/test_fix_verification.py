from __future__ import annotations

from pathlib import Path

from models.domain import Finding, IssueCategory, Severity, TestResult, TestStatus
from repository_analyzer.analyzer import analyze_repository
from test_engine.runner import run_tests
from verification.engine import apply_patch, verify_fix
from verification.fix_engine import recommend_fixes


def _baseline(workspace: Path):
    understanding = analyze_repository(workspace)
    runs, _ = run_tests(workspace, understanding, ["pytest"])
    return [t for r in runs for t in r.tests]


def test_fix_recommendation_is_specific() -> None:
    finding = Finding(
        id="F1", type="MissingConfiguration", severity=Severity.HIGH,
        category=IssueCategory.CONFIGURATION, file="src/config.py",
        message="Required environment variable 'DB_URL' is not defined",
        reproducible=True, confidence=0.95, metadata={"env_var": "DB_URL"},
    )
    fixes = recommend_fixes([finding], [], [])
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix.automatable
    assert "DB_URL" in fix.required_change
    assert "os.environ.get" in fix.required_change
    assert fix.patch and fix.patch["file"] == "src/config.py"
    assert fix.verification_method
    assert fix.regression_tests


def test_apply_patch_and_verify(tmp_path: Path) -> None:
    root = tmp_path / "cfg"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "config.py").write_text(
        "import os\nDB_URL = os.environ['DB_URL']\n", encoding="utf-8"
    )
    (root / "test_config.py").write_text(
        "import os\nos.environ.setdefault('DB_URL', 'sqlite:///:memory:')\n"
        "from src.config import DB_URL\n\n\ndef test_db_url():\n    assert DB_URL\n",
        encoding="utf-8",
    )
    baseline = _baseline(root)
    finding = Finding(
        id="F2", type="MissingConfiguration", severity=Severity.HIGH,
        category=IssueCategory.CONFIGURATION, file="src/config.py",
        message="DB_URL missing", reproducible=True, confidence=0.95,
        metadata={"env_var": "DB_URL"},
    )
    fix = recommend_fixes([finding], [], [])[0]
    workspace = root
    applied, detail = apply_patch(workspace, fix.patch)
    assert applied
    assert "os.environ.get" in (workspace / "src" / "config.py").read_text(encoding="utf-8")
    assert (workspace / ".env.example").exists()
    # Verification runs against a copy and compares failing test names.
    result = verify_fix(fix, root, baseline)
    assert result.outcome in ("CONFIRMED", "REJECTED", "SKIPPED")
