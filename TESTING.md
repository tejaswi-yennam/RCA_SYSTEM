# Testing

## System test suite

```bash
cd rca_system
python -m pip install -r requirements.txt
python -m pytest tests -q
```

Current result: **36 passed** (Python 3.13). Tests use temporary repositories and an isolated
`RCA_DATA_DIR`, so they do not touch production data.

| Test file | Coverage |
|---|---|
| `test_ingestion.py` | Source-type detection, local/archive ingestion, empty-repo rejection. |
| `test_repository_analyzer.py` | Profile detection, component discovery, dependency edges, parse errors. |
| `test_static_analyzer.py` | Syntax errors, missing env config, secret/eval pattern detection and redaction. |
| `test_dependency_analyzer.py` | Missing undeclared dependency, conflicting version specifiers. |
| `test_test_engine.py` | Framework detection, passing suite, failing test findings. |
| `test_dynamic_analyzer.py` | Generated probes, destructive function skip list. |
| `test_agent_and_evidence.py` | Evidence linking, agent hypothesis confirmation via deterministic experiment. |
| `test_correlation_rca.py` | Cascade grouping, RCA chain construction, graph/Mermaid output. |
| `test_fix_verification.py` | Specific patch generation, isolated patch application, verification outcome. |
| `test_reporting.py` | Redaction and all required report sections. |
| `test_api.py` | Health, repository/analysis endpoints, report endpoints, validation errors. |
| `test_security.py` | Sanitised env, sandbox copy isolation, command timeout, proxy restriction. |
| `test_evaluation_and_pipeline.py` | Full pipeline on cascading sample, evaluation framework bounds. |

## Sample repositories

Seven intentionally broken repositories live in `sample-repos/`. Regenerate them at any time:

```bash
python sample-repos/generate_samples.py
```

Each repo contains `expected_rca.json` with expected root-cause types, categories, file,
symptom count and fix expectations. They cover syntax, dependency, configuration, runtime,
data, integration and cascading failures.

## End-to-end evaluation

```bash
python run_evaluation.py
# or
python run.py evaluate
```

`run_evaluation.py` analyses every sample repo through the **real** pipeline, writes one
example Markdown report per repo to `docs/examples/`, and writes metrics to:

- `docs/EVALUATION.md`
- `docs/evaluation_results.json`

CLI analysis of a single repo:

```bash
python run.py analyze sample-repos/configuration-error-project --output report.md
python run.py report RUN-... --format json
```

## Metrics

The evaluation framework reports:

- **Detection precision/recall/F1** — contextual (expected categories plus observed failure
  categories) and strict (expected root categories only). Contextual precision is the primary
  signal because downstream test/dynamic findings are legitimate evidence; strict precision
  measures how many findings are specifically root-cause categories.
- **Root-cause identification rate** — did a chain point at the expected file/type/category?
- **Cascade accuracy** — were expected downstream symptoms grouped and linked?
- **Fix verification rate** — confirmed fixes divided by automatable fixes.
- **Evidence consistency** — fraction of RCA evidence IDs present in the evidence store.
- **Agent efficiency** — rejected/incorrect hypotheses and excessive hypothesis count.

See [docs/EVALUATION.md](docs/EVALUATION.md) for the latest actual numbers.

## Manual UI smoke test

1. `python run.py serve`
2. Open `http://127.0.0.1:8000`.
3. Enter `sample-repos/cascading-failure-project` and click **Start analysis**.
4. Watch live progress/logs; when complete, inspect Components, Tests, Issues, RCA, Graph,
   Fixes, Coverage and Logs tabs.
5. Download both Markdown and JSON reports.
