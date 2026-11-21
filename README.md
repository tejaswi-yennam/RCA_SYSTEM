# Autonomous Repository Testing & Root Cause Analysis (RCA) System

A complete, working hybrid system that accepts an arbitrary software repository and
automatically performs ingestion, structural understanding, deterministic analysis,
test execution, AI-agent investigation, root-cause analysis, fix recommendation,
fix verification, regression testing and report generation.

The system is **evidence-driven**: the AI agent consumes structured findings from the
deterministic engine, forms hypotheses, and verifies them deterministically before
labelling anything a confirmed root cause. It works fully offline (rule-based agent)
and optionally uses an OpenAI-compatible LLM for richer correlation.

---

## 1. Quick start

```bash
# From the repository root
python -m pip install -r requirements.txt

# Start API + web UI at http://127.0.0.1:8000
python run.py serve

# Analyse a repository from the CLI
python run.py analyze sample-repos/cascading-failure-project --output rca-report.md

# Run the system's own test suite
python -m pytest tests -q

# Regenerate the sample repositories and evaluation results
python sample-repos/generate_samples.py
python run_evaluation.py
```

Open <http://127.0.0.1:8000> for the web UI, or <http://127.0.0.1:8000/docs> for
interactive API documentation.

### Cloning from GitHub

```bash
git clone https://github.com/tejaswi-yennam/RCA_SYSTEM.git
cd RCA_SYSTEM
python -m pip install -r requirements.txt
python run.py serve
```

Public repositories can also be analysed without downloading them: paste a Git URL such as
`https://github.com/benjaminp/six.git` into the **Source** field of the web UI, or pass it to
`python run.py analyze <url>`.

---

## 2. What the system does

1. **Repository input** — local path, uploaded archive (`.zip`, `.tar.gz`, …) or Git URL.
2. **Ingestion** — validation, safe extraction, size/file limits, source-type detection.
3. **Understanding** — languages, frameworks, package managers, dependencies, config,
   test frameworks, entry points, external services, components, imports, dependency graph.
4. **Deterministic engine** — syntax checks, AST-based undefined/unused-name and import
   validation, configuration validation (including missing environment variables),
   security patterns, JSON/TOML validation, dependency/version checks.
5. **Testing engine** — auto-detects pytest, unittest, Jest, Mocha, Go, Cargo, Maven,
   Gradle; records JUnit-style results with error, stack trace, duration, expected/actual.
6. **Dynamic testing** — generates safe boundary/invalid-input probes (empty, null, 0,
   -1, `[]`, `{}`), skips destructive function names, executes in an isolated copy.
7. **AI agent engine** — consumes evidence, generates and verifies hypotheses, follows
   dependency links, produces cascade findings; optional LLM enrichment.
8. **RCA engine** — builds symptom → immediate cause → contributing factor → root cause
   → downstream impact chains, with confidence and evidence IDs.
9. **Correlation** — groups cascades, collapses duplicates, flags independent failures.
10. **Root cause graph** — machine-readable nodes/edges plus Mermaid rendering.
11. **Fix engine** — specific recommendations with reason, side effects, verification
    method, regression tests and (where safe) structured patches.
12. **Fix verification** — applies patches in a temporary workspace copy, re-runs the
    original and related tests; rejects fixes that do not resolve failures.
13. **Regression testing** — full suite after a confirmed fix; reports new failures.
14. **Reporting** — comprehensive Markdown + JSON report, downloadable from the UI/API.

---

## 3. Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full diagram and module responsibilities.

```
Repository → Ingestion → Understanding → Deterministic Engine ─┐
                                      ↘ Test/Dynamic Engines ───┤
                                                               ├→ Evidence → Agent → RCA
                                                               │              ↓
                                                               └←── Deterministic verification
                                                                      ↓
                                              Fix → Verify → Regression → Report
```

Modules: `ingestion/`, `repository_analyzer/`, `static_analyzer/`,
`dependency_analyzer/`, `test_engine/`, `dynamic_analyzer/`, `agent/`,
`orchestration/`, `evidence/`, `rca/`, `correlation/`, `graph/`, `verification/`,
`regression/`, `reporting/`, `api/`, `ui/`, `models/`, `config/`, `storage/`,
`sandbox/`, `evaluation/`, `sample-repos/`, `tests/`, `docs/`.

---

## 4. Configuration

All settings are environment variables prefixed `RCA_` (see `config/settings.py`):

| Variable | Default | Meaning |
|---|---:|---|
| `RCA_DATA_DIR` | `.rca_data` | Persistence root (SQLite + workspaces + reports) |
| `RCA_DB_PATH` | `<data_dir>/rca.db` | SQLite file |
| `RCA_COMMAND_TIMEOUT` | `300` | Seconds per executed command (incl. Git clone) |
| `RCA_MAX_CPU_SECONDS` | `120` | CPU limit (POSIX) |
| `RCA_MAX_MEMORY_MB` | `2048` | Address-space limit (POSIX) |
| `RCA_NETWORK_RESTRICTED` | `true` | Advisory proxy restrictions in sandbox |
| `RCA_LLM_BASE_URL` | empty | OpenAI-compatible endpoint (optional) |
| `RCA_LLM_API_KEY` | empty | API key (optional) |
| `RCA_LLM_MODEL` | `gpt-4o-mini` | Model name |
| `RCA_CONFIDENCE_THRESHOLD` | `0.65` | Minimum confidence for confirmed conclusions |
| `RCA_ENABLE_FIX_VERIFICATION` | `true` | Apply/reject fixes in isolated copies |
| `RCA_RUN_DYNAMIC_TESTS` | `true` | Generate boundary probes |
| `RCA_PYTEST_PLUGINS` | empty | Comma-separated opt-in pytest plugins |
| `RCA_MAX_UPLOAD_MB` | `1000` | Maximum uploaded archive size (1 GB) |
| `RCA_MAX_REPO_FILES` | `100000` | Maximum analysable files per repository |

Uploaded archives are capped at `RCA_MAX_UPLOAD_MB`; local paths and Git URLs bypass
that cap but still respect `RCA_MAX_REPO_FILES`.

Without `RCA_LLM_BASE_URL`/`RCA_LLM_API_KEY`, the agent uses its offline rule engine —
no external service is required.

---

## 5. API

See [API.md](API.md). Key endpoints:

```
POST /repositories
POST /repositories/upload
POST /analysis/start
GET  /analysis/{id}/status
GET  /analysis/{id}/components
GET  /analysis/{id}/tests
GET  /analysis/{id}/issues
GET  /analysis/{id}/rca
GET  /analysis/{id}/evidence
GET  /analysis/{id}/hypotheses
GET  /analysis/{id}/report?format=markdown|json
GET  /analysis/{id}/report/download
GET  /analysis/{id}/logs
```

---

## 6. Security model

Repository code is never executed in the user's original directory.

- Every execution stage uses a fresh temporary copy of the repository.
- Subprocesses run with a sanitised environment (credentials removed),
  advisory network-blocking proxies, timeouts and bounded output.
- POSIX runs apply CPU, memory, file-size and core-dump limits.
- Pytest plugin autoloading is disabled inside the sandbox; specific plugins can be
  opted into with `RCA_PYTEST_PLUGINS`.
- Archive extraction is protected against path traversal.
- Secret-like values are redacted from reports and logs.
- No destructive operations are performed on external systems.

A container/VM with a network namespace is recommended for untrusted repositories in
production (see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)).

---

## 7. Testing and evaluation

```bash
python -m pytest tests -q        # 38 system tests
python run_evaluation.py         # 7 faulty sample repos + metrics
```

Sample repositories with known expected RCAs:

```
sample-repos/
├── syntax-error-project/
├── dependency-error-project/
├── configuration-error-project/
├── runtime-error-project/
├── data-error-project/
├── integration-error-project/
└── cascading-failure-project/
```

Results: [docs/EVALUATION.md](docs/EVALUATION.md). Example report:
[docs/examples/cascading-failure-project-report.md](docs/examples/cascading-failure-project-report.md).
Testing details: [TESTING.md](TESTING.md).

---

## 8. Persistence

SQLite (`<data_dir>/rca.db`) stores repositories, analysis runs, components, tests,
findings, evidence, hypotheses, RCA chains, issue groups, fixes, verifications,
regressions, reports and logs. Multiple repositories and multiple runs per repository
are supported. All stage progress and observability logs are persisted and exposed by
the API.

---

## 9. Failure handling

Each pipeline stage is isolated. If static analysis, test execution, dynamic probing,
the agent, verification or reporting fails, the failure is recorded as a capability with
status `FAILED`, logged, and the remaining stages continue. The final report includes a
capability-status table so unavailable analysis is explicit.

---

## 10. Known limitations and future work

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and [FUTURE_IMPROVEMENTS.md](FUTURE_IMPROVEMENTS.md).

---

## 11. Public repository validation

The system was also validated end to end against public repositories, in addition to the
faulty sample repos used for measured evaluation. For example, `https://github.com/benjaminp/six.git`:

| Metric | Value |
|---|---:|
| Analysis status | `COMPLETED` |
| Components | 132 |
| Tests recorded | 213 |
| Assessment | `NOT READY` (no confirmed fault in this healthy upstream repo) |

The **213 tests** are the test-result records the system persisted for that run — not a number
taken from GitHub or from the repository's README. Breakdown:

- **201** came from the repository's own pytest suite. The pipeline runs
  `python -m pytest --junitxml=.rca_pytest.xml -q --no-header -p no:cacheprovider` inside an
  isolated workspace copy and parses one `TestResult` per `<testcase>` element in the JUnit XML
  (`test_engine/runner.py`, `_parse_junit`).
- **12** were synthetic boundary/invalid-input probes generated by the dynamic analyser and
  labelled with framework `generated-probe`.

Of the 213 recorded tests, 202 passed, 3 were skipped and 8 failed. The aggregate is computed as
`len(tests)` in `reporting/generator.py` after pytest results and generated probes are merged in
`orchestration/pipeline.py`. The same data is available through `GET /analysis/{id}/tests` or in
the JSON report.

Reproduce the run with:

```bash
python run.py analyze https://github.com/benjaminp/six.git
```

or paste the URL into the web UI's **Source** field.
