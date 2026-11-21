# Future improvements

Prioritised roadmap. Items are grouped by the value they add to correctness, safety and
usability.

## P0 — correctness and coverage

1. **Language servers per ecosystem** — integrate `pyright`/`mypy`, `tsc`, `go vet`,
   `cargo check`, `javac` to get real type/symbol diagnostics instead of heuristics.
2. **Broader dynamic probes** — generate fixtures/mocks for Node, Go and Java callables;
   reuse repository test helpers where safe.
3. **Cascade classifier** — learn from stack traces, import graphs and error fingerprints to
   distinguish root causes from symptoms without hardcoding failure types.
4. **Full regression baseline** — run the unpatched suite twice to identify flaky tests
   before comparing fix results.
5. **Evaluation expansion** — add 20+ repositories (multi-language, flaky tests, security
   issues, performance regressions) and report confidence intervals.

## P1 — safety and operations

6. **Container sandbox** — optional Docker/Firecracker runner with network namespace disabled,
   read-only root filesystem, cgroup CPU/memory limits and seccomp profile.
7. **Job queue** — Celery/RQ/Temporal workers with per-job resource quotas, cancellation,
   retry policies and durable progress events.
8. **PostgreSQL storage** — move from SQLite to a shared DB with migrations (Alembic).
9. **Secret scanning and redaction v2** — use entropy scoring and provider-specific patterns;
   never persist raw stdout containing potential credentials.
10. **Audit trail** — sign reports and evidence records, record tool versions and hashes of
    analysed source trees.

## P2 — agent and RCA quality

11. **Tool-use trace in UI** — render agent decisions, tool calls and rejected hypotheses as
    a timeline with evidence links.
12. **Multi-agent roles** — separate explorer, test designer, RCA analyst and reviewer agents
    with cross-checking.
13. **Counterfactual verification** — automatically mutate candidate root causes and confirm
    symptom disappearance without mutating the original repository.
14. **Mermaid rendering in UI** — add a lightweight Mermaid viewer for the generated graph.
15. **Feedback loop** — let users mark conclusions correct/incorrect and use those labels to
    tune ranking and evaluation thresholds.

## P3 — developer experience

16. **Plugin architecture** — entry points for custom ingesters, analysers, test runners and
    fix engines without editing core modules.
17. **Incremental analysis** — hash files and re-run only affected components on repeat
    analyses.
18. **IDE/CI integrations** — GitHub Action, pre-commit hook and VS Code extension.
19. **Report templating** — HTML/PDF/SARIF exports for security and compliance workflows.
20. **Localisation and theming** — translated UI/report text and dark mode.

## Non-goals

- Automatically pushing fixes to remote repositories.
- Executing arbitrary repository code in the API process.
- Guaranteeing security for adversarial code without an external container/VM boundary.
