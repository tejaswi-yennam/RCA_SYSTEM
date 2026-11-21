# Architecture

## Design goals

- **Repository-agnostic**: no hardcoded repository names, file layouts or failure signatures.
- **Hybrid**: deterministic engines produce evidence; the agent reasons over it; deterministic
  experiments verify agent hypotheses before conclusions are promoted.
- **Resilient**: every capability is isolated; failure of one stage does not abort the pipeline.
- **Safe**: user repositories are never modified; all execution happens in temporary copies.
- **Explainable**: findings, hypotheses, evidence IDs, RCA chains and capability status are
  persisted and reported.

## End-to-end flow

```
                    ┌──────────────────────── source: path | archive | Git URL
                    ▼
              [ Ingestion ] ──validate, extract, limits, safe paths
                    │
                    ▼
        [ Repository Understanding ] ──profile, components, import/dependency graph
                    │
       ┌────────────┼──────────────────────────┐
       ▼            ▼                          ▼
[ Static Engine ] [ Dependency Engine ]  [ Test Engine ]  [ Dynamic Probes ]
       │            │                          │                 │
       └────────────┴────────────┬─────────────┴─────────────────┘
                                  ▼
                          [ Evidence Store ]
                                  │
                                  ▼
                  [ AI Agent / Offline Rule Engine ]
                     hypothesize → inspect → verify deterministically
                                  │
                                  ▼
                  [ Correlation + RCA + Root Cause Graph ]
                                  │
                                  ▼
                  [ Fix Engine → Verify → Regression ]
                                  │
                                  ▼
                         [ Markdown + JSON Report ]
```

The orchestrator (`orchestration/pipeline.py`) runs these stages in a background thread,
persists progress after each stage, records logs and capability statuses, and writes both
report formats to SQLite.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `ingestion/` | Detect source type, clone Git URLs, extract archives safely, validate size/file limits, hold temporary clones until the run finishes. |
| `repository_analyzer/` | Detect languages/frameworks/manifests/config/test frameworks/entry points; build component inventory and import edges. Uses Python AST plus generic token heuristics for other languages. |
| `static_analyzer/` | Syntax checks, AST undefined/unused name checks, import resolution, config/env validation, JSON/TOML validation, security-pattern checks. |
| `dependency_analyzer/` | Undeclared dependencies, version conflicts, missing environment variables; normalises import names and respects local packages. |
| `test_engine/` | Detect frameworks (pytest/unittest/Jest/Mocha/Go/Cargo/Maven/Gradle), execute in sandbox copy, parse JUnit XML with console fallback, create reproducible test findings. |
| `dynamic_analyzer/` | Generate safe boundary/invalid-input probes for selected callables, skip destructive names, execute each probe with timeout and record failures. |
| `evidence/` | Link findings and tests to evidence records with confidence and status; expose evidence for agent and RCA. |
| `agent/llm.py` | Optional OpenAI-compatible LLM client. Falls back to a deterministic offline provider when unconfigured. |
| `agent/investigator.py` | Rule-based agent loop: rank candidate root causes, inspect source, run targeted deterministic experiments (e.g. supply a missing env var), accept/reject hypotheses, emit cascade findings. |
| `correlation/` | Collapse duplicates, group dependent failures, flag independent issues, avoid treating symptoms as roots. |
| `rca/` | Build symptom → immediate cause → contributing factors → root cause → downstream impact chains with confidence/status. |
| `graph/` | Structured `GraphNode`/`GraphEdge` model plus Mermaid rendering. |
| `verification/fix_engine.py` | Convert confirmed roots into specific fixes with rationale, side effects, verification method, regression tests, priority and bounded patches. |
| `verification/engine.py` | Apply patches in isolated copy, re-run tests, compare failing test names before/after, confirm or reject fix. |
| `regression/` | Run full suite after confirmed fixes, report newly failing and resolved tests. |
| `reporting/` | Generate the 11-section Markdown report and equivalent structured JSON; redact secret-like values. |
| `orchestration/` | Pipeline lifecycle, progress, capability isolation, persistence, stage logs. |
| `storage/` | SQLite schema and typed item helpers for repositories, analyses, components, tests, findings, evidence, hypotheses, groups, RCA chains, fixes, verifications, regressions, reports and logs. |
| `sandbox/` | Temp workspace copies, sanitised env, advisory network proxies, CPU/memory/output limits, timeouts. |
| `api/` | FastAPI REST endpoints and static UI hosting. |
| `ui/` | Vanilla JS dashboard with live progress, logs, tabs, badges and report downloads. |
| `evaluation/` | Compare reports against `expected_rca.json` files; compute precision/recall/F1, root-cause and cascade metrics, fix verification, evidence consistency. |
| `sample-repos/` | Generator plus seven intentionally broken repositories with known expected RCAs. |
| `tests/` | 36 automated tests covering every subsystem and the API. |

## Evidence model

A `Finding` is an observed issue with severity, category, source (`DETERMINISTIC`, `TEST`,
`DYNAMIC`, `AGENT`), confidence, status and metadata. A `TestResult` records reproducible
execution evidence. The `EvidenceStore` links these into `Evidence` records. Hypotheses
reference the finding IDs they explain and the symptom IDs they target. RCA chains reference
evidence IDs so every conclusion is traceable.

Confidence is derived from source reliability and verification:
- deterministic syntax/import findings: high (≈0.9–0.95)
- failing tests: high (≈0.97)
- dynamic probe failures: high (≈0.88)
- agent hypothesis confirmed by a targeted experiment: inherits evidence confidence
- unverified heuristics: `Potential`/`Probable`, never `Confirmed`

## Root cause vs symptom

The correlator excludes known cascade carriers (`CascadingFailure`, dynamic import failures
when stronger roots exist), uses agent-provided causal links, and checks dependency edges and
shared error tokens before grouping. Symptoms are attached to groups as
`dependent_issue_ids`; only the upstream finding becomes a root cause. The RCA engine emits
one chain per group, and the graph renders `causes` edges from root to symptoms.

## Persistence and concurrency

SQLite uses WAL mode and a lock-guarded connection. Each analysis has a unique ID; items are
stored with `(analysis_id, kind)` keys. The API starts runs in daemon threads and clients poll
`/analysis/{id}/status`. Multiple repositories and multiple analyses per repository are
supported. Reports are stored in both `markdown` and `json` formats.

## Failure isolation

Each stage is wrapped in a context manager that records `CapabilityResult(OK|FAILED)` and a
duration. Failures are logged and the pipeline continues. The final report includes a
Capability Status table, so unavailable analysis is explicit rather than silently omitted.

## Security boundaries

See [README.md](README.md#6-security-model) and
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). In short: original files are read-only inputs;
subcommands run in copies with scrubbed environment, command timeouts, output limits and
advisory proxy restrictions. Pytest plugin autoloading is disabled inside the sandbox.
