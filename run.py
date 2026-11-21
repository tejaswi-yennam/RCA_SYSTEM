"""Command-line entry point for the autonomous RCA system.

Examples
--------
    python run.py serve
    python run.py analyze sample-repos/cascading-failure-project
    python run.py evaluate
    python run.py report RUN-ID --output report.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import settings  # noqa: E402
from orchestration.pipeline import get_orchestrator  # noqa: E402
from storage.database import get_db  # noqa: E402


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


def cmd_analyze(args: argparse.Namespace) -> None:
    orchestrator = get_orchestrator()
    run = orchestrator.start(args.source, args.name)
    print(f"Started analysis {run.id}")
    last_stage = ""
    while True:
        current = get_db().get_analysis(run.id)
        if current.current_stage != last_stage:
            last_stage = current.current_stage
            print(f"[{current.progress:3d}%] {last_stage}")
        if current.status.value in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.8)
    final = get_db().get_analysis(run.id)
    print(json.dumps({
        "analysis_id": final.id, "status": final.status.value,
        "assessment": final.summary.get("assessment"),
        "summary": final.summary, "error": final.error,
    }, indent=2, default=str))
    if args.output:
        report = get_db().get_report(final.id, "markdown")
        if report:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"Report written to {args.output}")
    if final.status.value == "FAILED":
        raise SystemExit(1)


def cmd_evaluate(args: argparse.Namespace) -> None:
    from evaluation.framework import evaluate_all

    metrics = evaluate_all(Path(args.samples))
    print(json.dumps(metrics, indent=2, default=str))
    if args.output:
        Path(args.output).write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")


def cmd_report(args: argparse.Namespace) -> None:
    report = get_db().get_report(args.analysis_id, args.format)
    if report is None:
        print("Report not found", file=sys.stderr)
        raise SystemExit(1)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Written to {args.output}")
    else:
        print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the API + web UI")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    analyze = sub.add_parser("analyze", help="Analyse a repository")
    analyze.add_argument("source", help="Local path, archive or Git URL")
    analyze.add_argument("--name", default=None)
    analyze.add_argument("--output", default=None, help="Write Markdown report here")
    analyze.set_defaults(func=cmd_analyze)

    evaluate = sub.add_parser("evaluate", help="Run evaluation on sample repositories")
    evaluate.add_argument("--samples", default=str(Path(__file__).parent / "sample-repos"))
    evaluate.add_argument("--output", default=None)
    evaluate.set_defaults(func=cmd_evaluate)

    report = sub.add_parser("report", help="Print/download a saved report")
    report.add_argument("analysis_id")
    report.add_argument("--format", default="markdown", choices=["markdown", "json"])
    report.add_argument("--output", default=None)
    report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
