"""Machine-readable root cause / dependency graph."""
from __future__ import annotations

from typing import Dict, List, Optional

from models.domain import (
    Component,
    DependencyEdge,
    EdgeKind,
    Finding,
    GraphEdge,
    GraphNode,
    IssueGroup,
    RCAChain,
    RootCauseGraph,
    TestResult,
    TestStatus,
)


def build_graph(
    findings: List[Finding],
    groups: List[IssueGroup],
    rcas: List[RCAChain],
    components: List[Component],
    dependency_edges: List[DependencyEdge],
    tests: List[TestResult],
) -> RootCauseGraph:
    graph = RootCauseGraph()
    by_id = {f.id: f for f in findings}
    group_by_root = {g.root_issue_id: g for g in groups}

    for f in findings:
        graph.nodes.append(GraphNode(
            id=f.id, label=f"{f.type}: {f.message[:50]}",
            kind="issue",
            severity=getattr(f.severity, "value", str(f.severity)),
            source=getattr(f.source, "value", str(f.source)),
            status=getattr(f.status, "value", str(f.status)),
        ))
    for c in components[:250]:
        if c.type.value in ("module", "class", "api", "service", "database", "config", "entry_point"):
            graph.nodes.append(GraphNode(
                id=c.id, label=f"{c.name} ({c.type.value})", kind="component",
                source="repository", status="analysed",
            ))
    for t in tests:
        if t.status in (TestStatus.FAIL, TestStatus.ERROR):
            graph.nodes.append(GraphNode(
                id=t.id, label=t.name[:70], kind="test",
                source=t.framework, status=t.status.value,
            ))

    for chain in rcas:
        root = chain.root_cause_finding_id
        for dep_id in _dependents(group_by_root.get(root)):
            if dep_id in by_id:
                graph.edges.append(GraphEdge(
                    source=root, target=dep_id, kind=EdgeKind.CAUSES,
                    label="root cause -> symptom",
                ))
    for f in findings:
        if f.metadata.get("test_id") and f.metadata["test_id"] in {t.id for t in tests}:
            graph.edges.append(GraphEdge(
                source=f.metadata["test_id"], target=f.id,
                kind=EdgeKind.REPRODUCES, label="test reproduces finding",
            ))
        if f.file:
            matching = [c for c in components if c.path == f.file and c.type.value == "module"]
            if matching:
                graph.edges.append(GraphEdge(
                    source=f.id, target=matching[0].id,
                    kind=EdgeKind.AFFECTS, label="finding affects component",
                ))
    for edge in dependency_edges[:400]:
        if any(n.id == edge.source for n in graph.nodes) and any(n.id == edge.target for n in graph.nodes):
            graph.edges.append(GraphEdge(
                source=edge.source, target=edge.target,
                kind=EdgeKind.DEPENDS_ON, label=edge.detail,
            ))
    # Multiple RCA chains can reference the same causal link; keep one per triple.
    seen: set[tuple[str, str, EdgeKind]] = set()
    unique: List[GraphEdge] = []
    for edge in graph.edges:
        key = (edge.source, edge.target, edge.kind)
        if key in seen or edge.source == edge.target:
            continue
        seen.add(key)
        unique.append(edge)
    graph.edges = unique
    return graph


def _dependents(group: Optional[IssueGroup]) -> List[str]:
    if not group:
        return []
    return list(group.dependent_issue_ids)


def to_mermaid(graph: RootCauseGraph, limit: int = 80) -> str:
    """Render a Mermaid flowchart for UI/report consumption."""
    lines = ["flowchart TD"]
    style = {
        "issue": ":::issue", "test": ":::test", "component": ":::component",
    }
    for node in graph.nodes[:limit]:
        label = node.label.replace('"', "'").replace("\n", " ")
        lines.append(f'  {node.id}["{label[:60]}"]{style.get(node.kind, "")}')
    arrow = {
        EdgeKind.CAUSES: "-->|causes|",
        EdgeKind.CONTRIBUTES_TO: "-->|contributes_to|",
        EdgeKind.DEPENDS_ON: "-->|depends_on|",
        EdgeKind.AFFECTS: "-->|affects|",
        EdgeKind.REPRODUCES: "-->|reproduces|",
        EdgeKind.VALIDATES: "-->|validates|",
    }
    seen = set()
    for edge in graph.edges:
        key = (edge.source, edge.target, edge.kind)
        if key in seen or edge.source == edge.target:
            continue
        if not any(n.id == edge.source for n in graph.nodes[:limit]):
            continue
        if not any(n.id == edge.target for n in graph.nodes[:limit]):
            continue
        seen.add(key)
        lines.append(f"  {edge.source} {arrow.get(edge.kind, '-->')} {edge.target}")
    lines.extend([
        "  classDef issue fill:#ffe0e0,stroke:#c00;",
        "  classDef test fill:#fff3cd,stroke:#e0a800;",
        "  classDef component fill:#d9edf7,stroke:#31708f;",
    ])
    return "\n".join(lines)
