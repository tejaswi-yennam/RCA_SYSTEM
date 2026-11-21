"""Deterministic static analysis (no LLM required)."""
from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from models.domain import (
    EvidenceStatus,
    Finding,
    FindingSource,
    IssueCategory,
    Severity,
    new_id,
)
from repository_analyzer.analyzer import RepositoryUnderstanding, _read, _rel, _iter_files

STDLIB = set(getattr(sys, "stdlib_module_names", ())) | set(dir(builtins)) | {
    "flask", "fastapi", "pytest", "numpy", "pandas", "requests", "django",
}

SECRET_PATTERNS = [
    (re.compile(r"(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}['\"]", re.I),
     "Hard-coded credential-like literal"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key literal"),
    (re.compile(r"-----BEGIN (RSA|OPENSSH|PRIVATE) KEY-----"), "Private key literal"),
]

UNSAFE_PATTERNS = [
    (re.compile(r"\beval\s*\("), "Use of eval()", IssueCategory.SECURITY, Severity.HIGH),
    (re.compile(r"\bexec\s*\("), "Use of exec()", IssueCategory.SECURITY, Severity.HIGH),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "Shell command with shell=True",
     IssueCategory.SECURITY, Severity.HIGH),
    (re.compile(r"\bpickle\.loads?\s*\("), "Unsafe deserialisation via pickle",
     IssueCategory.SECURITY, Severity.MEDIUM),
    (re.compile(r"DEBUG\s*=\s*True|debug\s*[:=]\s*true", re.I), "Debug mode enabled",
     IssueCategory.CONFIGURATION, Severity.LOW),
]


def _finding(**kwargs: Any) -> Finding:
    kwargs.setdefault("source", FindingSource.DETERMINISTIC)
    kwargs.setdefault("status", EvidenceStatus.POTENTIAL)
    kwargs.setdefault("id", new_id("DET"))
    return Finding(**kwargs)


def _severity_for_syntax(msg: str) -> Severity:
    return Severity.HIGH if "SyntaxError" in msg or "IndentationError" in msg else Severity.MEDIUM


def analyze_python_syntax(root: Path, understanding: RepositoryUnderstanding) -> List[Finding]:
    findings: List[Finding] = []
    for err in understanding.parse_errors:
        findings.append(_finding(
            type="SyntaxError", severity=Severity.HIGH, category=IssueCategory.SYNTAX,
            file=err["file"], line=int(err["line"]) if err.get("line") else None,
            message=f"Python syntax error: {err['message'][:300]}",
            evidence=[f"ast.parse failed for {err['file']}", err["message"][:300]],
            reproducible=True, confidence=0.99,
            status=EvidenceStatus.CONFIRMED,
            metadata={"parse_error": True},
        ))
    return findings


def analyze_python_ast(root: Path, understanding: RepositoryUnderstanding) -> List[Finding]:
    """Lightweight pyflakes-style checks implemented with the stdlib AST."""
    findings: List[Finding] = []
    local_modules = {Path(c.path).stem for c in understanding.components if c.path}
    # Include non-component source files (e.g. local C extensions such as
    # _speedups.c) so they are not mistaken for missing third-party packages.
    local_modules |= {Path(p).stem for p in understanding.files}
    local_packages = {
        p.relative_to(root).parts[0]
        for p in _iter_files(root)
        if p.suffix == ".py" and len(p.relative_to(root).parts) > 1
    }
    for path in _iter_files(root):
        if path.suffix != ".py":
            continue
        rel = _rel(root, path)
        text = _read(path)
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue  # already reported

        imported: Dict[str, ast.Import | ast.ImportFrom] = {}
        used: Set[str] = set()
        assigned: Set[str] = set()
        undefined: List[Tuple[str, int]] = []
        known: Set[str] = set(dir(builtins))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported[name] = node
                    known.add(name)
                    base = alias.name.split(".")[0]
                    if (base not in STDLIB and base not in local_modules and base not in local_packages
                            and importlib.util.find_spec(base) is None):
                        findings.append(_finding(
                            type="MissingDependency", severity=Severity.HIGH,
                            category=IssueCategory.DEPENDENCY, file=rel, line=node.lineno,
                            message=f"Import '{alias.name}' cannot be resolved",
                            evidence=[f"import statement at {rel}:{node.lineno}",
                                      f"'{base}' not found locally, in stdlib, or in environment"],
                            reproducible=True, confidence=0.9,
                            status=EvidenceStatus.CONFIRMED,
                            metadata={"module": base},
                        ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module.split(".")[0]
                    known.add(node.module)
                    if (base not in STDLIB and base not in local_modules and base not in local_packages
                            and importlib.util.find_spec(base) is None):
                        findings.append(_finding(
                            type="MissingDependency", severity=Severity.HIGH,
                            category=IssueCategory.DEPENDENCY, file=rel, line=node.lineno,
                            message=f"From-import '{node.module}' cannot be resolved",
                            evidence=[f"from-import at {rel}:{node.lineno}",
                                      f"'{base}' not found locally, in stdlib, or in environment"],
                            reproducible=True, confidence=0.9,
                            status=EvidenceStatus.CONFIRMED,
                            metadata={"module": base},
                        ))
                for alias in node.names:
                    known.add(alias.asname or alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                known.add(node.name)
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    used.add(node.id)
                    if node.id not in known and node.id not in assigned:
                        # Ignore names introduced by comprehensions/lambdas etc.
                        undefined.append((node.id, node.lineno))
                elif isinstance(node.ctx, ast.Store):
                    assigned.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                known.add(node.name)
            elif isinstance(node, ast.arg):
                known.add(node.arg)

        # Second pass: filter undefined names that are function parameters,
        # globals, or defined later in module scope.
        module_names = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        } | {
            t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
            for t in ([n.target] if isinstance(n, ast.AnnAssign) else n.targets)
            if isinstance(t, ast.Name)
        }
        for name, line in undefined:
            if name in module_names or name in used:
                continue
            findings.append(_finding(
                type="UndefinedName", severity=Severity.MEDIUM,
                category=IssueCategory.LOGIC, file=rel, line=line,
                message=f"Possibly undefined name '{name}'",
                evidence=[f"name '{name}' loaded at {rel}:{line} without a visible definition"],
                reproducible=False, confidence=0.55,
                metadata={"name": name},
            ))

        for name, node in imported.items():
            if name not in used and name != "*":
                findings.append(_finding(
                    type="UnusedImport", severity=Severity.LOW,
                    category=IssueCategory.OTHER, file=rel, line=node.lineno,
                    message=f"Unused import '{name}'",
                    evidence=[f"imported at {rel}:{node.lineno}, never referenced"],
                    reproducible=False, confidence=0.85,
                ))

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append(_finding(
                    type="BareExcept", severity=Severity.LOW,
                    category=IssueCategory.MISSING_ERROR_HANDLING,
                    file=rel, line=node.lineno,
                    message="Bare 'except:' swallows all errors",
                    evidence=[f"bare except at {rel}:{node.lineno}"],
                    confidence=0.8,
                ))
    return findings


def analyze_configuration(root: Path, understanding: RepositoryUnderstanding) -> List[Finding]:
    findings: List[Finding] = []
    env_files: Set[str] = set()
    defined: Set[str] = set()
    for path in _iter_files(root):
        if path.name.startswith(".env"):
            env_files.add(path.name)
            for line in _read(path).splitlines():
                m = re.match(r"\s*(?:export\s+)?([\w]+)\s*=", line)
                if m:
                    defined.add(m.group(1))
            if path.name == ".env":
                for line in _read(path).splitlines():
                    for m in re.finditer(r"^\s*([\w]+)\s*=\s*(.+)$", line):
                        if m.group(2).strip() and not m.group(2).startswith("#"):
                            findings.append(_finding(
                                type="ExposedSecret", severity=Severity.HIGH,
                                category=IssueCategory.SECURITY, file=_rel(root, path),
                                message=f"Secret-like value assigned to {m.group(1)} in .env",
                                evidence=[".env committed with non-empty value; value redacted"],
                                confidence=0.6,
                            ))

    pattern = re.compile(
        r"os\.environ\[\s*['\"]([\w]+)['\"]\s*\]|os\.getenv\(\s*['\"]([\w]+)['\"]"
    )
    for path in _iter_files(root):
        if path.suffix.lower() not in {".py", ".js", ".ts", ".tsx"}:
            continue
        rel = _rel(root, path)
        for m in pattern.finditer(_read(path)):
            name = m.group(1) or m.group(2)
            if name in defined or os.environ.get(name):
                continue
            is_db = "DB" in name.upper() or "DATABASE" in name.upper()
            findings.append(_finding(
                type="MissingConfiguration", severity=Severity.HIGH if is_db else Severity.MEDIUM,
                category=IssueCategory.CONFIGURATION, file=rel,
                message=f"Required environment variable '{name}' is not defined",
                evidence=[f"{rel} reads {name}",
                          f"No definition found in {sorted(env_files) or 'any .env file'}",
                          "No value present in analysis environment"],
                reproducible=True, confidence=0.93,
                status=EvidenceStatus.CONFIRMED,
                metadata={"env_var": name, "config_key": name},
            ))

    for path in _iter_files(root):
        if path.suffix.lower() == ".json":
            try:
                json.loads(_read(path))
            except json.JSONDecodeError as exc:
                findings.append(_finding(
                    type="InvalidConfiguration", severity=Severity.HIGH,
                    category=IssueCategory.CONFIGURATION, file=_rel(root, path),
                    line=exc.lineno,
                    message=f"Invalid JSON configuration: {exc.msg}",
                    evidence=[f"json parser error at line {exc.lineno}: {exc.msg}"],
                    reproducible=True, confidence=0.99, status=EvidenceStatus.CONFIRMED,
                ))
        elif path.suffix.lower() == ".toml":
            try:
                tomllib.loads(_read(path))
            except tomllib.TOMLDecodeError as exc:
                findings.append(_finding(
                    type="InvalidConfiguration", severity=Severity.HIGH,
                    category=IssueCategory.CONFIGURATION, file=_rel(root, path),
                    message=f"Invalid TOML configuration: {exc}",
                    evidence=[str(exc)], reproducible=True, confidence=0.99,
                    status=EvidenceStatus.CONFIRMED,
                ))
    return findings


def analyze_security(root: Path, understanding: RepositoryUnderstanding) -> List[Finding]:
    findings: List[Finding] = []
    for path in _iter_files(root):
        if path.suffix.lower() not in {".py", ".js", ".ts", ".java", ".go", ".yml", ".yaml"}:
            continue
        rel = _rel(root, path)
        text = _read(path)
        for pattern, label in SECRET_PATTERNS:
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(_finding(
                    type="PotentialSecret", severity=Severity.HIGH,
                    category=IssueCategory.SECURITY, file=rel, line=line,
                    message=label,
                    evidence=[f"{label} detected at {rel}:{line} (value redacted)"],
                    confidence=0.7, metadata={"redacted": True},
                ))
        for pattern, label, category, severity in UNSAFE_PATTERNS:
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(_finding(
                    type=label, severity=severity, category=category,
                    file=rel, line=line, message=label,
                    evidence=[f"pattern detected at {rel}:{line}"],
                    confidence=0.75,
                ))
    return findings


def analyze_generic_syntax(root: Path, understanding: RepositoryUnderstanding) -> List[Finding]:
    """Parse-only syntax checks for non-Python languages when runtimes exist."""
    findings: List[Finding] = []
    from sandbox.executor import execute

    checks = {".js": ["node", "--check"], ".mjs": ["node", "--check"],
              ".ts": ["npx", "tsc", "--noEmit"]}
    checked = 0
    for path in _iter_files(root):
        cmd = checks.get(path.suffix.lower())
        if not cmd or checked >= 20:
            continue
        result = execute(cmd + [_rel(root, path)], cwd=root, timeout=20)
        checked += 1
        if result.returncode != 0 and not result.timed_out:
            findings.append(_finding(
                type="SyntaxError", severity=Severity.HIGH,
                category=IssueCategory.SYNTAX, file=_rel(root, path),
                message=f"Syntax check failed for {path.name}",
                evidence=[result.stderr.strip()[:600] or result.stdout.strip()[:600]],
                reproducible=True, confidence=0.95, status=EvidenceStatus.CONFIRMED,
            ))
    return findings


def analyze(root: Path, understanding: RepositoryUnderstanding) -> Tuple[List[Finding], Dict[str, Any]]:
    details: Dict[str, Any] = {}
    collectors = [
        ("syntax", analyze_python_syntax),
        ("ast", analyze_python_ast),
        ("configuration", analyze_configuration),
        ("security", analyze_security),
        ("generic_syntax", analyze_generic_syntax),
    ]
    findings: List[Finding] = []
    for name, fn in collectors:
        try:
            part = fn(root, understanding)
            findings.extend(part)
            details[name] = {"status": "OK", "findings": len(part)}
        except Exception as exc:  # capability-level failure must not abort pipeline
            details[name] = {"status": "FAILED", "error": str(exc)[:300]}
    return findings, details
