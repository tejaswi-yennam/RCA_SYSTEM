from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from ingestion.ingestor import (
    IngestionError,
    cleanup_temporary_repositories,
    detect_source_type,
    ingest,
)


def test_detect_source_types(tmp_path: Path) -> None:
    assert detect_source_type("https://github.com/a/b.git") == "git"
    assert detect_source_type("git@github.com:a/b.git") == "git"
    assert detect_source_type(str(tmp_path)) == "local"
    archive = tmp_path / "repo.zip"
    archive.write_bytes(b"")
    assert detect_source_type(str(archive)) == "archive"


def test_ingest_local(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "README.md").write_text("hello", encoding="utf-8")
    result = ingest(str(repo), "myrepo")
    assert result.repository.source_type == "local"
    assert result.repository.name == "myrepo"
    assert result.metadata["file_count"] == 1
    assert result.workspace == repo.resolve()


def test_ingest_rejects_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(IngestionError):
        ingest(str(empty))


def test_ingest_archive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "project").mkdir(parents=True)
    (source / "project" / "app.py").write_text("print(1)\n", encoding="utf-8")
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source / "project" / "app.py", "project/app.py")
    result = ingest(str(archive))
    assert result.repository.source_type == "archive"
    assert (result.workspace / "app.py").exists()


def test_git_clone_uses_normal_network_env(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_clone(cmd, **kwargs):
        captured.update(kwargs)
        target = Path(cmd[-1])
        target.mkdir(parents=True)
        (target / "app.py").write_text("print(1)\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    monkeypatch.setattr("ingestion.ingestor.subprocess.run", fake_clone)
    try:
        result = ingest("https://github.com/example/repo.git", "git-test")
        assert result.repository.source_type == "git"
        assert "PATH" in captured["env"]
        # The sandbox sentinel must not be present during ingestion.
        assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in captured["env"]
    finally:
        cleanup_temporary_repositories()
