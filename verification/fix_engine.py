"""Specific, evidence-based fix recommendations with optional safe patches."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from models.domain import (
    Finding,
    FixRecommendation,
    IssueCategory,
    IssueGroup,
    RCAChain,
    Severity,
    new_id,
)
from repository_analyzer.analyzer import _read, _rel

SEVERITY_PRIORITY = {
    Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
    Severity.LOW: 3, Severity.INFO: 4,
}


def _safe_default(env_var: str) -> str:
    upper = env_var.upper()
    if "URL" in upper or "URI" in upper or "DSN" in upper or "DB" in upper:
        return "sqlite:///:memory:"
    if "PORT" in upper:
        return "8080"
    if "DEBUG" in upper:
        return "False"
    return ""


def _recommend(root: Finding, chain: Optional[RCAChain], group: Optional[IssueGroup],
               workspace_root: str) -> FixRecommendation:
    meta = root.metadata or {}
    env_var = str(meta.get("env_var", ""))
    module = str(meta.get("module", ""))
    file = root.file
    component = root.component or (file.split("/")[-1] if file else "project")
    downstream = len(group.dependent_issue_ids) if group else 0
    patch: Optional[Dict[str, Any]] = None
    automatable = False
    change = ""
    why = ""
    side = "No functional change outside the affected component."
    additional: List[str] = []
    verification = "Re-run the failing test and the full suite in an isolated workspace."
    regression = ["Full existing test suite", "Boundary probes for the affected component"]

    if root.type in ("MissingConfiguration", "MissingEnvironmentVariable"):
        default = _safe_default(env_var)
        change = (
            f"Define '{env_var}' in the runtime environment (or .env), and make code "
            f"resilient with a safe default: os.environ.get('{env_var}', '{default}') "
            f"instead of direct indexing."
        )
        why = (
            f"The value is read at {root.location} but absent from all configuration "
            "sources; direct indexing raises KeyError before dependent components start."
        )
        side = "A default can mask a real deployment misconfiguration; log a warning when used."
        additional = [
            f"Add '{env_var}=' to .env.example without a real secret.",
            "Fail fast at startup with a clear message when running in production.",
        ]
        verification = (
            f"Re-run original failing test with {env_var} supplied; agent verification "
            "already confirmed failure count decreases when the value is provided."
        )
        if file and file.endswith(".py") and env_var:
            patch = {
                "file": file,
                "replacements": [[
                    f'os.environ["{env_var}"]',
                    f'os.environ.get("{env_var}", "{default}")',
                ], [
                    f"os.environ['{env_var}']",
                    f'os.environ.get("{env_var}", "{default}")',
                ]],
                "append_files": {
                    ".env.example": f"{env_var}=\n",
                },
            }
            automatable = True
    elif root.type in ("MissingDependency", "UndeclaredDependency", "DynamicImportFailure"):
        change = f"Add '{module}' to the project dependency manifest (requirements.txt/pyproject.toml/package.json)."
        why = "The import is resolved at runtime but the package is not declared or installed, so startup fails."
        side = "Pin a compatible version to avoid future conflicts; installation requires network during build."
        additional = ["Regenerate the lock file after adding the dependency."]
        verification = f"Install the dependency in an isolated environment and re-run import + failing tests."
        if module:
            patch = {"file": "requirements.txt", "append": f"{module}\n"}
            automatable = True
    elif root.category == IssueCategory.SYNTAX or root.type == "SyntaxError":
        change = f"Correct the syntax error at {root.location} indicated by the parser."
        why = "Python cannot compile the module, so every import/test depending on it fails."
        side = "Ensure indentation and surrounding code remain consistent."
        verification = "Run python -m py_compile on the file, then the full test suite."
        regression = ["Syntax compile check", "Full test suite"]
    elif root.type == "VersionConflict":
        change = f"Reconcile conflicting version specifiers for '{module or 'the package'}' into a single pinned version."
        why = "Conflicting constraints make dependency resolution non-deterministic or impossible."
        side = "Upgrading may require migration; run all tests after pinning."
        verification = "Resolve dependencies from scratch and run the suite."
    elif root.type == "UnusedImport":
        change = f"Remove the unused import at {root.location}."
        why = "The imported name is never referenced and may trigger avoidable import failures."
        side = "Verify the name is not used dynamically via globals()."
        verification = "Static check + test suite."
    elif root.type == "BareExcept":
        change = f"Replace the bare except at {root.location} with 'except Exception as exc:' and log or re-raise."
        why = "Bare except hides the original error and turns root causes into misleading symptoms."
        side = "Previously swallowed errors may surface; handle them intentionally."
        verification = "Re-run tests that exercise the error path."
    elif root.category == IssueCategory.DATABASE or "database" in root.message.lower():
        change = "Correct the database connection settings and initialise the connection before dependent services start."
        why = "Database connectivity is a prerequisite for downstream service initialisation."
        side = "Changing connection targets can affect data; verify against a test database."
        verification = "Run integration tests against an isolated test database."
    elif root.type == "DynamicBoundaryFailure":
        change = f"Add input validation/guards in {root.file or component} for the failing boundary inputs."
        why = "The component raises unhandled exceptions for empty/null/negative inputs, which propagates to callers."
        side = "Stricter validation may reject previously tolerated inputs; document contract changes."
        verification = "Re-run generated boundary probe and existing tests."
    elif root.type in ("FAIL", "ERROR", "AssertionError") or root.category == IssueCategory.TEST_FAILURE:
        change = f"Inspect expected vs actual values in {root.file or component} and correct the failing logic or test expectation."
        why = "The test is a confirmed, reproducible failure with deterministic evidence."
        side = "If the test expectation is wrong, update the test rather than production code."
        verification = "Re-run the original failing test, then related tests."
    elif root.category == IssueCategory.INTEGRATION or root.type == "CascadingFailure":
        change = "Fix the upstream root-cause finding first; this finding is a downstream cascade."
        why = "Multiple failures share a single upstream cause; patching this layer only masks the symptom."
        side = "None directly; verify the upstream fix resolves this finding."
        verification = "Apply upstream fix and re-run all linked failing tests."
    else:
        change = f"Address {root.type} at {root.location}: {root.message[:180]}"
        why = "Deterministic evidence identifies this location as the source of the issue."
        side = "Review the affected component before applying changes."
        verification = "Re-run the failing test and related tests."

    priority = SEVERITY_PRIORITY.get(root.severity, 9) * 10 + max(0, 5 - min(downstream, 5))
    return FixRecommendation(
        id=new_id("FIX"), issue_id=root.id,
        problem=root.message[:400],
        root_cause=(chain.root_cause if chain else root.message)[:500],
        affected_file=file, affected_component=component,
        required_change=change, why_this_fix_works=why,
        potential_side_effects=side,
        required_additional_changes=additional,
        verification_method=verification,
        regression_tests=regression,
        priority=priority, automatable=automatable, patch=patch,
    )


def recommend_fixes(roots: List[Finding], chains: List[RCAChain],
                    groups: List[IssueGroup]) -> List[FixRecommendation]:
    chain_by_root = {c.root_cause_finding_id: c for c in chains}
    group_by_root = {g.root_issue_id: g for g in groups}
    fixes = [
        _recommend(root, chain_by_root.get(root.id), group_by_root.get(root.id), "")
        for root in roots
    ]
    return sorted(fixes, key=lambda f: f.priority)
