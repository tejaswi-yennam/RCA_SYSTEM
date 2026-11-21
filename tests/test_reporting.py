from __future__ import annotations

from pathlib import Path

from models.domain import (
    AnalysisRun,
    AnalysisStatus,
    CapabilityResult,
    CapabilityStatus,
    Finding,
    RootCauseGraph,
    IssueCategory,
    Severity,
    TestResult,
    TestStatus,
)
from reporting.generator import build_report, redact
from repository_analyzer.analyzer import analyze_repository


def test_redaction() -> None:
    assert "AKIA" not in redact("key=AKIA1234567890ABCDEF")
    assert "[REDACTED]" in redact("password=hunter2secret")


def test_report_sections(tmp_path: Path) -> None:  # type: ignore[valid-type]
    understanding = analyze_repository(tmp_path)
    analysis = AnalysisRun(
        id="RUN-TEST", repository_id="REPO-TEST", status=AnalysisStatus.COMPLETED,
        capabilities=[CapabilityResult("static_analysis", CapabilityStatus.OK)],
        summary={"assessment": "NOT READY"},
    )
    finding = Finding(id="F1", type="MissingConfiguration", severity=Severity.HIGH,
                      category=IssueCategory.CONFIGURATION, file="src/config.py",
                      message="DB_URL missing", confidence=0.95, status="Confirmed")
    test = TestResult(id="T1", name="test_x", status=TestStatus.FAIL,
                      error_message="KeyError: DB_URL")
    report = build_report(
        analysis, understanding.profile, understanding.components, [test], [finding],
        [], [], RootCauseGraph(),
        [], [], [], [], ["No tests for config"],
    )
    md = report["markdown"]
    for section in ("Executive Summary", "Repository Analysis", "Testing Summary",
                    "Issue Summary", "Detailed RCA", "Root Cause Graph",
                    "Missing Test Coverage", "Recommended Fix Order", "Final Assessment"):
        assert section in md
    assert report["assessment"] in ("READY", "READY WITH WARNINGS", "NOT READY", "CRITICAL FAILURE")
