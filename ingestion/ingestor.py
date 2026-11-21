"""Repository input layer: validation, extraction and normalisation."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config.settings import settings
from models.domain import Repository, new_id

_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2")
_GIT_RE = re.compile(
    r"^(https?://|git://|ssh://git@|git@)[\w\-./:]+(?:[/\w\-.]+?)(?:\.git)?/?$"
)

# Temp directories for archive/git ingestion are kept alive for the lifetime of
# the process and cleaned up on shutdown by ``cleanup_temporary_repositories``.
# Plain paths are used instead of TemporaryDirectory finalizers because a failed
# git clone can leave transient file locks on Windows, which makes interpreter
# shutdown noisy when a finalizer retries deletion.
_TEMP_DIRS: Dict[str, str] = {}


def cleanup_temporary_repositories() -> None:
    for path in list(_TEMP_DIRS.values()):
        shutil.rmtree(path, ignore_errors=True)
    _TEMP_DIRS.clear()


class IngestionError(ValueError):
    """Raised when the supplied repository cannot be ingested."""


@dataclass
class IngestionResult:
    repository: Repository
    workspace: Path
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def detect_source_type(source: str) -> str:
    s = source.strip()
    if _GIT_RE.match(s):
        return "git"
    p = Path(s)
    if p.suffix.lower() in _ARCHIVE_SUFFIXES:
        return "archive"
    if p.is_dir():
        return "local"
    if p.is_file():
        return "archive"
    return "local"


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.infolist():
        dest = (target / member.filename).resolve()
        if not str(dest).startswith(str(target)):
            raise IngestionError(f"Unsafe path in archive: {member.filename}")
    archive.extractall(target)


def _safe_extract_tar(archive: tarfile.TarFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.getmembers():
        dest = (target / member.name).resolve()
        if not str(dest).startswith(str(target)):
            raise IngestionError(f"Unsafe path in archive: {member.name}")
    archive.extractall(target)


def _single_root(path: Path) -> Path:
    entries = [e for e in path.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return path


def _count_files(path: Path) -> int:
    count = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", ".venv", "venv", "__pycache__"}]
        count += len(files)
        if count > settings.max_repo_files:
            break
    return count


def ingest(source: str, name: Optional[str] = None) -> IngestionResult:
    """Validate and materialise a repository from path, archive or Git URL."""
    source_type = detect_source_type(source)
    warnings: List[str] = []
    tmp_dir: Optional[str] = None
    workspace: Path

    if source_type == "local":
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise IngestionError(f"Repository path is not a directory: {source}")
        workspace = path

    elif source_type == "archive":
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise IngestionError(f"Archive not found: {source}")
        if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
            raise IngestionError("Archive exceeds configured size limit")
        tmp_dir = tempfile.mkdtemp(dir=str(settings.workspace_root))
        extracted = Path(tmp_dir) / "extracted"
        extracted.mkdir()
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    _safe_extract_zip(zf, extracted)
            elif tarfile.is_tarfile(path):
                with tarfile.open(path) as tf:
                    _safe_extract_tar(tf, extracted)
            else:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                tmp_dir = None
                raise IngestionError(f"Unsupported archive format: {path.name}")
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
            raise IngestionError(f"Failed to extract archive: {exc}") from exc
        workspace = _single_root(extracted)

    elif source_type == "git":
        # Cloning is an ingestion step, not repository code execution. It needs
        # normal network access; sandbox proxies are applied only when running
        # commands from the cloned repository.
        tmp_dir = tempfile.mkdtemp(dir=str(settings.workspace_root))
        target = Path(tmp_dir) / "repo"
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--no-hardlinks", source, str(target)],
                capture_output=True, text=True, timeout=settings.command_timeout,
                env=os.environ.copy(),
            )
            if proc.returncode != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                tmp_dir = None
                raise IngestionError(f"git clone failed: {proc.stderr.strip()[:400]}")
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
            raise IngestionError("git clone timed out") from exc
        except FileNotFoundError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
            raise IngestionError("git executable not available") from exc
        workspace = target

    else:  # pragma: no cover - guarded by detect_source_type
        raise IngestionError(f"Unsupported source type: {source_type}")

    try:
        file_count = _count_files(workspace)
        if file_count > settings.max_repo_files:
            raise IngestionError(
                f"Repository has too many files ({file_count} > {settings.max_repo_files})"
            )
        if file_count == 0:
            raise IngestionError("Repository contains no analysable files")
    except IngestionError:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    repo_name = name or workspace.name or "repository"
    metadata: Dict[str, Any] = {
        "file_count": file_count,
        "source_type": source_type,
        "extracted": source_type != "local",
    }
    if tmp_dir is not None:
        # Keep temp directory alive for subsequent analysis runs.
        _TEMP_DIRS[str(source)] = tmp_dir
    repo = Repository(
        id=new_id("REPO"), name=repo_name, source_type=source_type,
        source=str(source), path=str(workspace), metadata=metadata,
    )
    return IngestionResult(repository=repo, workspace=workspace,
                           warnings=warnings, metadata=metadata)
