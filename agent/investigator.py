"""Autonomous AI agent engine (hybrid, evidence-driven investigation)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings
from evidence.store import EvidenceStore
from models.domain import (
    AgentDecision,
    EdgeKind,
    EvidenceStatus,
    Finding,
    FindingSource,
    Hypothesis,
    IssueCategory,
    Severity,
    TestResult,
    TestStatus,
    new_id,
)
from repository_analyzer.analyzer import RepositoryUnderstanding, _read, _rel
from sandbox.executor import execute

from .llm import get_provider, parse_json_response

ROOT_TYPES = {
    "SyntaxError", "MissingDependency", "UndeclaredDependency",
    "MissingConfiguration", "InvalidConfiguration", "VersionConflict",
    "DynamicImportFailure", "PotentialSecret", "Use of eval()", "Use of exec()",
    "ExposedSecret", "Python syntax error",
}
SYMPTOM_TYPES = {"FAIL", "ERROR", "DynamicBoundaryFailure", "DynamicImportFailure"}

SYSTEM_PROMPT = """You are a root-cause analyst. You receive structured evidence from deterministic
static analysis, dependency analysis and test execution — never raw guesses.
Return ONLY JSON:
{
  "hypotheses": [
    {"statement": "...", "root_cause_finding_ids": ["DET-..."],
     "target_finding_ids": ["TESTF-..."], "confidence": 0.0-1.0,
     "reasoning": "..."}
  ],
  "notes": "short summary"
}
Rules:
- Do not invent finding IDs. Only use IDs present in the context.
- A root cause must explain one or more downstream failures.
- Distinguish symptoms from causes. If evidence is insufficient, say so in notes.
- Be concise; no markdown outside JSON."""

_VERIFY_TEMPLATE = r'''
import json
"""Verify that supplying a missing config value changes test outcomes."""
'''


@dataclass
class AgentInvestigation:
    hypotheses: List[Hypothesis] = field(default_factory=list)
    decisions: List[AgentDecision] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    llm_used: bool = False
    llm_error: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)


def _decision(iteration: int, action: str, rationale: str,
              tools: Optional[List[str]] = None, result: str = "") -> AgentDecision:
    return AgentDecision(
        id=new_id("DEC"), iteration=iteration, action=action,
        rationale=rationale, tool_calls=tools or [], result=result[:1500],
    )


def _root_candidates(findings: List[Finding]) -> List[Finding]:
    out = [f for f in findings
           if f.type in ROOT_TYPES or f.category in {
               IssueCategory.SYNTAX, IssueCategory.DEPENDENCY,
               IssueCategory.CONFIGURATION, IssueCategory.SECURITY,
           }]
    return sorted(out, key=lambda f: -f.confidence)[:16]


def _symptoms(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings
            if f.type in SYMPTOM_TYPES or f.status == TestStatus.FAIL.value
            or f.category in {IssueCategory.TEST_FAILURE, IssueCategory.RUNTIME,
                              IssueCategory.LOGIC}]


def _link(root: Finding, symptom: Finding, understanding: RepositoryUnderstanding) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    haystack = f"{symptom.message} {symptom.stack_trace} {symptom.file} {' '.join(symptom.evidence)}".lower()
    if root.file and root.file == symptom.file:
        reasons.append(f"same file {root.file}")
    root_config = str(root.metadata.get("env_var", "")).lower()
    if root_config and root_config in haystack:
        reasons.append(f"config key '{root_config}' appears in failure")
    root_module = str(root.metadata.get("module", "")).lower()
    if root_module and root_module in haystack.replace("_", ""):
        reasons.append(f"module '{root_module}' appears in failure")
    root_file_stem = Path(root.file).stem.lower() if root.file else ""
    if root_file_stem and root_file_stem in haystack:
        reasons.append(f"module '{root_file_stem}' referenced by failure")
    for edge in understanding.edges:
        if (edge.kind == EdgeKind.DEPENDS_ON and symptom.file
                and edge.source == symptom.file and edge.target == root.file):
            reasons.append("dependency graph links symptom to root file")
            break
    return bool(reasons), reasons


def _verify(root: Finding, linked_symptoms: List[Finding], workspace: Path,
            baseline_failures: int) -> Tuple[bool, str, float]:
    """Deterministically test a hypothesis where possible."""
    if not linked_symptoms:
        return False, "No linked symptom to verify against", 0.0

    # Missing configuration: rerun tests with a benign value supplied.
    if root.type == "MissingConfiguration":
        env_var = root.metadata.get("env_var")
        if env_var:
            import os

            result = execute(["python", "-m", "pytest", "-q", "--no-header"],
                             cwd=workspace, timeout=settings.command_timeout,
                             env_extra={
                                 str(env_var): "sqlite:///:memory:",
                                 "PYTHONPATH": str(workspace) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                             })
            import re

            m = re.search(r"(\d+)\s+failed", result.stdout + result.stderr)
            new_failures = int(m.group(1)) if m else 0
            if baseline_failures > 0 and new_failures < baseline_failures:
                return True, (
                    f"Supplying {env_var} reduced failures from {baseline_failures} to {new_failures}"
                ), 0.97
            return False, (
                f"Supplying {env_var} did not reduce failures "
                f"({baseline_failures} -> {new_failures})"
            ), 0.55

    # Import failures: attempt the import inside the sandbox.
    if root.type in ("MissingDependency", "DynamicImportFailure"):
        module = root.metadata.get("module") or ""
        if module:
            result = execute(
                ["python", "-c", f"import importlib.util; assert importlib.util.find_spec({module!r}) is not None"],
                cwd=workspace, timeout=20,
            )
            if result.returncode != 0:
                return True, f"import of '{module}' still fails in sandbox", 0.95

    # Syntax/config parse failures are already deterministic facts.
    if root.status == EvidenceStatus.CONFIRMED and root.confidence >= 0.9:
        return True, f"Deterministic evidence already confirmed ({root.type})", root.confidence

    return False, "No deterministic verification procedure available", min(root.confidence, 0.7)


def _context(findings: List[Finding], tests: List[TestResult],
             understanding: RepositoryUnderstanding) -> str:
    lines = ["FINDINGS:"]
    for f in findings[:40]:
        lines.append(
            f"- {f.id} | {f.type} | {f.category.value} | {f.severity.value} | "
            f"{f.location} | {f.message[:220]}"
        )
    lines.append("FAILED TESTS:")
    for t in tests:
        if t.status in (TestStatus.FAIL, TestStatus.ERROR):
            lines.append(f"- {t.name} | {t.status.value} | {t.error_message[:300]}")
    lines.append("DEPENDENCY EDGES:")
    for e in understanding.edges[:40]:
        lines.append(f"- {e.source} -> {e.target} ({e.kind.value})")
    return "\n".join(lines)[:14000]


def investigate(
    workspace: Path,
    understanding: RepositoryUnderstanding,
    findings: List[Finding],
    tests: List[TestResult],
    evidence_store: EvidenceStore,
) -> AgentInvestigation:
    report = AgentInvestigation()
    roots = _root_candidates(findings)
    symptoms = _symptoms(findings)
    baseline_failures = sum(
        1 for t in tests if t.status in (TestStatus.FAIL, TestStatus.ERROR)
    ) or sum(
        1 for f in findings if f.type in ("FAIL", "ERROR")
    )

    report.decisions.append(_decision(
        1, "consume_structured_evidence",
        f"Received {len(findings)} deterministic findings, {len(tests)} test results; "
        f"selected {len(roots)} root candidates and {len(symptoms)} symptoms.",
        ["evidence_store.all"], f"baseline_failures={baseline_failures}",
    ))

    # ---- Hypothesis generation (local rule engine) ----
    hypotheses: List[Hypothesis] = []
    for root in roots:
        linked: List[Finding] = []
        reasons: List[str] = []
        for symptom in symptoms:
            ok, why = _link(root, symptom, understanding)
            if ok:
                linked.append(symptom)
                reasons.extend(why)
        hypothesis = Hypothesis(
            id=new_id("HYP"),
            statement=f"{root.type} at {root.location} is a likely root cause: {root.message[:160]}",
            confidence=root.confidence if linked else min(root.confidence, 0.6),
            supporting_evidence=[
                f"Finding {root.id}: {root.message[:180]}",
                *root.evidence[:3],
                *[f"Linked {s.id}: {s.message[:140]}" for s in linked[:5]],
                *reasons[:3],
            ],
            investigation_notes=[f"Selected via deterministic evidence source {root.source.value}"],
            root_cause_finding_ids=[root.id],
            target_finding_ids=[s.id for s in linked],
        )
        hypotheses.append(hypothesis)

    report.decisions.append(_decision(
        2, "generate_hypotheses",
        f"Generated {len(hypotheses)} evidence-linked hypotheses without reading unrelated files.",
        ["_root_candidates", "_link"],
        "; ".join(h.statement[:90] for h in hypotheses[:4]),
    ))

    # ---- LLM enrichment (optional) ----
    provider = get_provider()
    if settings.llm_configured:
        response = provider.complete(SYSTEM_PROMPT, _context(findings, tests, understanding))
        report.llm_used = True
        report.llm_error = response.error
        parsed = parse_json_response(response.text)
        if isinstance(parsed, dict):
            known = {f.id for f in findings}
            for item in (parsed.get("hypotheses") or [])[:8]:
                if not isinstance(item, dict):
                    continue
                rids = [x for x in item.get("root_cause_finding_ids", []) if x in known]
                tids = [x for x in item.get("target_finding_ids", []) if x in known]
                if not rids:
                    continue
                hypotheses.append(Hypothesis(
                    id=new_id("HYP"), statement=str(item.get("statement", ""))[:400],
                    confidence=float(item.get("confidence") or 0.6),
                    supporting_evidence=[str(item.get("reasoning", ""))[:500]],
                    investigation_notes=[f"LLM enrichment via {provider.name}"],
                    root_cause_finding_ids=rids, target_finding_ids=tids,
                ))
        report.decisions.append(_decision(
            3, "llm_enrichment",
            "Asked configured LLM to correlate deterministic evidence.",
            [f"{provider.name}.complete"],
            response.error or f"received {len(response.text)} chars",
        ))

    # ---- Investigation / verification loop ----
    for iteration, hypothesis in enumerate(hypotheses[:settings.max_agent_iterations], start=4):
        root_id = hypothesis.root_cause_finding_ids[0] if hypothesis.root_cause_finding_ids else ""
        root = next((f for f in findings if f.id == root_id), None)
        if root is None:
            hypothesis.status = "REJECTED"
            continue

        # Targeted file inspection (only suspicious files, never whole repo rereads).
        inspected = ""
        if root.file and (workspace / root.file).exists():
            inspected = _read(workspace / root.file, limit=4000)
            hypothesis.investigation_notes.append(
                f"Inspected {root.file} ({len(inspected)} chars) at line {root.line}"
            )
        linked = [f for f in symptoms if f.id in hypothesis.target_finding_ids]
        verified, detail, confidence = _verify(root, linked, workspace, baseline_failures)

        hypothesis.confidence = round(max(hypothesis.confidence, confidence) if verified
                                      else min(hypothesis.confidence, confidence), 2)
        if verified and hypothesis.confidence >= settings.confidence_threshold:
            hypothesis.status = "CONFIRMED"
        elif linked and hypothesis.confidence >= settings.confidence_threshold * 0.8:
            hypothesis.status = "SUPPORTED"
        else:
            hypothesis.status = "OPEN" if linked else "REJECTED"
        hypothesis.investigation_notes.append(detail)

        report.decisions.append(_decision(
            iteration, "test_hypothesis",
            f"Verified hypothesis for {root.type}: {detail}",
            [f"inspect:{root.file}"] if inspected else [],
            f"status={hypothesis.status}, confidence={hypothesis.confidence}",
        ))
        evidence_store.from_hypothesis(hypothesis)

    report.hypotheses = hypotheses

    # ---- Agent-generated findings for confirmed cascades ----
    # Only the strongest causal hypothesis becomes an agent finding; weaker
    # candidates remain as hypotheses/evidence so they do not masquerade as
    # independent root causes.
    finding_by_id = {f.id: f for f in findings}

    def _rank(h: Hypothesis) -> tuple[int, int, float]:
        root = finding_by_id.get(h.root_cause_finding_ids[0]) if h.root_cause_finding_ids else None
        true_root = bool(root and root.category in {
            IssueCategory.SYNTAX, IssueCategory.DEPENDENCY,
            IssueCategory.CONFIGURATION, IssueCategory.SECURITY,
        } and root.type != "DynamicImportFailure")
        return (int(true_root), len(h.target_finding_ids), h.confidence)

    ranked = sorted(
        [h for h in hypotheses if h.status in ("CONFIRMED", "SUPPORTED")],
        key=_rank, reverse=True,
    )
    for hypothesis in ranked[:1]:
        if hypothesis.status not in ("CONFIRMED", "SUPPORTED"):
            continue
        root_id = hypothesis.root_cause_finding_ids[0]
        root = next((f for f in findings if f.id == root_id), None)
        if root is None:
            continue
        linked = hypothesis.target_finding_ids
        if len(linked) >= 1:
            report.findings.append(Finding(
                id=new_id("AGT"), type="CascadingFailure",
                severity=Severity.HIGH if len(linked) > 1 else Severity.MEDIUM,
                category=IssueCategory.INTEGRATION,
                file=root.file, line=root.line, component=root.component,
                message=(f"{len(linked)} downstream failure(s) traced to {root.type} "
                         f"at {root.location}"),
                evidence=[
                    f"Agent hypothesis {hypothesis.id} status={hypothesis.status}",
                    f"Deterministic root-cause finding {root.id}",
                    *[f"Cascade: {fid}" for fid in linked[:8]],
                    *hypothesis.investigation_notes[-2:],
                ],
                reproducible=root.reproducible,
                source=FindingSource.AGENT,
                status=(EvidenceStatus.CONFIRMED if hypothesis.status == "CONFIRMED"
                        else EvidenceStatus.PROBABLE),
                confidence=hypothesis.confidence,
                metadata={"root_finding_id": root.id, "linked_findings": linked},
            ))

    report.summary = {
        "hypotheses": len(hypotheses),
        "confirmed": sum(1 for h in hypotheses if h.status == "CONFIRMED"),
        "supported": sum(1 for h in hypotheses if h.status == "SUPPORTED"),
        "rejected": sum(1 for h in hypotheses if h.status == "REJECTED"),
        "agent_findings": len(report.findings),
        "llm_used": report.llm_used,
        "llm_error": report.llm_error,
    }
    report.decisions.append(_decision(
        99, "finalise", f"Agent investigation complete: {report.summary}",
        [], json.dumps(report.summary)[:800],
    ))
    return report
