"""Shared pytest fixtures and environment setup for the RCA system's own tests."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
from pathlib import Path

import pytest

# Isolate all persistence before the settings singleton is created.
os.environ.setdefault("RCA_DATA_DIR", tempfile.mkdtemp(prefix="rca_tests_"))
os.environ.setdefault("RCA_COMMAND_TIMEOUT", "30")
os.environ.setdefault("RCA_MAX_CPU_SECONDS", "15")

import sys  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repository_analyzer.analyzer import analyze_repository  # noqa: E402
from sandbox.executor import make_workspace  # noqa: E402


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


@pytest.fixture
def simple_python_repo(tmp_path: Path) -> Path:
    root = tmp_path / "simple_python_repo"
    root.mkdir()
    write(root, "src/__init__.py", "")
    write(root, "src/calc.py", """
        def add(a, b):
            return a + b

        def divide(a, b):
            return a / b
    """)
    write(root, "tests/test_calc.py", """
        from src.calc import add

        def test_add():
            assert add(1, 2) == 3
    """)
    write(root, "requirements.txt", "pytest>=7\n")
    return root


@pytest.fixture
def broken_python_repo(tmp_path: Path) -> Path:
    root = tmp_path / "broken_python_repo"
    root.mkdir()
    write(root, "src/__init__.py", "")
    write(root, "src/config.py", """
        import os
        DB_URL = os.environ['DB_URL']
    """)
    write(root, "src/database.py", """
        from src.config import DB_URL

        def connect():
            return DB_URL
    """)
    write(root, "src/service.py", """
        from src.database import connect

        def start():
            return connect()
    """)
    write(root, "tests/test_service.py", """
        from src.service import start

        def test_start():
            assert start()
    """)
    return root


@pytest.fixture
def understanding(simple_python_repo: Path):
    return analyze_repository(simple_python_repo)


@pytest.fixture
def sandbox_copy(simple_python_repo: Path) -> Path:
    ws = make_workspace(simple_python_repo)
    yield ws
    shutil.rmtree(ws, ignore_errors=True)
