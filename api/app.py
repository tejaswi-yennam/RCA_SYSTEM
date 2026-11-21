"""REST API for the autonomous RCA system."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from config.settings import settings
from ingestion.ingestor import IngestionError
from models.domain import AnalysisStatus
from orchestration.pipeline import get_orchestrator
from storage.database import get_db

app = FastAPI(
    title="Autonomous Repository Testing & RCA API",
    version="1.0.0",
    description="Evidence-driven hybrid (deterministic + AI agent) repository testing and root cause analysis.",
)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class RepositoryRequest(BaseModel):
    source: str = Field(..., description="Local path, archive path or Git URL")
    name: Optional[str] = Field(None, description="Optional friendly repository name")

    @field_validator("source")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("source must not be empty")
        return value.strip()


class AnalysisRequest(BaseModel):
    source: str = Field(..., description="Local path, archive path or Git URL")
    name: Optional[str] = None
    wait: bool = Field(False, description="Wait for completion before responding (max 300s)")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": "1.0.0", "llm_configured": settings.llm_configured}


@app.post("/repositories")
def create_repository(request: RepositoryRequest) -> Dict[str, Any]:
    from ingestion.ingestor import ingest

    try:
        result = ingest(request.source, request.name)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db = get_db()
    db.create_repository(result.repository)
    return result.repository.to_dict()


@app.get("/repositories")
def list_repositories() -> List[Dict[str, Any]]:
    return [r.to_dict() for r in get_db().list_repositories()]


@app.post("/analysis/start")
def start_analysis(request: AnalysisRequest) -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    try:
        analysis = orchestrator.start(request.source, request.name)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.wait:
        completed = orchestrator.wait(analysis.id, timeout=300)
        if completed:
            return completed.to_dict()
    return analysis.to_dict()


@app.get("/analysis/{analysis_id}/status")
def analysis_status(analysis_id: str) -> Dict[str, Any]:
    analysis = get_db().get_analysis(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    data = analysis.to_dict()
    data["logs"] = get_db().get_logs(analysis_id, limit=60)[::-1]
    return data


@app.get("/analysis/{analysis_id}/components")
def get_components(analysis_id: str) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_items(analysis_id, "components")


@app.get("/analysis/{analysis_id}/tests")
def get_tests(analysis_id: str) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_items(analysis_id, "tests")


@app.get("/analysis/{analysis_id}/issues")
def get_issues(analysis_id: str) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_items(analysis_id, "findings")


@app.get("/analysis/{analysis_id}/evidence")
def get_evidence(analysis_id: str) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_items(analysis_id, "evidence")


@app.get("/analysis/{analysis_id}/hypotheses")
def get_hypotheses(analysis_id: str) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_items(analysis_id, "hypotheses")


@app.get("/analysis/{analysis_id}/rca")
def get_rca(analysis_id: str) -> Dict[str, Any]:
    _require_analysis(analysis_id)
    db = get_db()
    return {
        "rca_chains": db.get_items(analysis_id, "rcas"),
        "issue_groups": db.get_items(analysis_id, "groups"),
        "fixes": db.get_items(analysis_id, "fixes"),
        "verifications": db.get_items(analysis_id, "verifications"),
        "regressions": db.get_items(analysis_id, "regressions"),
    }


@app.get("/analysis/{analysis_id}/report")
def get_report(analysis_id: str, format: str = "markdown") -> Any:
    _require_analysis(analysis_id)
    content = get_db().get_report(analysis_id, "json" if format == "json" else "markdown")
    if content is None:
        raise HTTPException(status_code=404, detail="Report not yet available")
    if format == "json":
        try:
            import json

            return JSONResponse(json.loads(content))
        except Exception:
            return PlainTextResponse(content)
    return PlainTextResponse(content)


@app.get("/analysis/{analysis_id}/report/download")
def download_report(analysis_id: str, format: str = "markdown") -> Any:
    _require_analysis(analysis_id)
    content = get_db().get_report(analysis_id, "json" if format == "json" else "markdown")
    if content is None:
        raise HTTPException(status_code=404, detail="Report not yet available")
    target = settings.reports_dir / f"{analysis_id}.{'json' if format == 'json' else 'md'}"
    target.write_text(content, encoding="utf-8")
    return FileResponse(str(target), filename=target.name, media_type="text/plain")


@app.get("/analysis/{analysis_id}/logs")
def get_logs(analysis_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    _require_analysis(analysis_id)
    return get_db().get_logs(analysis_id, limit=min(limit, 2000))


@app.get("/analysis")
def list_analyses() -> List[Dict[str, Any]]:
    return [a.to_dict() for a in get_db().list_analyses()]


@app.post("/repositories/upload")
async def upload_repository(file: UploadFile = File(...), name: Optional[str] = Form(None)) -> Dict[str, Any]:
    limit_bytes = settings.max_upload_mb * 1024 * 1024
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > limit_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds size limit")

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "repo.zip").suffix
    path: Optional[Path] = None
    too_large = False
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                     dir=str(settings.workspace_root)) as tmp:
        path = Path(tmp.name)
        written = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > limit_bytes:
                too_large = True
                break
            tmp.write(chunk)
    if too_large:
        if path is not None:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Upload exceeds size limit")
    if path is None:  # pragma: no cover - NamedTemporaryFile always provides a path
        raise HTTPException(status_code=400, detail="Upload could not be staged")

    from ingestion.ingestor import ingest

    try:
        result = ingest(str(path), name or Path(file.filename or "repository").stem)
    except IngestionError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_db().create_repository(result.repository)
    return result.repository.to_dict()


def _require_analysis(analysis_id: str) -> None:
    if not get_db().get_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    target = UI_DIR / "index.html"
    if not target.exists():
        return HTMLResponse("<h1>UI not found</h1>", status_code=404)
    return FileResponse(str(target))


app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")
