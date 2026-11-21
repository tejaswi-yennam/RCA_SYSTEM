"""Evaluation framework for detection, RCA, fix and agent performance."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from storage.database import Database, get_db


# Findings that are legitimate evidence even when the expected root category
# differs (downstream symptoms and observed failure types).
CONTEXTUAL_CATEGORIES = {
    "test failure", "runtime error", "logic error", "data error",
    "type error", "integration error", "database error", "api error",
}


@dataclass
class EvaluationResult:
    repository: str
    detection_precision: float = 0.0
    strict_detection_precision: float = 0.0
    detection_recall: float = 0.0
    detection_f1: float = 0.0
    root_cause_identified: bool = False
    root_cause_accuracy: float = 0.0
    false_root_cause_rate: float = 0.0
    cascade_accuracy: float = 0.0
    fix_verification_rate: float = 0.0
    unnecessary_tool_calls: int = 0
    incorrect_hypotheses: int = 0
    evidence_consistency: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {**self.__dict__, "details": self.details}


def _prf(expected: Sequence[str], predicted: Sequence[str]) -> tuple[float, float, float]:
    exp, pred = list(expected), list(predicted)
    if not exp and not pred:
        return 1.0, 1.0, 1.0
    true_positive = 0
    remaining = list(pred)
    for item in exp:
        match = next((p for p in remaining if p.lower() == item.lower()), None)
        if match:
            true_positive += 1
            remaining.remove(match)
    precision = true_positive / len(pred) if pred else 0.0
    recall = true_positive / len(exp) if exp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return round(precision, 3), round(recall, 3), round(f1, 3)


def evaluate_analysis(analysis_id: str, expected: Dict[str, Any],
                      db: Optional[Database] = None) -> EvaluationResult:
    db = db or get_db()
    raw = db.get_report(analysis_id, "json")
    if not raw:
        return EvaluationResult(repository=expected.get("name", "?"),
                                details={"error": "No report available"})
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        return EvaluationResult(repository=expected.get("name", "?"),
                                details={"error": "Report is not JSON"})

    findings = report.get("findings", [])
    chains = report.get("rca_chains", [])
    groups = report.get("issue_groups", [])
    fixes = report.get("fixes", [])
    verifications = report.get("verifications", [])
    summary = report.get("summary", {})
    agent = (summary.get("agent") or {})

    expected_types = [str(x).lower() for x in expected.get("expected_root_cause_types", [])]
    expected_categories = [str(x).lower() for x in expected.get("expected_categories", [])]
    predicted_categories = [str(f.get("category", "")).lower() for f in findings]
    strict_precision, recall, _ = _prf(expected_categories, predicted_categories)
    contextual_expected = list(dict.fromkeys(expected_categories + sorted(CONTEXTUAL_CATEGORIES)))
    precision, _, f1 = _prf(contextual_expected, predicted_categories)

    expected_file = expected.get("expected_file", "")
    root_matches = [
        c for c in chains
        if str(c.get("root_cause", "")).find(Path(expected_file).stem) >= 0
        or any(expected_file in str(f.get("file", "")) for f in findings
               if f.get("id") == c.get("root_cause_finding_id"))
    ]
    type_matches = [
        f for f in findings
        if str(f.get("type", "")).lower() in expected_types
        or str(f.get("category", "")).lower() in expected_categories
    ]
    root_identified = bool(root_matches or type_matches)
    root_accuracy = 1.0 if root_identified else 0.0

    expected_symptoms = int(expected.get("expected_symptom_count", 1))
    cascade_scores: List[float] = []
    for group in groups:
        deps = group.get("dependent_issue_ids", [])
        if deps:
            cascade_scores.append(1.0 if len(deps) >= expected_symptoms else 0.5)
    cascade_accuracy = max(cascade_scores) if cascade_scores else 0.0

    automatable = [f for f in fixes if f.get("automatable")]
    confirmed = [v for v in verifications if v.get("outcome") == "CONFIRMED"]
    fix_rate = len(confirmed) / len(automatable) if automatable else 0.0

    evidence_ids = {e.get("id") for e in report.get("evidence", [])}
    referenced = {
        eid for c in chains for eid in c.get("evidence_ids", [])
    }
    consistency = (len(referenced & evidence_ids) / len(referenced)) if referenced else 1.0

    return EvaluationResult(
        repository=expected.get("name", "?"),
        detection_precision=precision,
        strict_detection_precision=strict_precision,
        detection_recall=recall, detection_f1=f1,
        root_cause_identified=root_identified, root_cause_accuracy=root_accuracy,
        false_root_cause_rate=0.0 if root_identified else 1.0,
        cascade_accuracy=round(cascade_accuracy, 3),
        fix_verification_rate=round(fix_rate, 3),
        unnecessary_tool_calls=max(0, int(agent.get("hypotheses", 0)) - 6),
        incorrect_hypotheses=int(agent.get("rejected", 0) or 0),
        evidence_consistency=round(consistency, 3),
        details={
            "analysis_id": analysis_id,
            "assessment": report.get("assessment"),
            "expected_file": expected_file,
            "matched_root_chains": len(root_matches),
            "type_matches": len(type_matches),
            "findings": len(findings), "chains": len(chains),
            "expected_types": expected_types,
        },
    )


def evaluate_all(sample_root: Path, db: Optional[Database] = None) -> Dict[str, Any]:
    """Evaluate the most recent analysis for each sample repository by name."""
    db = db or get_db()
    results: List[EvaluationResult] = []
    for expected_file in sorted(Path(sample_root).glob("*/expected_rca.json")):
        expected = json.loads(expected_file.read_text(encoding="utf-8"))
        analyses = db.list_analyses()
        match = None
        for analysis in analyses:
            repo = db.get_repository(analysis.repository_id)
            if repo and repo.name == expected.get("name"):
                match = analysis
                break
        if not match:
            results.append(EvaluationResult(
                repository=expected.get("name", "?"),
                details={"error": "No analysis run found; run analysis first"},
            ))
            continue
        results.append(evaluate_analysis(match.id, expected, db))

    def avg(key: str) -> float:
        values = [getattr(r, key) for r in results]
        return round(sum(values) / len(values), 3) if values else 0.0

    aggregate = {
        "repositories": len(results),
        "mean_detection_precision": avg("detection_precision"),
        "mean_strict_detection_precision": avg("strict_detection_precision"),
        "mean_detection_recall": avg("detection_recall"),
        "mean_detection_f1": avg("detection_f1"),
        "root_cause_identification_rate": avg("root_cause_accuracy"),
        "mean_cascade_accuracy": avg("cascade_accuracy"),
        "mean_fix_verification_rate": avg("fix_verification_rate"),
        "mean_evidence_consistency": avg("evidence_consistency"),
        "total_incorrect_hypotheses": sum(r.incorrect_hypotheses for r in results),
        "per_repository": [r.to_dict() for r in results],
    }
    return aggregate
