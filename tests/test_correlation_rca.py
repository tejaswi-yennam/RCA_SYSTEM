from __future__ import annotations

from pathlib import Path

from evidence.store import EvidenceStore
from models.domain import (
    Finding,
    FindingSource,
    IssueCategory,
    Severity,
    new_id,
)
from repository_analyzer.analyzer import analyze_repository
from correlation.correlator import correlate
from rca.engine import build_rcas
from graph.root_cause_graph import build_graph, to_mermaid


def _finding(type_, category, file, message, source=FindingSource.DETERMINISTIC, confidence=0.9):
    return Finding(id=new_id("F"), type=type_, category=category, file=file,
                   message=message, severity=Severity.HIGH, source=source,
                   confidence=confidence, status="Confirmed", reproducible=True,
                   metadata={"env_var": "DB_URL"})


def test_correlates_cascade(tmp_path: Path) -> None:
    understanding = analyze_repository(tmp_path)
    root_f = _finding("MissingConfiguration", IssueCategory.CONFIGURATION,
                      "src/config.py", "DB_URL missing")
    sym1 = _finding("ERROR", IssueCategory.TEST_FAILURE, "tests/test_a.py",
                    "KeyError: DB_URL", source=FindingSource.TEST)
    sym2 = _finding("ERROR", IssueCategory.TEST_FAILURE, "tests/test_b.py",
                    "KeyError DB_URL in stack trace", source=FindingSource.TEST)
    store = EvidenceStore()
    for f in (root_f, sym1, sym2):
        store.add_finding(f)
    groups, assignment = correlate([root_f, sym1, sym2], understanding,
                                   {root_f.id: [sym1.id, sym2.id]})
    assert len(groups) == 1
    assert groups[0].root_issue_id == root_f.id
    assert len(groups[0].dependent_issue_ids) == 2
    assert assignment[sym1.id] == groups[0].id

    chains = build_rcas(groups, [root_f, sym1, sym2], store)
    assert len(chains) == 1
    assert "MissingConfiguration" in chains[0].root_cause
    assert len(chains[0].downstream_impact) == 2
    assert chains[0].status.value == "Confirmed"

    graph = build_graph([root_f, sym1, sym2], groups, chains,
                        understanding.components, understanding.edges, [])
    assert any(e.kind.value == "causes" for e in graph.edges)
    assert "flowchart TD" in to_mermaid(graph)
