"""Domain models shared by every subsystem.

Models are plain dataclasses (no heavy dependencies) with a ``to_dict`` helper
so they serialise cleanly to JSON and into SQLite.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class IssueCategory(StrEnum):
    SYNTAX = "Syntax Error"
    LOGIC = "Logic Error"
    RUNTIME = "Runtime Error"
    TYPE = "Type Error"
    CONFIGURATION = "Configuration Error"
    DEPENDENCY = "Dependency Error"
    DATABASE = "Database Error"
    API = "API Error"
    INTEGRATION = "Integration Error"
    DATA = "Data Error"
    VALIDATION = "Validation Error"
    SECURITY = "Security Issue"
    PERFORMANCE = "Performance Issue"
    RESOURCE = "Resource Issue"
    BUILD = "Build Error"
    DEPLOYMENT = "Deployment Error"
    TEST_FAILURE = "Test Failure"
    MISSING_ERROR_HANDLING = "Missing Error Handling"
    MISSING_TEST_COVERAGE = "Missing Test Coverage"
    OTHER = "Other"


class FindingSource(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    DYNAMIC = "DYNAMIC"
    TEST = "TEST"
    AGENT = "AGENT"


class EvidenceStatus(StrEnum):
    CONFIRMED = "Confirmed"
    PROBABLE = "Probable"
    POTENTIAL = "Potential"
    UNVERIFIED = "Unverified"


class TestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CapabilityStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ComponentType(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    API = "api"
    SERVICE = "service"
    DATA_PROCESSOR = "data_processor"
    DATABASE = "database"
    CONFIG = "config"
    TEST = "test"
    ENTRY_POINT = "entry_point"
    WORKFLOW = "workflow"
    INFRASTRUCTURE = "infrastructure"


class EdgeKind(StrEnum):
    CAUSES = "causes"
    CONTRIBUTES_TO = "contributes_to"
    DEPENDS_ON = "depends_on"
    AFFECTS = "affects"
    REPRODUCES = "reproduces"
    VALIDATES = "validates"


import pathlib  # noqa: E402


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_encode(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _encode(asdict(value))
    return value


class JSONMixin:
    def to_dict(self) -> Dict[str, Any]:
        return _encode(asdict(self))  # type: ignore[arg-type]

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), indent=2, default=str)


@dataclass
class Repository(JSONMixin):
    id: str
    name: str
    source_type: str  # local | archive | git
    source: str
    path: str
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Component(JSONMixin):
    id: str
    name: str
    type: ComponentType
    path: str = ""
    language: str = ""
    symbols: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    line: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyEdge(JSONMixin):
    source: str
    target: str
    kind: EdgeKind = EdgeKind.DEPENDS_ON
    detail: str = ""


@dataclass
class RepositoryProfile(JSONMixin):
    project_types: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    package_managers: List[str] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    config_files: List[str] = field(default_factory=list)
    test_frameworks: List[str] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)
    external_services: List[str] = field(default_factory=list)
    database_connections: List[str] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0


@dataclass
class Finding(JSONMixin):
    id: str
    type: str
    severity: Severity
    category: IssueCategory
    file: str = ""
    line: Optional[int] = None
    component: str = ""
    message: str = ""
    evidence: List[str] = field(default_factory=list)
    reproducible: bool = False
    source: FindingSource = FindingSource.DETERMINISTIC
    status: EvidenceStatus = EvidenceStatus.POTENTIAL
    confidence: float = 0.5
    stack_trace: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.file or self.component or "unknown"


@dataclass
class TestResult(JSONMixin):
    id: str
    name: str
    component: str = ""
    status: TestStatus = TestStatus.PASS
    duration: float = 0.0
    error_message: str = ""
    stack_trace: str = ""
    test_input: str = ""
    expected: str = ""
    actual: str = ""
    framework: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence(JSONMixin):
    id: str
    finding: str
    items: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source: FindingSource = FindingSource.DETERMINISTIC
    status: EvidenceStatus = EvidenceStatus.POTENTIAL
    related_findings: List[str] = field(default_factory=list)
    related_tests: List[str] = field(default_factory=list)


@dataclass
class Hypothesis(JSONMixin):
    id: str
    statement: str
    status: str = "OPEN"  # OPEN | SUPPORTED | REJECTED | CONFIRMED
    confidence: float = 0.5
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    investigation_notes: List[str] = field(default_factory=list)
    root_cause_finding_ids: List[str] = field(default_factory=list)
    target_finding_ids: List[str] = field(default_factory=list)


@dataclass
class AgentDecision(JSONMixin):
    id: str
    iteration: int
    action: str
    rationale: str
    tool_calls: List[str] = field(default_factory=list)
    result: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class IssueGroup(JSONMixin):
    id: str
    root_issue_id: str = ""
    dependent_issue_ids: List[str] = field(default_factory=list)
    duplicate_issue_ids: List[str] = field(default_factory=list)
    independent: bool = False
    label: str = ""
    confidence: float = 0.0


@dataclass
class RCAChain(JSONMixin):
    id: str
    issue_id: str
    symptom: str
    immediate_cause: str
    root_cause: str
    contributing_factors: List[str] = field(default_factory=list)
    root_cause_finding_id: str = ""
    downstream_impact: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    status: EvidenceStatus = EvidenceStatus.POTENTIAL
    verified: bool = False


@dataclass
class GraphNode(JSONMixin):
    id: str
    label: str
    kind: str  # issue | test | component | hypothesis
    severity: str = ""
    source: str = ""
    status: str = ""


@dataclass
class GraphEdge(JSONMixin):
    source: str
    target: str
    kind: EdgeKind
    label: str = ""


@dataclass
class RootCauseGraph(JSONMixin):
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)


@dataclass
class FixRecommendation(JSONMixin):
    id: str
    issue_id: str
    problem: str
    root_cause: str
    affected_file: str
    affected_component: str
    required_change: str
    why_this_fix_works: str
    potential_side_effects: str
    required_additional_changes: List[str] = field(default_factory=list)
    verification_method: str = ""
    regression_tests: List[str] = field(default_factory=list)
    priority: int = 99
    automatable: bool = False
    patch: Optional[Dict[str, Any]] = None  # {"file": relative path, "replacements": [[old, new], ...]}


@dataclass
class VerificationResult(JSONMixin):
    id: str
    fix_id: str
    applied: bool = False
    original_failure_resolved: bool = False
    related_tests_passed: bool = False
    no_new_failures: bool = False
    outcome: str = "NOT_RUN"  # CONFIRMED | REJECTED | NOT_RUN | SKIPPED
    details: str = ""
    test_output: str = ""
    duration: float = 0.0


@dataclass
class RegressionResult(JSONMixin):
    id: str
    fix_id: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    new_failures: List[str] = field(default_factory=list)
    resolved_failures: List[str] = field(default_factory=list)
    details: str = ""


@dataclass
class CapabilityResult(JSONMixin):
    name: str
    status: CapabilityStatus
    detail: str = ""
    duration: float = 0.0


@dataclass
class AnalysisRun(JSONMixin):
    id: str
    repository_id: str
    status: AnalysisStatus = AnalysisStatus.PENDING
    progress: int = 0
    current_stage: str = "queued"
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    capabilities: List[CapabilityResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class LogEntry(JSONMixin):
    id: str
    analysis_id: str
    level: str
    stage: str
    message: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)
