from __future__ import annotations

from pathlib import Path

from sandbox.executor import execute, make_workspace, sanitized_env
from config.settings import settings


def test_env_is_sanitized() -> None:
    env = sanitized_env({"MY_VAR": "value"})
    assert "MY_VAR" in env
    assert "PATH" in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_execution_is_in_sandbox_copy(simple_python_repo: Path) -> None:
    before = (simple_python_repo / "src" / "calc.py").read_text(encoding="utf-8")
    ws = make_workspace(simple_python_repo)
    try:
        assert ws != simple_python_repo.resolve()
        (ws / "src" / "calc.py").write_text("changed\n", encoding="utf-8")
        result = execute(["python", "-c", "print('sandbox')"], cwd=ws)
        assert result.returncode == 0
        assert "sandbox" in result.stdout
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
    assert (simple_python_repo / "src" / "calc.py").read_text(encoding="utf-8") == before


def test_execution_timeout() -> None:
    import tempfile
    ws = Path(tempfile.mkdtemp())
    result = execute(["python", "-c", "while True: pass"], cwd=ws, timeout=2)
    assert result.timed_out


def test_network_proxy_restriction() -> None:
    if settings.network_restricted:
        env = sanitized_env()
        assert env.get("HTTP_PROXY", "").startswith("http://127.0.0.1")
