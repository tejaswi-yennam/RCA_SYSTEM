"""Structured persistence for repositories, analysis runs and all evidence.

A lightweight SQLite store is used so the system has zero external service
requirements while still supporting multiple repositories and runs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from models.domain import (
    AnalysisRun,
    AnalysisStatus,
    JSONMixin,
    LogEntry,
    Repository,
    new_id,
)

_ITEM_TABLES = {
    "components", "tests", "findings", "evidence", "hypotheses",
    "rcas", "fixes", "verifications", "regressions", "groups",
}


class Database:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    repository_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    current_stage TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_items_analysis_kind
                    ON items(analysis_id, kind);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    format TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_logs_analysis ON logs(analysis_id, timestamp);
                """
            )

    # ---------- repositories ----------
    def create_repository(self, repo: Repository) -> Repository:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO repositories VALUES (?,?,?,?,?,?,?)",
                (repo.id, repo.name, repo.source_type, repo.source,
                 str(repo.path), repo.created_at, json.dumps(repo.metadata)),
            )
        return repo

    def get_repository(self, repo_id: str) -> Optional[Repository]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM repositories WHERE id=?", (repo_id,)
            ).fetchone()
        if not row:
            return None
        return Repository(
            id=row["id"], name=row["name"], source_type=row["source_type"],
            source=row["source"], path=row["path"], created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_repositories(self) -> List[Repository]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repositories ORDER BY created_at DESC"
            ).fetchall()
        return [
            Repository(
                id=r["id"], name=r["name"], source_type=r["source_type"],
                source=r["source"], path=r["path"], created_at=r["created_at"],
                metadata=json.loads(r["metadata_json"]),
            )
            for r in rows
        ]

    # ---------- analyses ----------
    def create_analysis(self, analysis: AnalysisRun) -> AnalysisRun:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?)",
                (analysis.id, analysis.repository_id, analysis.status.value,
                 analysis.progress, analysis.current_stage, analysis.started_at,
                 analysis.finished_at, json.dumps([]), json.dumps({}), ""),
            )
        return analysis

    def update_analysis(self, analysis: AnalysisRun) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE analyses SET status=?, progress=?, current_stage=?,
                   started_at=?, finished_at=?, capabilities_json=?, summary_json=?,
                   error=? WHERE id=?""",
                (analysis.status.value, analysis.progress, analysis.current_stage,
                 analysis.started_at, analysis.finished_at,
                 json.dumps(_serialize(analysis.capabilities)),
                 json.dumps(_serialize(analysis.summary)), analysis.error, analysis.id),
            )

    def get_analysis(self, analysis_id: str) -> Optional[AnalysisRun]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM analyses WHERE id=?", (analysis_id,)
            ).fetchone()
        if not row:
            return None
        return AnalysisRun(
            id=row["id"], repository_id=row["repository_id"],
            status=AnalysisStatus(row["status"]), progress=row["progress"],
            current_stage=row["current_stage"], started_at=row["started_at"],
            finished_at=row["finished_at"],
            capabilities=_deserialize_capabilities(json.loads(row["capabilities_json"])),
            summary=json.loads(row["summary_json"]), error=row["error"],
        )

    def list_analyses(self, repository_id: Optional[str] = None) -> List[AnalysisRun]:
        with self._lock:
            if repository_id:
                rows = self._conn.execute(
                    "SELECT * FROM analyses WHERE repository_id=? ORDER BY started_at DESC",
                    (repository_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM analyses ORDER BY started_at DESC"
                ).fetchall()
        out = []
        for row in rows:
            out.append(AnalysisRun(
                id=row["id"], repository_id=row["repository_id"],
                status=AnalysisStatus(row["status"]), progress=row["progress"],
                current_stage=row["current_stage"], started_at=row["started_at"],
                finished_at=row["finished_at"],
                capabilities=_deserialize_capabilities(json.loads(row["capabilities_json"])),
                summary=json.loads(row["summary_json"]), error=row["error"],
            ))
        return out

    # ---------- generic typed items ----------
    def save_items(self, analysis_id: str, kind: str, items: List[JSONMixin]) -> None:
        if kind not in _ITEM_TABLES:
            raise ValueError(f"Unknown item kind: {kind}")
        with self._lock, self._conn:
            for item in items:
                data = item.to_dict()
                item_id = data.get("id") or new_id("ITEM")
                self._conn.execute(
                    "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)",
                    (item_id, analysis_id, kind,
                     json.dumps(data, default=str), time.time()),
                )

    def get_items(self, analysis_id: str, kind: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM items WHERE analysis_id=? AND kind=?",
                (analysis_id, kind),
            ).fetchall()
        return [json.loads(r["data_json"]) for r in rows]

    # ---------- reports ----------
    def save_report(self, analysis_id: str, content: str, fmt: str = "markdown") -> str:
        report_id = new_id("RPT")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?)",
                (report_id, analysis_id, fmt, content, time.time()),
            )
        return report_id

    def get_report(self, analysis_id: str, fmt: Optional[str] = None) -> Optional[str]:
        with self._lock:
            if fmt:
                row = self._conn.execute(
                    "SELECT content FROM reports WHERE analysis_id=? AND format=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (analysis_id, fmt),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT content FROM reports WHERE analysis_id=? ORDER BY created_at DESC LIMIT 1",
                    (analysis_id,),
                ).fetchone()
        return row["content"] if row else None

    # ---------- logs ----------
    def log(self, entry: LogEntry) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO logs VALUES (?,?,?,?,?,?,?)",
                (entry.id, entry.analysis_id, entry.level, entry.stage,
                 entry.message, entry.timestamp, json.dumps(entry.data, default=str)),
            )

    def get_logs(self, analysis_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM logs WHERE analysis_id=? ORDER BY timestamp DESC LIMIT ?",
                (analysis_id, limit),
            ).fetchall()
        return [
            {
                "id": r["id"], "level": r["level"], "stage": r["stage"],
                "message": r["message"], "timestamp": r["timestamp"],
                "data": json.loads(r["data_json"]),
            }
            for r in rows
        ]


def _serialize(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _deserialize_capabilities(data: List[Dict[str, Any]]) -> List[Any]:
    from models.domain import CapabilityResult, CapabilityStatus

    return [
        CapabilityResult(
            name=d.get("name", ""),
            status=CapabilityStatus(d.get("status", "UNAVAILABLE")),
            detail=d.get("detail", ""), duration=d.get("duration", 0.0),
        )
        for d in data
    ]


_db: Optional[Database] = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
