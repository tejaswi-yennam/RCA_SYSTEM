"""Final RCA report generation (Markdown + structured JSON)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from graph.root_cause_graph import RootCauseGraph, to_mermaid
from models.domain import (
    AnalysisRun,
    Component,
    EvidenceStatus,
    Finding,
    FixRecommendation,
    RegressionResult,
    RepositoryProfile,
    RCAChain,
    Severity,
    TestResult,
    TestStatus,
    VerificationResult,
    IssueGroup,
)

SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?[^'\"\s,;]{8,})",
    re.I,
)


def redact(text: str) -> str:
    return SECRET_RE.sub(lambda m: "[REDACTED]" if m.group(0) else m.group(0), str(text))


def _v(value: Any) -> str:
    """Return an enum's value, tolerating plain strings from deserialised data."""
    return str(getattr(value, "value", value))


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
    return "\n".join(lines)


def _health(summary: Dict[str, Any]) -> str:
    if summary.get("critical", 0):
        return "CRITICAL FAILURE"
    if summary.get("high", 0) or summary.get("failed_tests", 0):
        return "NOT READY"
    if summary.get("medium", 0) or summary.get("potential_issues", 0):
        return "READY WITH WARNINGS"
    return "READY"


def build_report(
    analysis: AnalysisRun,
    profile: RepositoryProfile,
    components: List[Component],
    tests: List[TestResult],
    findings: List[Finding],
    groups: List[IssueGroup],
    rcas: List[RCAChain],
    graph: RootCauseGraph,
    fixes: List[FixRecommendation],
    verifications: List[VerificationResult],
    regressions: List[RegressionResult],
    evidence: List[Dict[str, Any]],
    missing_coverage: List[str],
) -> Dict[str, Any]:
    passed = sum(1 for t in tests if t.status == TestStatus.PASS)
    failed = sum(1 for t in tests if t.status in (TestStatus.FAIL, TestStatus.ERROR))
    critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    high = sum(1 for f in findings if f.severity == Severity.HIGH)
    confirmed_roots = sum(1 for r in rcas if r.status == EvidenceStatus.CONFIRMED)
    summary = {
        "total_components": len(components),
        "total_tests": len(tests),
        "passed_tests": passed,
        "failed_tests": failed,
        "total_issues": len(findings),
        "critical": critical,
        "high": high,
        "root_causes": len(rcas),
        "confirmed_root_causes": confirmed_roots,
        "potential_issues": sum(1 for f in findings if f.status == EvidenceStatus.POTENTIAL),
        "capabilities_available": sum(1 for c in analysis.capabilities if _v(c.status) == "OK"),
        "capabilities_failed": sum(1 for c in analysis.capabilities if _v(c.status) == "FAILED"),
    }
    assessment = _health(summary)

    findings_by_id = {f.id: f for f in findings}
    verification_by_fix = {v.fix_id: v for v in verifications}
    regression_by_fix = {r.fix_id: r for r in regressions}

    md: List[str] = []
    md.append("# Autonomous Repository Testing & RCA Report")
    md.append(f"\n**Analysis ID:** {analysis.id}  ")
    md.append(f"**Generated:** {analysis.finished_at or ''}  ")
    md.append(f"**Final assessment:** {assessment}\n")

    md.append("## 1. Executive Summary\n")
    md.append(_md_table(["Metric", "Value"], [
        ["Overall project health", assessment],
        ["Components analysed", summary["total_components"]],
        ["Tests executed", summary["total_tests"]],
        ["Tests passed", summary["passed_tests"]],
        ["Tests failed/errored", summary["failed_tests"]],
        ["Total issues", summary["total_issues"]],
        ["Critical issues", summary["critical"]],
        ["High-severity issues", summary["high"]],
        ["Root causes identified", summary["root_causes"]],
        ["Confirmed root causes", summary["confirmed_root_causes"]],
        ["Analysis capabilities OK / failed", f"{summary['capabilities_available']} / {summary['capabilities_failed']}"],
    ]))
    md.append("")

    md.append("## 2. Repository Analysis\n")
    md.append(f"- Project types: {', '.join(profile.project_types) or 'unknown'}")
    md.append(f"- Languages: {', '.join(profile.languages) or 'unknown'}")
    md.append(f"- Frameworks: {', '.join(profile.frameworks) or 'not detected'}")
    md.append(f"- Package managers: {', '.join(profile.package_managers) or 'not detected'}")
    md.append(f"- Test frameworks: {', '.join(profile.test_frameworks) or 'none detected'}")
    md.append(f"- Entry points: {', '.join(profile.entry_points) or 'not detected'}")
    md.append(f"- External services: {', '.join(profile.external_services) or 'not detected'}")
    md.append(f"- Database references: {', '.join(profile.database_connections) or 'not detected'}")
    md.append(f"- Files: {profile.total_files}, lines: {profile.total_lines}\n")
    if profile.dependencies:
        dep_rows = [[name, ", ".join(deps[:20])] for name, deps in profile.dependencies.items()]
        md.append(_md_table(["Manifest", "Declared dependencies"], dep_rows))
        md.append("")
    comp_rows = [[c.name, _v(c.type), c.path, c.language] for c in components[:80]]
    if comp_rows:
        md.append("### Components\n")
        md.append(_md_table(["Component", "Type", "Path", "Language"], comp_rows))
        md.append("")

    md.append("## 3. Testing Summary\n")
    md.append(_md_table(
        ["Test", "Component", "Status", "Error"],
        [[t.name, t.component, _v(t.status), redact(t.error_message)[:160]] for t in tests[:200]],
    ))
    md.append("")

    md.append("## 4. Issue Summary\n")
    chain_for_finding: Dict[str, RCAChain] = {}
    for chain in rcas:
        chain_for_finding[chain.issue_id] = chain
        if chain.root_cause_finding_id:
            chain_for_finding.setdefault(chain.root_cause_finding_id, chain)
    issue_rows: List[List[str]] = []
    for f in findings[:200]:
        chain = chain_for_finding.get(f.id)
        root_type = (
            findings_by_id[chain.root_cause_finding_id].type
            if chain and chain.root_cause_finding_id in findings_by_id else "-"
        )
        issue_rows.append([f.id, f.location, f.type, _v(f.severity),
                           _v(f.status), _v(f.source), root_type])
    md.append(_md_table(
        ["ID", "Component", "Error Type", "Severity", "Status", "Source", "Root Cause"],
        issue_rows,
    ))
    md.append("")

    md.append("## 5. Detailed RCA\n")
    for chain in rcas:
        root = findings_by_id.get(chain.root_cause_finding_id)
        md.append(f"### {chain.id} — {chain.root_cause[:120]}")
        md.append(f"- **Issue ID:** {chain.issue_id}")
        md.append(f"- **Component:** {getattr(root, 'component', '') or (root.file if root else '')}")
        md.append(f"- **File / line:** {redact(getattr(root, 'location', ''))}")
        md.append(f"- **Error type:** {getattr(root, 'type', 'Unknown')}")
        md.append(f"- **Severity:** {_v(getattr(root, 'severity', Severity.MEDIUM)) if root else 'MEDIUM'}")
        md.append(f"- **Confidence:** {chain.confidence} ({_v(chain.status)})")
        md.append(f"- **Verified:** {chain.verified}")
        md.append(f"- **Symptom:** {redact(chain.symptom)}")
        md.append(f"- **Immediate cause:** {redact(chain.immediate_cause)}")
        md.append(f"- **Contributing factors:** {'; '.join(redact(x) for x in chain.contributing_factors) or 'none'}")
        md.append(f"- **Root cause:** {redact(chain.root_cause)}")
        md.append(f"- **Impact:** {'; '.join(redact(x) for x in chain.downstream_impact) or 'none recorded'}")
        md.append(f"- **Evidence:** {', '.join(chain.evidence_ids) or 'see findings'}")
        if root:
            md.append(f"- **Expected behaviour:** operation completes without {root.type}")
            md.append(f"- **Observed behaviour:** {redact(root.message)}")
        md.append("")

    md.append("## 6. Root Cause Graph\n")
    md.append("```mermaid\n" + to_mermaid(graph) + "\n```\n")
    md.append(_md_table(
        ["From", "To", "Relationship"],
        [[e.source, e.target, _v(e.kind)] for e in graph.edges[:120]],
    ))
    md.append("")

    md.append("## 7. Missing Test Coverage\n")
    for item in missing_coverage or ["No obvious gaps detected."]:
        md.append(f"- {item}")
    md.append("")

    md.append("## 8. Recommended Fix Order\n")
    for idx, fix in enumerate(fixes, start=1):
        ver = verification_by_fix.get(fix.id)
        reg = regression_by_fix.get(fix.id)
        md.append(f"### {idx}. {fix.id} (priority {fix.priority}) — {fix.affected_file or fix.affected_component}")
        md.append(f"- **Problem:** {redact(fix.problem)}")
        md.append(f"- **Root cause:** {redact(fix.root_cause)}")
        md.append(f"- **Affected component:** {fix.affected_component}")
        md.append(f"- **Required change:** {redact(fix.required_change)}")
        md.append(f"- **Why it works:** {redact(fix.why_this_fix_works)}")
        md.append(f"- **Side effects:** {redact(fix.potential_side_effects)}")
        md.append(f"- **Additional changes:** {'; '.join(fix.required_additional_changes) or 'none'}")
        md.append(f"- **Verification:** {redact(fix.verification_method)}")
        md.append(f"- **Regression tests:** {', '.join(fix.regression_tests)}")
        md.append(f"- **Verification result:** {ver.outcome if ver else 'not run'}"
                  + (f" — {redact(ver.details)}" if ver else ""))
        if reg:
            md.append(f"- **Regression result:** {redact(reg.details)}")
        md.append("")

    md.append("## 9. Evidence Record\n")
    md.append(_md_table(
        ["Finding", "Evidence items", "Confidence", "Status"],
        [[redact(str(e.get("finding", "")))[:90],
          redact("; ".join(e.get("items", [])))[:180],
          e.get("confidence"), e.get("status")]
         for e in evidence[:80]],
    ))
    md.append("")

    md.append("## 10. Capability Status\n")
    md.append(_md_table(
        ["Capability", "Status", "Detail"],
        [[c.name, _v(c.status), redact(c.detail)[:160]] for c in analysis.capabilities],
    ))
    md.append("")

    md.append("## 11. Final Assessment\n")
    md.append(f"**{assessment}** — ")
    if assessment in ("NOT READY", "CRITICAL FAILURE"):
        md.append("confirmed failures or high-severity root causes remain unresolved. "
                  "Apply fixes in priority order and re-run the analysis.")
    elif assessment == "READY WITH WARNINGS":
        md.append("no critical/high failures, but warnings or unverified hypotheses remain. "
                  "Review the potential issues before release.")
    else:
        md.append("no blocking issues detected by the available analysis capabilities.")
    md.append("\n*All code execution was performed in an isolated sandbox copy. "
              "Secret-like values are redacted.*\n")

    markdown = redact("\n".join(md))
    structured = {
        "analysis_id": analysis.id,
        "assessment": assessment,
        "summary": summary,
        "repository": profile.to_dict(),
        "components": [c.to_dict() for c in components],
        "tests": [t.to_dict() for t in tests],
        "findings": [f.to_dict() for f in findings],
        "issue_groups": [g.to_dict() for g in groups],
        "rca_chains": [c.to_dict() for c in rcas],
        "root_cause_graph": graph.to_dict(),
        "mermaid": to_mermaid(graph),
        "fixes": [f.to_dict() for f in fixes],
        "verifications": [v.to_dict() for v in verifications],
        "regressions": [r.to_dict() for r in regressions],
        "evidence": evidence,
        "missing_test_coverage": missing_coverage,
        "capabilities": [c.to_dict() for c in analysis.capabilities],
        "report_markdown": markdown,
    }
    return {"markdown": markdown, "json": json.dumps(structured, indent=2, default=str),
            "structured": structured, "assessment": assessment, "summary": summary}
