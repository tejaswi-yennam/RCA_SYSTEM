"""Orchestration layer wiring every subsystem into one resilient pipeline."""
from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from agent.investigator import AgentInvestigation, investigate
from config.settings import settings
from correlation.correlator import correlate
from dependency_analyzer.analyzer import analyze as analyze_dependencies
from dynamic_analyzer.analyzer import analyze as run_dynamic
from evidence.store import EvidenceStore
from graph.root_cause_graph import build_graph
from ingestion.ingestor import ingest
from models.domain import (
    AnalysisRun,
    AnalysisStatus,
    CapabilityResult,
    CapabilityStatus,
    Component,
    Finding,
    LogEntry,
    RCAChain,
    Repository,
    TestResult,
    new_id,
)
from rca.engine import build_rcas
from regression.runner import run_regression
from repository_analyzer.analyzer import analyze_repository
from reporting.generator import build_report
from sandbox.executor import cleanup_workspace, make_workspace
from static_analyzer.analyzer import analyze as analyze_static
from storage.database import Database, get_db
from test_engine.runner import detect_frameworks, run_tests
from verification.engine import verify_fix
from verification.fix_engine import recommend_fixes


class AnalysisOrchestrator:
    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or get_db()
        self._threads: Dict[str, threading.Thread] = {}
        self._results: Dict[str, Dict[str, Any]] = {}

    # ---------- lifecycle ----------
    def start(self, source: str, name: Optional[str] = None) -> AnalysisRun:
        ingestion = ingest(source, name)
        repo = ingestion.repository
        self.db.create_repository(repo)
        analysis = AnalysisRun(
            id=new_id("RUN"), repository_id=repo.id,
            status=AnalysisStatus.PENDING, current_stage="queued",
        )
        self.db.create_analysis(analysis)
        self._log(analysis.id, "INFO", "orchestration",
                  f"Repository ingested: {repo.name} ({ingestion.metadata['file_count']} files)")
        for warning in ingestion.warnings:
            self._log(analysis.id, "WARN", "ingestion", warning)

        thread = threading.Thread(
            target=self._execute,
            args=(analysis.id, ingestion, analysis),
            daemon=True,
            name=f"rca-{analysis.id}",
        )
        self._threads[analysis.id] = thread
        thread.start()
        return analysis

    def _execute(self, analysis_id: str, ingestion: Any, analysis: AnalysisRun) -> None:
        analysis.status = AnalysisStatus.RUNNING
        analysis.current_stage = "starting"
        self._save(analysis)
        execution_ws: Optional[Path] = None
        try:
            source_ws = Path(ingestion.workspace)
            execution_ws = make_workspace(source_ws, prefix="rca_exec_")
            findings: List[Finding] = []
            tests: List[TestResult] = []
            agent: Optional[AgentInvestigation] = None
            evidence_store = EvidenceStore()

            understanding = None
            with self._stage(analysis, "repository_understanding", 20):
                understanding = analyze_repository(source_ws)
                self.db.save_items(analysis_id, "components", understanding.components)
                self._log(analysis_id, "INFO", "repository_understanding",
                          f"Discovered {len(understanding.components)} components, "
                          f"{len(understanding.edges)} dependency edges; "
                          f"languages={understanding.profile.languages}")

            with self._stage(analysis, "static_analysis", 32):
                static_findings, details = analyze_static(source_ws, understanding)
                findings.extend(static_findings)
                self._stage_detail(analysis, "static_analysis", details)
                self._log(analysis_id, "INFO", "static_analysis",
                          f"{len(static_findings)} deterministic findings")

            with self._stage(analysis, "dependency_analysis", 40):
                dep_findings, dep_details = analyze_dependencies(
                    source_ws, understanding, static_findings
                )
                findings.extend(dep_findings)
                self._stage_detail(analysis, "dependency_analysis", dep_details)

            with self._stage(analysis, "test_execution", 52):
                frameworks = detect_frameworks(execution_ws, understanding)
                runs, test_findings = run_tests(execution_ws, understanding, frameworks)
                tests = [t for run in runs for t in run.tests]
                findings.extend(test_findings)
                for run in runs:
                    if not run.available:
                        self._log(analysis_id, "WARN", "test_execution",
                                  f"{run.framework} unavailable: {run.detail}")
                self.db.save_items(analysis_id, "tests", tests)
                self._log(analysis_id, "INFO", "test_execution",
                          f"{len(tests)} tests recorded across {len(runs)} framework(s)")

            with self._stage(analysis, "dynamic_analysis", 62):
                dynamic_tests, dynamic_findings, dyn_details = run_dynamic(
                    execution_ws, understanding, findings
                )
                tests.extend(dynamic_tests)
                findings.extend(dynamic_findings)
                self._stage_detail(analysis, "dynamic_analysis", dyn_details)
                self.db.save_items(analysis_id, "tests", tests)
                self._log(analysis_id, "INFO", "dynamic_analysis",
                          f"{len(dynamic_findings)} dynamic findings, "
                          f"{len(dynamic_tests)} generated tests")

            with self._stage(analysis, "evidence_aggregation", 68):
                for f in findings:
                    evidence_store.add_finding(f)
                for t in tests:
                    evidence_store.add_test(t)
                self.db.save_items(analysis_id, "findings", findings)
                self.db.save_items(analysis_id, "evidence", evidence_store.all())
                self._log(analysis_id, "INFO", "evidence", evidence_store.summarize().__str__())

            with self._stage(analysis, "agent_investigation", 76):
                agent = investigate(execution_ws, understanding, findings,
                                    tests, evidence_store)
                findings.extend(agent.findings)
                self.db.save_items(analysis_id, "hypotheses", agent.hypotheses)
                self.db.save_items(analysis_id, "findings", findings)
                self.db.save_items(analysis_id, "evidence", evidence_store.all())
                for decision in agent.decisions:
                    self.db.log(LogEntry(
                        id=new_id("LOG"), analysis_id=analysis_id,
                        level="INFO", stage="agent", message=f"{decision.action}: {decision.rationale[:300]}",
                        data={"tool_calls": decision.tool_calls, "result": decision.result},
                    ))
                self._log(analysis_id, "INFO", "agent", str(agent.summary))

            groups: List[Any] = []
            rcas: List[RCAChain] = []
            agent_links: Dict[str, List[str]] = {}
            for f in agent.findings:
                rid = (f.metadata or {}).get("root_finding_id")
                if rid:
                    agent_links[rid] = list((f.metadata or {}).get("linked_findings", []))
            with self._stage(analysis, "rca_and_correlation", 84):
                groups, assignment = correlate(findings, understanding, agent_links)
                rcas = build_rcas(groups, findings, evidence_store)
                self.db.save_items(analysis_id, "groups", groups)
                self.db.save_items(analysis_id, "rcas", rcas)
                self._log(analysis_id, "INFO", "rca",
                          f"{len(groups)} issue groups, {len(rcas)} RCA chains")

            graph = None
            fixes = []
            with self._stage(analysis, "graph_and_fixes", 89):
                graph = build_graph(findings, groups, rcas, understanding.components,
                                    understanding.edges, tests)
                roots = [f for f in findings if any(g.root_issue_id == f.id for g in groups)]
                fixes = recommend_fixes(roots, rcas, groups)
                self.db.save_items(analysis_id, "fixes", fixes)
                self._log(analysis_id, "INFO", "fixes", f"{len(fixes)} fix recommendations")

            verifications = []
            regressions = []
            with self._stage(analysis, "verification", 94):
                for fix in fixes:
                    ver = verify_fix(fix, execution_ws, tests)
                    verifications.append(ver)
                    reg = run_regression(fix, ver, execution_ws, tests)
                    regressions.append(reg)
                self.db.save_items(analysis_id, "verifications", verifications)
                self.db.save_items(analysis_id, "regressions", regressions)
                self._log(analysis_id, "INFO", "verification",
                          f"{sum(1 for v in verifications if v.outcome == 'CONFIRMED')} fix(es) confirmed")

            with self._stage(analysis, "reporting", 99):
                analysis.finished_at = time.time()
                missing = self._missing_coverage(understanding, tests)
                report = build_report(
                    analysis, understanding.profile, understanding.components,
                    tests, findings, groups, rcas, graph, fixes,
                    verifications, regressions,
                    [e.to_dict() for e in evidence_store.all()], missing,
                )
                self.db.save_report(analysis_id, report["markdown"], "markdown")
                self.db.save_report(analysis_id, report["json"], "json")
                analysis.summary = {
                    **report["summary"],
                    "assessment": report["assessment"],
                    "agent": agent.summary if agent else {},
                    "missing_test_coverage": missing,
                }
                self._log(analysis_id, "INFO", "reporting",
                          f"Report generated: {report['assessment']}")

        except Exception as exc:  # top-level failure handling
            analysis.status = AnalysisStatus.FAILED
            analysis.error = f"{type(exc).__name__}: {exc}"[:800]
            self._log(analysis_id, "ERROR", "orchestration",
                      f"Pipeline failed: {traceback.format_exc()[-1500:]}")
        finally:
            if execution_ws:
                cleanup_workspace(execution_ws)
            analysis.status = (AnalysisStatus.COMPLETED
                               if analysis.status != AnalysisStatus.FAILED
                               else AnalysisStatus.FAILED)
            analysis.progress = 100
            analysis.current_stage = "completed" if analysis.status == AnalysisStatus.COMPLETED else "failed"
            analysis.finished_at = time.time()
            self._save(analysis)
            self._results[analysis_id] = {
                "analysis": analysis.to_dict(),
                "summary": analysis.summary,
            }
            self._log(analysis_id, "INFO", "orchestration",
                      f"Analysis {analysis_id} finished with status {analysis.status.value}")

    # ---------- helpers ----------
    @contextmanager
    def _stage(self, analysis: AnalysisRun, name: str, progress: int) -> Iterator[None]:
        start = time.time()
        analysis.current_stage = name
        analysis.progress = progress
        self._save(analysis)
        status, detail = CapabilityStatus.OK, ""
        try:
            yield
        except Exception as exc:
            status = CapabilityStatus.FAILED
            detail = f"{type(exc).__name__}: {exc}"[:400]
            self._log(analysis.id, "ERROR", name,
                      f"Capability failed but pipeline continues: {detail}")
        finally:
            analysis.capabilities.append(CapabilityResult(
                name=name, status=status, detail=detail,
                duration=round(time.time() - start, 3),
            ))
            self._save(analysis)

    def _stage_detail(self, analysis: AnalysisRun, name: str, details: Dict[str, Any]) -> None:
        for cap in analysis.capabilities:
            if cap.name == name:
                failed = [f"{k}:{v.get('error')}" for k, v in details.items()
                          if isinstance(v, dict) and v.get("status") == "FAILED"]
                if failed:
                    cap.status = CapabilityStatus.FAILED
                cap.detail = str(details)[:500]
        self._save(analysis)

    def _save(self, analysis: AnalysisRun) -> None:
        self.db.update_analysis(analysis)

    def _log(self, analysis_id: str, level: str, stage: str, message: str) -> None:
        self.db.log(LogEntry(
            id=new_id("LOG"), analysis_id=analysis_id,
            level=level, stage=stage, message=message[:2000],
        ))

    @staticmethod
    def _missing_coverage(understanding: Any, tests: List[TestResult]) -> List[str]:
        missing: List[str] = []
        if not any(t.framework != "generated-probe" for t in tests):
            missing.append("No repository test suite was executed; only generated probes were used.")
        test_files = {
            str(getattr(t, "component", "")) for t in tests
        }
        for comp in understanding.components:
            if comp.type.value in ("module", "api", "service", "data_processor", "database"):
                if comp.path and not any(comp.path in tf or Path(comp.path).stem in tf for tf in test_files):
                    missing.append(f"No test observed for {comp.type.value} '{comp.name}' ({comp.path})")
        return missing[:40]

    def wait(self, analysis_id: str, timeout: float = 300) -> Optional[AnalysisRun]:
        thread = self._threads.get(analysis_id)
        if thread:
            thread.join(timeout=timeout)
        return self.db.get_analysis(analysis_id)


_orchestrator: Optional[AnalysisOrchestrator] = None


def get_orchestrator() -> AnalysisOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AnalysisOrchestrator()
    return _orchestrator
