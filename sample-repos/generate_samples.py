"""Regenerate the intentionally broken sample repositories.

Each sample has a known expected root cause stored in ``expected_rca.json`` so
the evaluation framework can measure detection and RCA accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent

FILES: dict[str, dict[str, str]] = {
    "syntax-error-project": {
        "src/__init__.py": "",
        "src/calculator.py": (
            "def add(a, b)\n"
            "    return a + b\n"
        ),
        "tests/test_calculator.py": (
            "from src.calculator import add\n\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        ),
        "expected_rca.json": json.dumps({
            "name": "syntax-error-project",
            "expected_root_cause_types": ["SyntaxError"],
            "expected_categories": ["Syntax Error"],
            "expected_file": "src/calculator.py",
            "expected_symptom_count": 1,
            "description": "Missing colon in function definition prevents module import.",
        }, indent=2),
    },
    "dependency-error-project": {
        "src/__init__.py": "",
        "src/service.py": (
            "import nonexistent_package_xyz\n\n\n"
            "def build_message(name):\n"
            "    return nonexistent_package_xyz.format(name)\n"
        ),
        "tests/test_service.py": (
            "from src.service import build_message\n\n\n"
            "def test_build_message():\n"
            "    assert build_message('Ada') == 'Hello Ada'\n"
        ),
        "expected_rca.json": json.dumps({
            "name": "dependency-error-project",
            "expected_root_cause_types": ["MissingDependency", "UndeclaredDependency", "ModuleNotFoundError"],
            "expected_categories": ["Dependency Error"],
            "expected_file": "src/service.py",
            "expected_symptom_count": 1,
            "description": "Import of an uninstalled, undeclared package fails at import time.",
        }, indent=2),
    },
    "configuration-error-project": {
        "src/__init__.py": "",
        "src/database.py": (
            "import os\n\n"
            "DB_URL = os.environ['DB_URL']\n\n\n"
            "def connect():\n"
            "    return DB_URL\n"
        ),
        "tests/test_database.py": (
            "from src.database import connect\n\n\n"
            "def test_connect_returns_url():\n"
            "    assert connect().startswith('postgres')\n"
        ),
        ".env.example": "APP_NAME=configuration-error-project\n",
        "expected_rca.json": json.dumps({
            "name": "configuration-error-project",
            "expected_root_cause_types": ["MissingConfiguration", "MissingEnvironmentVariable"],
            "expected_categories": ["Configuration Error"],
            "expected_file": "src/database.py",
            "expected_env_var": "DB_URL",
            "expected_symptom_count": 1,
            "description": "Required DB_URL environment variable is never defined.",
        }, indent=2),
    },
    "runtime-error-project": {
        "src/__init__.py": "",
        "src/divider.py": (
            "def divide(a, b):\n"
            "    return a / b\n"
        ),
        "tests/test_divider.py": (
            "from src.divider import divide\n\n\n"
            "def test_divide_by_zero():\n"
            "    assert divide(1, 0) == 0\n"
        ),
        "expected_rca.json": json.dumps({
            "name": "runtime-error-project",
            "expected_root_cause_types": ["FAIL", "ERROR", "DynamicBoundaryFailure", "Logic Error"],
            "expected_categories": ["Runtime Error", "Test Failure", "Logic Error"],
            "expected_file": "src/divider.py",
            "expected_symptom_count": 1,
            "description": "Unhandled division by zero in divide().",
        }, indent=2),
    },
    "data-error-project": {
        "src/__init__.py": "",
        "src/processor.py": (
            "def double_value(record):\n"
            "    return record['value'] * 2\n"
        ),
        "tests/test_processor.py": (
            "from src.processor import double_value\n\n\n"
            "def test_double_value():\n"
            "    assert double_value({'value': '5'}) == 10\n"
        ),
        "expected_rca.json": json.dumps({
            "name": "data-error-project",
            "expected_root_cause_types": ["FAIL", "ERROR", "TypeError", "DynamicBoundaryFailure"],
            "expected_categories": ["Data Error", "Type Error", "Test Failure"],
            "expected_file": "src/processor.py",
            "expected_symptom_count": 1,
            "description": "String input multiplied as if numeric; missing input validation.",
        }, indent=2),
    },
    "integration-error-project": {
        "src/__init__.py": "",
        "src/database.py": (
            "def fetch_user(user_id):\n"
            "    raise ConnectionError('invalid DSN: user_store')\n"
        ),
        "src/service.py": (
            "from src.database import fetch_user\n\n\n"
            "def get_user(user_id):\n"
            "    return fetch_user(user_id)['name']\n"
        ),
        "tests/test_service.py": (
            "from src.service import get_user\n\n\n"
            "def test_get_user():\n"
            "    assert get_user(1) == 'Ada'\n"
        ),
        "expected_rca.json": json.dumps({
            "name": "integration-error-project",
            "expected_root_cause_types": ["FAIL", "ERROR", "ConnectionError"],
            "expected_categories": ["Integration Error", "Database Error", "Runtime Error", "Test Failure"],
            "expected_file": "src/database.py",
            "expected_symptom_count": 1,
            "description": "Database layer raises before service can assemble response.",
        }, indent=2),
    },
    "cascading-failure-project": {
        "src/__init__.py": "",
        "src/config.py": (
            "import os\n\n"
            "DATABASE_URL = os.environ['DATABASE_URL']\n"
        ),
        "src/database.py": (
            "from src.config import DATABASE_URL\n\n\n"
            "def connect():\n"
            "    return DATABASE_URL\n"
        ),
        "src/service.py": (
            "from src.database import connect\n\n\n"
            "def initialise_service():\n"
            "    return {'connection': connect()}\n"
        ),
        "tests/test_database.py": (
            "from src.database import connect\n\n\n"
            "def test_database_connect():\n"
            "    assert connect()\n"
        ),
        "tests/test_api.py": (
            "from src.service import initialise_service\n\n\n"
            "def test_api_startup():\n"
            "    assert initialise_service()['connection']\n"
        ),
        ".env.example": "SERVICE_NAME=cascading-failure-project\n",
        "expected_rca.json": json.dumps({
            "name": "cascading-failure-project",
            "expected_root_cause_types": ["MissingConfiguration", "MissingEnvironmentVariable"],
            "expected_categories": ["Configuration Error"],
            "expected_file": "src/config.py",
            "expected_env_var": "DATABASE_URL",
            "expected_symptom_count": 2,
            "description": "One missing env var causes two test failures through database -> service chain.",
        }, indent=2),
    },
}


def generate() -> list[str]:
    created = []
    for repo, files in FILES.items():
        root = BASE / repo
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        created.append(str(root))
    return created


if __name__ == "__main__":
    for path in generate():
        print("generated", path)
