"""Issue correlation: grouping symptoms, cascades, duplicates and independents."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from models.domain import (
    Finding,
    FindingSource,
    IssueCategory,
    IssueGroup,
    Severity,
    new_id,
)
from repository_analyzer.analyzer import RepositoryUnderstanding

ROOT_CATEGORIES = {
    IssueCategory.SYNTAX, IssueCategory.DEPENDENCY, IssueCategory.CONFIGURATION,
    IssueCategory.SECURITY, IssueCategory.DATABASE, IssueCategory.BUILD,
    IssueCategory.DEPLOYMENT,
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def _similar(a: Finding, b: Finding) -> bool:
    na, nb = _norm(a.message), _norm(b.message)
    if a.file == b.file and na == nb and na:
        return True
    if a.type == b.type and a.file == b.file and a.line == b.line:
        return True
    if not na or not nb:
        return False
    # Two distinct test names are never duplicates even if their failure text
    # is nearly identical.
    if a.category == IssueCategory.TEST_FAILURE and b.category == IssueCategory.TEST_FAILURE:
        if na != nb:
            return False
    sa, sb = set(na.split()), set(nb.split())
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    same_place = (not a.file or not b.file or a.file == b.file)
    return overlap > 0.75 and a.category == b.category and same_place


def _may_cause(root: Finding, other: Finding, understanding: RepositoryUnderstanding) -> bool:
    if root.id == other.id:
        return False
    hay = f"{other.message} {other.stack_trace} {' '.join(other.evidence)} {other.file}".lower()
    cfg = str(root.metadata.get("env_var", "")).lower()
    if cfg and cfg in hay:
        return True
    mod = str(root.metadata.get("module", "")).lower()
    if mod and mod.replace("_", "") in hay.replace("_", ""):
        return True
    if root.file and root.file == other.file:
        return True
    stem = Path(root.file).stem.lower() if root.file else ""
    if stem and stem in hay:
        return True
    for edge in understanding.edges:
        if edge.source == other.file and edge.target == root.file:
            return True
    return False


def correlate(findings: List[Finding], understanding: RepositoryUnderstanding,
              agent_links: Optional[Dict[str, List[str]]] = None) -> Tuple[List[IssueGroup], Dict[str, str]]:
    """Return issue groups and a finding_id -> group_id map."""
    agent_links = agent_links or {}
    groups: List[IssueGroup] = []
    assignment: Dict[str, str] = {}
    handled: Set[str] = set()

    # 1. Exact/near duplicates.
    for i, a in enumerate(findings):
        if a.id in handled:
            continue
        dups = [b.id for b in findings[i + 1:]
                if b.id not in handled and _similar(a, b)]
        for did in dups:
            handled.add(did)
        if dups:
            g = IssueGroup(
                id=new_id("GRP"), root_issue_id=a.id,
                duplicate_issue_ids=dups, label=f"Duplicate findings: {a.type}",
                confidence=0.9,
            )
            groups.append(g)
            for fid in [a.id, *dups]:
                assignment[fid] = g.id
            # The canonical finding stays eligible as a causal root; only its
            # duplicates are removed from further consideration.

    # 2. Agent-confirmed causal links first (strongest evidence).
    roots: List[Finding] = [
        f for f in findings
        if f.id not in handled
        and (f.category in ROOT_CATEGORIES or f.type in {
            "MissingConfiguration", "SyntaxError", "MissingDependency",
            "DynamicImportFailure", "CascadingFailure"})
    ]
    # Dynamic import failures are usually symptoms of a config/syntax/dependency
    # root; only treat them as roots when no stronger candidate exists.
    stronger = [f for f in roots if f.type != "DynamicImportFailure"]
    if stronger:
        roots = stronger
    roots.sort(key=lambda f: (-f.confidence, -len(agent_links.get(f.id, []))))
    for root in roots:
        if root.id in handled:
            continue
        # Agent cascade findings are evidence of a link, not independent roots.
        if root.type == "CascadingFailure":
            continue
        dependents: Set[str] = {d for d in agent_links.get(root.id, []) if d != root.id}
        for other in findings:
            if other.id in handled or other.id == root.id:
                continue
            if other.type == "CascadingFailure":
                continue
            if other.category in ROOT_CATEGORIES and other.category == root.category:
                continue
            if _may_cause(root, other, understanding) and other.id != root.id:
                dependents.add(other.id)
        # CascadingFailure agent findings already point at a root; skip them as roots.
        if root.type == "CascadingFailure" and not dependents:
            continue
        if not dependents:
            continue
        for did in dependents:
            handled.add(did)
        g = IssueGroup(
            id=new_id("GRP"), root_issue_id=root.id,
            dependent_issue_ids=sorted(dependents),
            label=f"{root.type} -> {len(dependents)} downstream failure(s)",
            confidence=max(root.confidence, 0.7),
        )
        groups.append(g)
        for fid in [root.id, *dependents]:
            assignment[fid] = g.id
        handled.add(root.id)

    # 3. Remaining findings are independent issues (or unlinked root candidates).
    for f in findings:
        if f.id in handled or f.id in assignment or f.type == "CascadingFailure":
            continue
        g = IssueGroup(
            id=new_id("GRP"), root_issue_id=f.id, independent=True,
            label=f"{f.type} at {f.location}", confidence=f.confidence,
        )
        groups.append(g)
        assignment[f.id] = g.id
        handled.add(f.id)

    # Ensure every finding is assigned.
    for f in findings:
        if f.type == "CascadingFailure":
            continue
        assignment.setdefault(f.id, assignment.get(f.id, ""))
        if not assignment.get(f.id):
            g = IssueGroup(id=new_id("GRP"), root_issue_id=f.id,
                           independent=True, label=f.type, confidence=f.confidence)
            groups.append(g)
            assignment[f.id] = g.id
    return groups, assignment
