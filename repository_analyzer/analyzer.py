"""Repository understanding engine.

Builds a machine-readable representation: technologies, components, tests,
entry points, external services and a dependency/component graph.
"""
from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from xml.etree import ElementTree

from models.domain import (
    Component,
    ComponentType,
    DependencyEdge,
    EdgeKind,
    RepositoryProfile,
    new_id,
)

LANGUAGE_BY_EXT: Dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".c": "C",
    ".cpp": "C++", ".h": "C/C++", ".cs": "C#", ".scala": "Scala", ".sh": "Shell",
    ".sql": "SQL", ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
    ".toml": "TOML", ".xml": "XML", ".md": "Markdown", ".html": "HTML",
    ".css": "CSS", ".tf": "Terraform", ".gradle": "Groovy",
}

CONFIG_NAMES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
    "poetry.lock", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", ".env", ".env.example",
    "Makefile", "CMakeLists.txt", "tsconfig.json", "jest.config.js",
    "jest.config.ts", "pytest.ini", "tox.ini", "conftest.py", "webpack.config.js",
    "vite.config.ts", "application.properties", "application.yml", ".gitlab-ci.yml",
}

TEST_MARKERS = ("test", "spec", "tests", "__tests__")

SERVICE_PATTERNS: Dict[str, str] = {
    "postgres": r"postgres(?:ql)?://", "mysql": r"mysql://",
    "mongodb": r"mongodb(?:\+srv)?://", "redis": r"redis://",
    "kafka": r"kafka", "rabbitmq": r"amqp://", "elasticsearch": r"elasticsearch",
    "aws": r"boto3|aws_|AKIA[0-9A-Z]{16}", "gcp": r"google\.cloud|firebase",
    "azure": r"azure", "stripe": r"stripe", "twilio": r"twilio",
}

DB_PATTERN = re.compile(
    r"(DB_URL|DATABASE_URL|DATABASE_URI|DB_HOST|SQLALCHEMY_DATABASE_URI|jdbc:)", re.I
)
ENV_PATTERN = re.compile(r"os\.environ\[['\"]([\w]+)['\"]\]|os\.getenv\(['\"]([\w]+)['\"]")
IMPORT_PY = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
IMPORT_JS = re.compile(
    r"(?:import\s+(?:[^'\"]+?\s+from\s+)?|require\()\s*['\"]([^'\"]+)['\"]"
)
IMPORT_JAVA = re.compile(r"^\s*import\s+([\w.]+)\s*;", re.M)
IMPORT_GO = re.compile(r'^\s*"([a-zA-Z0-9_./\-]+)"', re.M)
IMPORT_RUST = re.compile(r"^\s*use\s+([\w:]+)", re.M)


@dataclass
class RepositoryUnderstanding:
    profile: RepositoryProfile
    components: List[Component] = field(default_factory=list)
    edges: List[DependencyEdge] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    parse_errors: List[Dict[str, str]] = field(default_factory=list)
    config_contents: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "components": [c.to_dict() for c in self.components],
            "edges": [e.to_dict() for e in self.edges],
            "files": self.files,
            "parse_errors": self.parse_errors,
            "config_contents": self.config_contents,
        }


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in {".git", ".hg", ".svn", "node_modules", "__pycache__",
                         ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
                         "dist", "build", ".rca_data"}
        ]
        for name in filenames:
            yield Path(dirpath) / name


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _detect_profile(root: Path, files: List[Path]) -> RepositoryProfile:
    profile = RepositoryProfile()
    extensions = Counter(p.suffix.lower() for p in files if p.suffix)
    profile.languages = [
        lang for lang, _ in sorted(
            ((LANGUAGE_BY_EXT[e], c) for e, c in extensions.items() if e in LANGUAGE_BY_EXT),
            key=lambda x: -x[1],
        )
    ]
    names = {p.name for p in files}
    rels = [_rel(root, p) for p in files]
    profile.config_files = sorted(n for n in names & CONFIG_NAMES)

    contents: Dict[str, str] = {}
    for p in files:
        if p.name in CONFIG_NAMES:
            contents[p.name] = _read(p)

    if "package.json" in contents:
        profile.package_managers.append("npm/yarn/pnpm")
        try:
            pkg = json.loads(contents["package.json"])
            for dep in ("dependencies", "devDependencies"):
                profile.dependencies[dep] = list((pkg.get(dep) or {}).keys())
            scripts = pkg.get("scripts") or {}
            profile.project_types.append("Node.js application")
            if any("react" in str(v).lower() for v in (pkg.get("dependencies") or {})):
                profile.frameworks.append("React")
            if "jest" in scripts or any("jest" in str(v) for v in scripts.values()):
                profile.test_frameworks.append("Jest")
            if any("mocha" in str(v).lower() for v in scripts.values()):
                profile.test_frameworks.append("Mocha")
            for key in ("start", "main", "dev"):
                if key in scripts:
                    profile.entry_points.append(f"npm run {key}")
        except (json.JSONDecodeError, TypeError):
            profile.project_types.append("Node.js application (invalid package.json)")
    if contents.get("yarn.lock"):
        profile.package_managers = ["yarn"] if not profile.package_managers else profile.package_managers
    if contents.get("pnpm-lock.yaml"):
        profile.package_managers.append("pnpm")

    if "requirements.txt" in contents:
        profile.package_managers.append("pip")
        deps = []
        for line in contents["requirements.txt"].splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                deps.append(re.split(r"[<>=!~\[]", line)[0])
        profile.dependencies["requirements.txt"] = deps
    if "pyproject.toml" in contents:
        profile.package_managers.append("pyproject/poetry")
        try:
            data = tomllib.loads(contents["pyproject.toml"])
            project = data.get("project", {})
            profile.project_types.append("Python project")
            for dep in project.get("dependencies", []) or []:
                profile.dependencies.setdefault("pyproject.toml", []).append(
                    re.split(r"[<>=!~\[]", str(dep))[0]
                )
            if any("pytest" in str(v).lower() for v in
                   (data.get("tool", {}).get("pytest", {}) or {}).keys()):
                profile.test_frameworks.append("pytest")
        except tomllib.TOMLDecodeError:
            pass
    has_test_files = any(
        p.name.startswith("test_") or p.name.endswith("_test.py") or
        any("test" in part.lower() for part in p.parts[-2:-1])
        for p in files if p.suffix == ".py"
    )
    if (has_test_files or "setup.py" in names or "pytest.ini" in names
            or "tox.ini" in names or "conftest.py" in names):
        if "pytest" not in profile.test_frameworks:
            profile.test_frameworks.append("pytest")
    if any("unittest" in _read(p) for p in files if p.suffix == ".py"):
        if "unittest" not in profile.test_frameworks:
            profile.test_frameworks.append("unittest")

    if "pom.xml" in contents:
        profile.package_managers.append("Maven")
        profile.project_types.append("Java/Maven project")
        try:
            xml = ElementTree.fromstring(contents["pom.xml"])
            ns = {"m": "http://maven.apache.org/POM/4.0.0"}
            deps = [d.findtext("m:artifactId", "", ns) for d in xml.findall(".//m:dependency", ns)]
            profile.dependencies["pom.xml"] = [d for d in deps if d]
            plugins = [x.text or "" for x in xml.findall(".//m:artifactId", ns)]
            if any("surefire" in p for p in plugins):
                profile.test_frameworks.append("JUnit/Maven Surefire")
        except ElementTree.ParseError:
            pass
    if any(n.startswith("build.gradle") for n in names):
        profile.package_managers.append("Gradle")
        profile.project_types.append("Java/Gradle project")
        profile.test_frameworks.append("JUnit/Gradle")

    if "Cargo.toml" in contents:
        profile.package_managers.append("Cargo")
        profile.project_types.append("Rust project")
        profile.test_frameworks.append("cargo test")
        profile.dependencies["Cargo.toml"] = re.findall(
            r"^\s*([\w\-]+)\s*=", contents["Cargo.toml"], re.M
        )
    if "go.mod" in contents:
        profile.package_managers.append("go modules")
        profile.project_types.append("Go project")
        profile.test_frameworks.append("go test")
        profile.dependencies["go.mod"] = [
            line.split()[0] for line in contents["go.mod"].splitlines()
            if line.strip() and not line.startswith(("module", "go ", "require", ")", "("))
        ]

    if "Dockerfile" in names:
        profile.project_types.append("Containerised application")
    if any(n.startswith("docker-compose") for n in names):
        profile.project_types.append("Multi-service (docker-compose)")
    for marker in ("Flask", "FastAPI", "django", "express", "spring", "gin", "actix"):
        if any(marker.lower() in _read(p).lower() for p in files
               if p.suffix in {".py", ".js", ".ts", ".java", ".go", ".rs"}):
            if marker not in profile.frameworks:
                profile.frameworks.append(marker)

    for rel in rels:
        low = rel.lower()
        if any(m in low for m in TEST_MARKERS):
            continue
        if Path(rel).name in {"main.py", "app.py", "index.js", "server.js", "Main.java", "main.go", "main.rs"}:
            if rel not in profile.entry_points:
                profile.entry_points.append(rel)

    blob = "\n".join(_read(p) for p in files if p.suffix in
                     {".py", ".js", ".ts", ".java", ".go", ".rs", ".yaml", ".yml", ".toml", ".env"})
    if DB_PATTERN.search(blob):
        profile.database_connections.append("Configured database connection reference")
    for svc, pattern in SERVICE_PATTERNS.items():
        if re.search(pattern, blob, re.I):
            profile.external_services.append(svc)
    if any("DATABASE_URL" in line or "DB_URL" in line
           for line in blob.splitlines()):
        if "Configured database connection reference" not in profile.database_connections:
            profile.database_connections.append("Environment-based database configuration")

    profile.project_types = list(dict.fromkeys(profile.project_types))
    profile.languages = list(dict.fromkeys(profile.languages))
    profile.test_frameworks = list(dict.fromkeys(profile.test_frameworks))
    profile.dependencies = {k: sorted(set(v)) for k, v in profile.dependencies.items()}
    profile.total_files = len(files)
    profile.total_lines = sum(_read(p).count("\n") for p in files)
    return profile


def _python_components(root: Path, path: Path, rel: str, text: str,
                       out: List[Component], errors: List[Dict[str, str]],
                       module_kind: ComponentType = ComponentType.MODULE) -> List[str]:
    imports: List[str] = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        errors.append({"file": rel, "message": str(exc), "line": str(exc.lineno or "")})
        return re.findall(IMPORT_PY, text) and [a or b for a, b in re.findall(IMPORT_PY, text)] or []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    cls_nodes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    fn_nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not cls_nodes and not fn_nodes:
        out.append(Component(
            id=new_id("CMP"), name=Path(rel).stem, type=module_kind,
            path=rel, language="Python", imports=imports,
            metadata={"lines": text.count("\n")},
        ))
        return imports
    out.append(Component(
        id=new_id("CMP"), name=Path(rel).stem, type=module_kind,
        path=rel, language="Python", imports=imports,
        symbols=[n.name for n in cls_nodes + fn_nodes],
        metadata={"lines": text.count("\n")},
    ))
    for cls in cls_nodes:
        methods = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        out.append(Component(
            id=new_id("CMP"), name=cls.name, type=ComponentType.CLASS,
            path=rel, language="Python", line=cls.lineno, imports=imports,
            symbols=methods, metadata={"module": Path(rel).stem, "bases": [
                ast.unparse(b) for b in cls.bases]},
        ))
        for m in methods:
            node = next(n for n in cls.body if getattr(n, "name", None) == m)
            out.append(Component(
                id=new_id("CMP"), name=f"{cls.name}.{m}", type=ComponentType.FUNCTION,
                path=rel, language="Python", line=node.lineno,
                imports=imports, metadata={"class": cls.name},
            ))
    for fn in fn_nodes:
        out.append(Component(
            id=new_id("CMP"), name=fn.name, type=ComponentType.FUNCTION,
            path=rel, language="Python", line=fn.lineno, imports=imports,
            metadata={"module": Path(rel).stem, "args": [a.arg for a in fn.args.args]},
        ))
    return imports


def _generic_components(rel: str, text: str, language: str, kind: ComponentType,
                        import_pattern: re.Pattern, out: List[Component]) -> List[str]:
    imports = [m.group(1) if m.lastindex else m.group(0)
               for m in import_pattern.finditer(text)]
    out.append(Component(
        id=new_id("CMP"), name=Path(rel).stem, type=kind, path=rel,
        language=language, imports=imports, metadata={"lines": text.count("\n")},
    ))
    if language in ("Java", "C#", "Kotlin", "Scala"):
        for m in re.finditer(r"\b(?:public\s+|class\s+)*([\w]+)\s*(?:extends|implements|:|\{)",
                             text):
            name = m.group(1)
            if name and name[0].isupper() and name not in {"Class", "Public", "Return"}:
                out.append(Component(
                    id=new_id("CMP"), name=name, type=ComponentType.CLASS,
                    path=rel, language=language, imports=imports,
                ))
    return imports


def analyze_repository(root: Path) -> RepositoryUnderstanding:
    root = Path(root).resolve()
    files = sorted(_iter_files(root))
    profile = _detect_profile(root, files)
    understanding = RepositoryUnderstanding(profile=profile)
    understanding.files = [_rel(root, f) for f in files]

    module_imports: Dict[str, List[str]] = {}
    for path in files:
        rel = _rel(root, path)
        ext = path.suffix.lower()
        text = _read(path)
        lang = LANGUAGE_BY_EXT.get(ext, "")
        is_test = any(m in rel.lower() for m in TEST_MARKERS) or "conftest" in rel.lower()
        kind = ComponentType.TEST if is_test else ComponentType.MODULE
        if rel in profile.entry_points:
            kind = ComponentType.ENTRY_POINT
        if ext == ".py":
            imports = _python_components(root, path, rel, text, understanding.components,
                                         understanding.parse_errors, kind)
        elif ext in (".js", ".jsx", ".mjs", ".ts", ".tsx"):
            imports = _generic_components(rel, text, lang, kind, IMPORT_JS, understanding.components)
        elif ext == ".java":
            imports = _generic_components(rel, text, lang, kind, IMPORT_JAVA, understanding.components)
        elif ext == ".go":
            imports = _generic_components(rel, text, lang, kind, IMPORT_GO, understanding.components)
        elif ext == ".rs":
            imports = _generic_components(rel, text, lang, kind, IMPORT_RUST, understanding.components)
        else:
            imports = []
        if imports:
            module_imports[rel] = imports

        low = text.lower() if ext in {".py", ".js", ".jsx", ".mjs", ".ts", ".tsx",
                                      ".java", ".go", ".rs", ".rb", ".php"} else ""
        if "database" in low or any(x in low for x in ("db_url", "connection", "sqlalchemy", "psycopg", "pymysql")):
            understanding.components.append(Component(
                id=new_id("CMP"), name=f"{Path(rel).stem}:database", type=ComponentType.DATABASE,
                path=rel, language=lang, metadata={"reason": "database reference"},
            ))
        if any(x in low for x in ("@app.route", "router.", "app.get(", "app.post(", "@RestController", "http.handle")):
            understanding.components.append(Component(
                id=new_id("CMP"), name=f"{Path(rel).stem}:api", type=ComponentType.API,
                path=rel, language=lang, metadata={"reason": "HTTP route reference"},
            ))
        if any(x in low for x in ("env[", "getenv", "process.env", "os.environ")):
            understanding.components.append(Component(
                id=new_id("CMP"), name=f"{Path(rel).stem}:config", type=ComponentType.CONFIG,
                path=rel, language=lang, metadata={"reason": "environment configuration use"},
            ))

    component_by_path: Dict[str, List[Component]] = defaultdict(list)
    for c in understanding.components:
        if c.path:
            component_by_path[c.path].append(c)

    # Build module-level dependency edges (resolved against known local modules).
    module_names = {
        Path(p).stem: p for p in understanding.files if p.endswith(".py")
    }
    for rel, imports in module_imports.items():
        targets: Set[str] = set()
        for imp in imports:
            base = imp.split(".")[0] if imp else ""
            if base in module_names and module_names[base] != rel:
                targets.add(module_names[base])
            for candidate, module_rel in module_names.items():
                if candidate and candidate != Path(rel).stem and candidate in imp:
                    targets.add(module_rel)
        for target in targets:
            understanding.edges.append(DependencyEdge(
                source=rel, target=target, kind=EdgeKind.DEPENDS_ON,
                detail="import reference",
            ))
        for comp in component_by_path.get(rel, []):
            if comp.type in (ComponentType.MODULE, ComponentType.ENTRY_POINT):
                for imp in imports:
                    if "." not in imp and imp not in {"os", "sys", "json", "time", "re", "math"}:
                        understanding.edges.append(DependencyEdge(
                            source=comp.id, target=imp, kind=EdgeKind.DEPENDS_ON,
                            detail="external/imported dependency",
                        ))
    return understanding
