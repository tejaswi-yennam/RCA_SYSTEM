"""Central configuration for the RCA system.

All settings can be overridden with environment variables prefixed ``RCA_``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"RCA_{name}", default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", ".rca_data")))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "")))

    # Sandbox / execution limits. Defaults are generous so large repositories
    # can be ingested and tested; lower them via RCA_* on constrained hosts.
    command_timeout: int = field(default_factory=lambda: _env_int("COMMAND_TIMEOUT", 300))
    max_cpu_seconds: int = field(default_factory=lambda: _env_int("MAX_CPU_SECONDS", 120))
    max_memory_mb: int = field(default_factory=lambda: _env_int("MAX_MEMORY_MB", 2048))
    max_output_bytes: int = field(default_factory=lambda: _env_int("MAX_OUTPUT_BYTES", 2_000_000))
    network_restricted: bool = field(default_factory=lambda: _env_bool("NETWORK_RESTRICTED", True))

    # Agent / LLM (optional). Without these the offline heuristic provider is used.
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.1))

    # Analysis behaviour
    confidence_threshold: float = field(default_factory=lambda: _env_float("CONFIDENCE_THRESHOLD", 0.65))
    enable_fix_verification: bool = field(default_factory=lambda: _env_bool("ENABLE_FIX_VERIFICATION", True))
    run_dynamic_tests: bool = field(default_factory=lambda: _env_bool("RUN_DYNAMIC_TESTS", True))
    max_files_in_context: int = field(default_factory=lambda: _env_int("MAX_FILES_IN_CONTEXT", 12))
    max_agent_iterations: int = field(default_factory=lambda: _env_int("MAX_AGENT_ITERATIONS", 8))

    # Ingestion
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 1000))
    max_repo_files: int = field(default_factory=lambda: _env_int("MAX_REPO_FILES", 100_000))

    # Server
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if str(self.db_path) in ("", "."):
            self.db_path = self.data_dir / "rca.db"
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "workspaces").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "reports").mkdir(parents=True, exist_ok=True)

    @property
    def workspace_root(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key)


settings = Settings()
