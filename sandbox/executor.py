"""Isolated command execution and workspace copying.

Repository code is never executed in the user's original directory.  Every
command runs against a temporary copy with a sanitised environment, time and
output limits, and (on POSIX) CPU/memory limits.  Network restrictions are
requested through environment variables and can be tightened with a container
in production (see README "Security").
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from config.settings import settings

_IGNORE = shutil.ignore_patterns(
    ".git", ".hg", ".svn", "__pycache__", "*.pyc", "*.pyo",
    "node_modules", ".venv", "venv", "env", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".rca_data", "*.egg-info",
    ".idea", ".vscode", ".DS_Store", "coverage", ".coverage",
)


@dataclass
class SandboxResult:
    command: List[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()

    def to_dict(self) -> Dict[str, object]:
        return {
            "command": self.command, "cwd": self.cwd,
            "returncode": self.returncode, "stdout": self.stdout,
            "stderr": self.stderr, "duration": self.duration,
            "timed_out": self.timed_out, "truncated": self.truncated,
        }


def make_workspace(source: Path, prefix: str = "rca_ws_") -> Path:
    """Copy a validated repository into a fresh temporary workspace."""
    root = settings.workspace_root
    root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))
    for entry in source.iterdir():
        dst = path / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst, ignore=_IGNORE, dirs_exist_ok=True)
        elif entry.is_file():
            shutil.copy2(entry, dst)
    return path


def sanitized_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return a minimal environment that removes common credential variables."""
    keep = ("PATH", "SystemRoot", "TEMP", "TMP", "HOME", "LANG", "LC_ALL",
            "PYTHONIOENCODING", "USERPROFILE", "COMSPEC")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "RCA_SANDBOX": "1",
        "CI": "1",
        "NO_COLOR": "1",
        # Prevent arbitrary third-party pytest plugins from loading code inside
        # the sandbox. Specific plugins can be opt-in via RCA_PYTEST_PLUGINS.
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    if settings.network_restricted:
        # Advisory: Python/JVM/Node tooling honours proxy vars. A hardened
        # deployment should additionally use a network namespace/container.
        env["HTTP_PROXY"] = "http://127.0.0.1:9"
        env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        env["ALL_PROXY"] = "http://127.0.0.1:9"
        env["NO_PROXY"] = "localhost,127.0.0.1"
    if extra:
        env.update(extra)
    return env


def _limits():
    if platform.system() != "POSIX":
        return None

    def apply() -> None:  # pragma: no cover - platform dependent
        import resource

        cpu = settings.max_cpu_seconds
        mem = settings.max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def execute(
    command: Sequence[str],
    cwd: Path,
    timeout: Optional[int] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> SandboxResult:
    """Run a command in the sandbox and capture bounded output."""
    timeout = timeout or settings.command_timeout
    cmd = [str(c) for c in command]
    start = time.time()
    timed_out = False
    truncated = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=sanitized_env(env_extra),
            capture_output=True,
            timeout=timeout,
            text=True,
            errors="replace",
            preexec_fn=_limits(),
            start_new_session=True,
        )
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out = exc.stdout or ""
        err = (exc.stderr or "") + f"\n[timeout after {timeout}s]"
        code = -9
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        out, err, code = "", f"[sandbox error] {exc}", -1

    limit = settings.max_output_bytes
    if len(out) > limit:
        out = out[:limit] + "\n[output truncated]"
        truncated = True
    if len(err) > limit:
        err = err[:limit] + "\n[output truncated]"
        truncated = True

    return SandboxResult(
        command=cmd, cwd=str(cwd), returncode=code, stdout=out, stderr=err,
        duration=round(time.time() - start, 3), timed_out=timed_out,
        truncated=truncated,
    )


def run_python(code: str, cwd: Path, timeout: Optional[int] = None) -> SandboxResult:
    return execute([os.sys.executable, "-c", code], cwd=cwd, timeout=timeout)


def cleanup_workspace(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:  # pragma: no cover - defensive
        pass
