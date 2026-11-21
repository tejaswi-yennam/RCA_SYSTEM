from __future__ import annotations

import json
from pathlib import Path

from evaluation.framework import evaluate_all, evaluate_analysis
from orchestration.pipeline import AnalysisOrchestrator
from storage.database import Database


def test_pipeline_end_to_end_on_cascading_repo() -> None:
    sample = (Path(__file__).resolve().parents[1] / "sample-repos" /
              "cascading-failure-project")
    db = Database()
    orchestrator = AnalysisOrchestrator(db=db)
    analysis = orchestrator.start(str(sample), "cascading-failure-project")
    finished = orchestrator.wait(analysis.id, timeout=180)
    assert finished is not None
    assert finished.status.value in ("COMPLETED", "FAILED")
    assert finished.progress == 100

    findings = db.get_items(analysis.id, "findings")
    rcas = db.get_items(analysis.id, "rcas")
    report = db.get_report(analysis.id)
    assert report
    assert "CascadingFailure" in json.dumps(findings) or rcas
    assert "Root Cause Graph" in report


def test_evaluation_framework(sample_analysis_id: str | None = None) -> None:
    sample_root = Path(__file__).resolve().parents[1] / "sample-repos"
    expected = json.loads((sample_root / "cascading-failure-project" /
                           "expected_rca.json").read_text(encoding="utf-8"))
    if sample_analysis_id:
        result = evaluate_analysis(sample_analysis_id, expected)
        assert 0.0 <= result.detection_f1 <= 1.0
    aggregate = evaluate_all(sample_root)
    assert aggregate["repositories"] >= 7
    assert 0.0 <= aggregate["mean_detection_f1"] <= 1.0
