# Known limitations

This system is designed to be useful and safe, but it is not a substitute for a hardened
CI sandbox, a full language server, or human review.

## Sandboxing

- The subprocess environment is scrubbed and advisory `HTTP_PROXY`/`HTTPS_PROXY` loopback
  variables are set when `RCA_NETWORK_RESTRICTED=true`. This is **not** a real network
  namespace: determined code can bypass proxy-based restrictions with raw sockets.
- CPU/memory/file-size limits are applied through POSIX `resource` only (best effort on
  Windows). Timeouts and output caps apply everywhere.
- A malicious repository can still consume disk or CPU between samples, spawn subprocesses
  that outlive the parent on some platforms, or exploit an unpatched interpreter.
  For untrusted code, run the API inside a container/VM with no network and a read-only
  image, and enforce cgroup limits externally.
- Git ingestion performs a network clone before sandboxing; only the resulting files are
  executed in copies.

## Language and framework support

- Deep AST analysis is Python-specific. JavaScript/TypeScript, Java, Go, Rust and others get
  profile detection, generic component extraction, import edges and framework-aware test
  execution, but not undefined-symbol or unused-symbol analysis.
- Test detection covers common ecosystems (pytest/unittest/Jest/Mocha/Go/Cargo/Maven/Gradle).
  Custom runners need to be added to `test_engine/runner.py`.
- Generated dynamic probes execute Python callables only. They select small functions with
  simple signatures and skip names containing destructive terms (`delete`, `drop`, `remove`,
  `shutdown`, etc.), but the skip list is heuristic.
- Static import resolution uses installed packages and local module names; editable installs,
  namespace packages and dynamic imports can produce false positives/negatives.

## Analysis accuracy

- The offline agent uses transparent rules rather than general reasoning. It is strong for
  the included failure classes but may miss novel multi-hop causal chains.
- Optional LLM enrichment improves phrasing/correlation when configured, but all conclusions
  still require deterministic verification. LLM calls can fail or be unavailable; the system
  degrades to offline mode.
- Confidence values are calibrated heuristics, not probabilities.
- Evaluation `detection_precision` has a contextual and a strict variant because reporting a
  downstream test failure is legitimate evidence even though it is not the root category.
- Duplicate detection intentionally avoids collapsing distinct test failures; this can leave
  multiple evidence findings for one cascade.

## Fixes and verification

- Automatable patches are narrow: missing env vars, optional dependency declaration and a
  few additive changes. Most fixes are recommendations only.
- Verification compares failing test names before and after a patch in a fresh workspace.
  Tests that were not collected in the baseline may hide regressions.
- Applying a recommended patch still requires human review; side effects are reported but
  cannot be exhaustively predicted.
- Patches are never applied to the original repository, only to temporary copies.

## Scale and persistence

- Ingestion limits default to 1000 MB uploads / 100,000 files. Larger monorepos can be
  ingested by local path or Git URL (no upload cap) or tuned further with
  `RCA_MAX_UPLOAD_MB` / `RCA_MAX_REPO_FILES`.
- Analysis runs in a single background thread per run; there is no distributed queue.
- SQLite is suitable for local/single-node use. Replace with PostgreSQL for multi-worker
  production deployments.
- Reports truncate very long component/test/evidence tables (80/200/80 rows) for readability;
  complete data remains available via the typed-item API endpoints.

## UI

- The dashboard polls every 1.5s and is intended for interactive use, not high-frequency
  monitoring of thousands of concurrent runs.
- Mermaid is emitted as source in the Markdown report; the UI currently shows a textual
  causal tree rather than rendering the diagram.
