from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_repositories_and_analysis_api(simple_python_repo: Path) -> None:
    response = client.post("/repositories", json={"source": str(simple_python_repo),
                                                  "name": "api-test"})
    assert response.status_code == 200
    repo = response.json()
    assert repo["name"] == "api-test"

    response = client.post("/analysis/start", json={"source": str(simple_python_repo),
                                                    "name": "api-test", "wait": True})
    assert response.status_code == 200
    run = response.json()
    assert run["status"] in ("COMPLETED", "FAILED")
    analysis_id = run["id"]

    for endpoint in ("status", "components", "tests", "issues", "rca", "logs"):
        r = client.get(f"/analysis/{analysis_id}/{endpoint}")
        assert r.status_code == 200, endpoint

    status = client.get(f"/analysis/{analysis_id}/status").json()
    assert "missing_test_coverage" in status["summary"]

    report = client.get(f"/analysis/{analysis_id}/report")
    assert report.status_code == 200
    assert "Executive Summary" in report.text

    json_report = client.get(f"/analysis/{analysis_id}/report?format=json")
    assert json_report.status_code == 200
    assert "assessment" in json_report.json()

    download = client.get(f"/analysis/{analysis_id}/report/download")
    assert download.status_code == 200


def test_validation_error() -> None:
    response = client.post("/repositories", json={"source": ""})
    assert response.status_code == 422


def test_upload_size_limit_is_enforced(monkeypatch) -> None:
    from config.settings import settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "max_upload_mb", 0)
    response = client.post(
        "/repositories/upload",
        files={"file": ("too-big.zip", b"x" * 1024, "application/zip")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Upload exceeds size limit"


def test_upload_archive(simple_python_repo: Path, tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in simple_python_repo.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(simple_python_repo))

    response = client.post(
        "/repositories/upload",
        files={"file": ("repo.zip", archive.read_bytes(), "application/zip")},
        data={"name": "uploaded-api-test"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "uploaded-api-test"
