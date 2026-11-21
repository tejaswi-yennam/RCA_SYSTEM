# REST API

Base URL: `http://127.0.0.1:8000`. Interactive Swagger UI: `/docs`.
Start the server with `python run.py serve`.

Status codes: `200` success, `400` invalid repository/limits, `404` unknown analysis or report
not ready, `413` upload too large, `422` request validation failure.

The upload limit defaults to 1000 MB (`RCA_MAX_UPLOAD_MB`). For larger projects, ingest a
local path or Git URL via `POST /repositories` instead — those are not subject to the upload cap.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service status, version and whether an LLM is configured. |
| `POST` | `/repositories` | Validate/ingest a repository and persist it. Body: `{source, name?}`. |
| `GET` | `/repositories` | List persisted repositories. |
| `POST` | `/repositories/upload` | Upload an archive (`multipart/form-data`, field `file`, optional `name`). Returns repository metadata; use the returned `path` with `/analysis/start`. |
| `POST` | `/analysis/start` | Start a full analysis. Body: `{source, name?, wait?}`. Returns the run immediately unless `wait: true` (max 300s). |
| `GET` | `/analysis` | List all analysis runs. |
| `GET` | `/analysis/{id}/status` | Progress, stage, status, summary, capabilities and recent logs. |
| `GET` | `/analysis/{id}/components` | Discovered components. |
| `GET` | `/analysis/{id}/tests` | Executed and generated tests with errors/stack traces. |
| `GET` | `/analysis/{id}/issues` | All findings with severity/status/source. |
| `GET` | `/analysis/{id}/evidence` | Evidence records and confidence. |
| `GET` | `/analysis/{id}/hypotheses` | Agent hypotheses and verification notes. |
| `GET` | `/analysis/{id}/rca` | RCA chains, issue groups, fixes, verifications, regressions. |
| `GET` | `/analysis/{id}/report?format=markdown\|json` | Final report. Default `markdown`. |
| `GET` | `/analysis/{id}/report/download?format=markdown\|json` | Downloadable file response. |
| `GET` | `/analysis/{id}/logs?limit=500` | Persisted stage logs (max 2000). |
| `GET` | `/` | Web UI. |

## Examples

### Health

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"1.0.0","llm_configured":false}
```

### Analyse a local repository asynchronously

```bash
curl -X POST http://127.0.0.1:8000/analysis/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"sample-repos/cascading-failure-project"}'
# {"id":"RUN-...","status":"PENDING","current_stage":"queued", ...}
```

Poll until `status` is `COMPLETED` or `FAILED`:

```bash
curl http://127.0.0.1:8000/analysis/RUN-.../status
```

### Analyse synchronously

```bash
curl -X POST http://127.0.0.1:8000/analysis/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"sample-repos/syntax-error-project","wait":true}'
```

### Upload an archive

```bash
curl -X POST http://127.0.0.1:8000/repositories/upload \
  -F 'file=@myrepo.zip' -F 'name=myrepo'
```

Then use the returned `path` as `source` in `/analysis/start`.

### Get RCA and reports

```bash
curl http://127.0.0.1:8000/analysis/RUN-.../rca | python -m json.tool
curl http://127.0.0.1:8000/analysis/RUN-.../report?format=markdown
curl -OJ http://127.0.0.1:8000/analysis/RUN-.../report/download?format=json
```

### Git URL

```bash
curl -X POST http://127.0.0.1:8000/analysis/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"https://github.com/org/repo.git"}'
```

Git cloning requires network access from the API process; the sandbox restriction applies
to repository code execution, not to the ingestion step.

## Response shapes

`Finding` includes `id`, `type`, `severity`, `category`, `file`, `line`, `component`,
`message`, `evidence`, `reproducible`, `source`, `status`, `confidence`, `stack_trace`,
`metadata` and a computed `location`.

`RCAChain` includes `symptom`, `immediate_cause`, `contributing_factors`, `root_cause`,
`root_cause_finding_id`, `downstream_impact`, `evidence_ids`, `confidence`, `status` and
`verified`.

The JSON report additionally includes `root_cause_graph`, a `mermaid` string, all typed
items, `missing_test_coverage`, `capabilities` and the full Markdown report in
`report_markdown`.
