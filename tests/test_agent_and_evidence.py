from __future__ import annotations

from pathlib import Path

from agent.investigator import AgentInvestigation, investigate
from evidence.store import EvidenceStore
from models.domain import EvidenceStatus, FindingSource, Severity, IssueCategory
from repository_analyzer.analyzer import analyze_repository
from static_analyzer.analyzer import analyze as static_analyze
from test_engine.runner import run_tests


def test_evidence_store_links_findings() -> None:
    from models.domain import Finding, new_id

    store = EvidenceStore()
    f = Finding(id=new_id("F"), type="X", severity=Severity.HIGH,
                category=IssueCategory.CONFIGURATION, message="missing config")
    store.add_finding(f)
    assert store.evidence_for(f.id)
    assert store.confidence_for(f.id) == f.confidence
    assert store.summarize()["findings"] == 1


def test_agent_links_root_to_symptoms(broken_python_repo: Path) -> None:
    understanding = analyze_repository(broken_python_repo)
    static, _ = static_analyze(broken_python_repo, understanding)
    runs, test_findings = run_tests(broken_python_repo, understanding, ["pytest"])
    tests = [t for r in runs for t in r.tests]
    findings = static + test_findings
    store = EvidenceStore()
    for f in findings:
        store.add_finding(f)
    for t in tests:
        store.add_test(t)

    report = investigate(broken_python_repo, understanding, findings, tests, store)
    assert isinstance(report, AgentInvestigation)
    assert report.hypotheses
    assert any(h.status in ("CONFIRMED", "SUPPORTED") for h in report.hypotheses)
    assert report.summary["hypotheses"] == len(report.hypotheses)
    assert any(f.source == FindingSource.AGENT for f in report.findings)
