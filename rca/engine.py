"""Root Cause Analysis engine: symptom -> cause -> root -> impact chains."""
from __future__ import annotations

from typing import Dict, List, Optional

from evidence.store import EvidenceStore
from models.domain import (
    EvidenceStatus,
    Finding,
    IssueCategory,
    IssueGroup,
    RCAChain,
    Severity,
    new_id,
    SEVERITY_RANK,
)


def _extract(groups: List[IssueGroup], findings: List[Finding]) -> Dict[str, Finding]:
    return {f.id: f for f in findings}


def build_rcas(groups: List[IssueGroup], findings: List[Finding],
               evidence_store: EvidenceStore) -> List[RCAChain]:
    by_id = _extract(groups, findings)
    chains: List[RCAChain] = []
    for group in groups:
        root = by_id.get(group.root_issue_id)
        if root is None:
            continue
        dependents = [by_id[i] for i in group.dependent_issue_ids if i in by_id]
        duplicates = [by_id[i] for i in group.duplicate_issue_ids if i in by_id]
        symptoms = sorted(
            dependents, key=lambda f: (-SEVERITY_RANK.get(f.severity, 0), f.location)
        ) or [root]

        primary = symptoms[0]
        impact = [
            f"{f.type} at {f.location}: {f.message[:150]}" for f in dependents
        ]
        if duplicates:
            impact.append(f"{len(duplicates)} duplicate observation(s) collapsed")
        contributing = [
            f"{f.type} ({f.location}): {f.message[:140]}"
            for f in dependents if f.id != primary.id
        ][:6]
        contributing.extend(root.evidence[:3])

        evidence_ids = [
            e.id for e in evidence_store.evidence_for(root.id)
        ]
        for dep in dependents:
            evidence_ids.extend(e.id for e in evidence_store.evidence_for(dep.id))
        evidence_ids = list(dict.fromkeys(evidence_ids))

        is_confirmed = (
            root.status == EvidenceStatus.CONFIRMED
            and (root.confidence >= 0.85 or not dependents
                 or root.source.value != "DETERMINISTIC")
        )
        status = (
            EvidenceStatus.CONFIRMED if is_confirmed
            else EvidenceStatus.PROBABLE if root.confidence >= 0.7
            else EvidenceStatus.POTENTIAL
        )
        confidence = root.confidence
        if dependents:
            confidence = min(0.99, confidence + 0.02 * min(len(dependents), 4))
        chains.append(RCAChain(
            id=new_id("RCA"), issue_id=root.id,
            symptom=f"{primary.type} at {primary.location}: {primary.message[:180]}",
            immediate_cause=root.message[:400],
            contributing_factors=list(dict.fromkeys(contributing))[:8],
            root_cause=(
                f"{root.type} in {root.file or root.component or 'project'}"
                + (f" (line {root.line})" if root.line else "")
                + f": {root.message[:220]}"
            ),
            root_cause_finding_id=root.id,
            downstream_impact=impact[:10],
            evidence_ids=evidence_ids[:20],
            confidence=round(confidence, 2),
            status=status,
            verified=bool(is_confirmed and root.reproducible),
        ))
    return chains
