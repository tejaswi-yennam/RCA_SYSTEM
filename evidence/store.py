"""Evidence collection and confidence bookkeeping."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from models.domain import (
    Evidence,
    EvidenceStatus,
    Finding,
    FindingSource,
    Hypothesis,
    TestResult,
    new_id,
)


class EvidenceStore:
    """Accumulates evidence linking findings, tests and agent hypotheses."""

    def __init__(self) -> None:
        self._records: Dict[str, Evidence] = {}
        self._findings: Dict[str, Finding] = {}
        self._tests: Dict[str, TestResult] = {}

    def add_finding(self, finding: Finding) -> None:
        self._findings[finding.id] = finding
        self.add(Evidence(
            id=new_id("EV"),
            finding=finding.message,
            items=[f"Finding {finding.id} ({finding.source.value})", *finding.evidence],
            confidence=finding.confidence,
            source=finding.source,
            status=finding.status,
            related_findings=[finding.id],
        ))

    def add_test(self, test: TestResult) -> None:
        self._tests[test.id] = test

    def add(self, evidence: Evidence) -> Evidence:
        self._records[evidence.id] = evidence
        return evidence

    def from_hypothesis(self, hypothesis: Hypothesis) -> Evidence:
        ev = Evidence(
            id=new_id("EV"),
            finding=hypothesis.statement,
            items=[*hypothesis.supporting_evidence, *hypothesis.investigation_notes],
            confidence=hypothesis.confidence,
            source=FindingSource.AGENT,
            status=(
                EvidenceStatus.CONFIRMED if hypothesis.status == "CONFIRMED"
                else EvidenceStatus.PROBABLE if hypothesis.status == "SUPPORTED"
                else EvidenceStatus.POTENTIAL
            ),
            related_findings=hypothesis.root_cause_finding_ids,
            related_tests=[
                t.id for t in self._tests.values()
                if any(t.name in item for item in hypothesis.supporting_evidence)
            ],
        )
        return self.add(ev)

    def evidence_for(self, finding_id: str) -> List[Evidence]:
        return [e for e in self._records.values() if finding_id in e.related_findings]

    def all(self) -> List[Evidence]:
        return list(self._records.values())

    def confidence_for(self, finding_id: str) -> float:
        evs = self.evidence_for(finding_id)
        if not evs:
            f = self._findings.get(finding_id)
            return f.confidence if f else 0.0
        return max(e.confidence for e in evs)

    def summarize(self) -> Dict[str, int]:
        return {
            "total_evidence": len(self._records),
            "findings": len(self._findings),
            "tests": len(self._tests),
        }
