"""Typed records mirroring ``contracts/sdlc.schema.json`` one-to-one.

Rules that keep these models honest (handoff docs/07):

* Every record forbids unknown properties and is immutable once constructed.
* Field names and enums are copied from the JSON Schema; a contract test checks parity.
* Timestamps stay strings (RFC 3339, UTC) so that canonical bytes and hashes never
  change through a parse/serialise round trip.
* Identifiers are opaque strings; hashes are lowercase SHA-256 of actual bytes.

The models are plain data. Authorization, hashing of payloads, and state changes live
in services, never here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from sdlc import SCHEMA_VERSION

# --------------------------------------------------------------------------- scalars

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
OPERATION_PATTERN = r"^(planning|requirements|design|implementation|testing|maintenance)\.[a-z][a-z0-9_]+$"
#: Relative POSIX path: no leading slash, no backslash, no ``..`` segment, no drive letter.
RELATIVE_PATH_PATTERN = r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.\.(?:/|$))(?![A-Za-z]:).+$"
#: RFC 3339 date-time (syntax only; the runtime treats these as UTC).
DATETIME_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)

Identifier = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
OperationName = Annotated[str, StringConstraints(pattern=OPERATION_PATTERN)]
RelativePath = Annotated[str, StringConstraints(min_length=1, pattern=RELATIVE_PATH_PATTERN)]
DateTime = Annotated[str, StringConstraints(pattern=DATETIME_PATTERN)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
SchemaVersion = Literal["1.0"]

# --------------------------------------------------------------------------- enums


class TeamId(StrEnum):
    PLANNING = "planning"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    MAINTENANCE = "maintenance"


class OwnerTeam(StrEnum):
    """A content owner: the six teams or validated participant ingress."""

    PARTICIPANT = "participant"
    PLANNING = "planning"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    MAINTENANCE = "maintenance"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ErrorCategory(StrEnum):
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTH_REQUIRED = "auth_required"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_OUTPUT = "invalid_output"
    CONTEXT_LIMIT = "context_limit"
    PERMISSION_REQUIRED = "permission_required"
    CAPABILITY_MISSING = "capability_missing"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CompletionCertainty(StrEnum):
    NOT_COMPLETED = "not_completed"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class Measurement(StrEnum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class Origin(StrEnum):
    PARTICIPANT = "participant"
    MODEL = "model"
    TOOL = "tool"
    IMPORT = "import"
    FIXTURE = "fixture"


class Outcome(StrEnum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Relation(StrEnum):
    DERIVES_FROM = "derives_from"
    SATISFIES = "satisfies"
    IMPLEMENTS = "implements"
    VERIFIES = "verifies"
    AFFECTS = "affects"


class BundleKind(StrEnum):
    FORWARD = "forward"
    DEFECT = "defect"
    CHANGE_REQUEST = "change_request"
    CLARIFICATION = "clarification"
    BASELINE = "baseline"
    MANUAL_INPUT = "manual_input"


class ReceiptStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE = "stale"


class EventKind(StrEnum):
    STARTED = "started"
    PROGRESS = "progress"
    ARTIFACT_CANDIDATE = "artifact_candidate"
    USAGE = "usage"
    PERMISSION_REQUEST = "permission_request"
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class SideEffectOutcome(StrEnum):
    CONFIRMED = "confirmed"
    NOT_PERFORMED = "not_performed"
    UNKNOWN = "unknown"


class RequestedBy(StrEnum):
    PARTICIPANT = "participant"
    AGENT = "agent"


class DiagramType(StrEnum):
    CONTEXT = "context"
    USE_CASE = "use_case"
    ACTIVITY = "activity"
    SEQUENCE = "sequence"
    STATE = "state"
    CLASS = "class"
    COMPONENT = "component"
    DEPLOYMENT = "deployment"
    ERD = "erd"
    DFD = "dfd"
    SCHEDULE = "schedule"
    CUSTOM = "custom"


class ClaimBasis(StrEnum):
    RETRIEVED = "retrieved"
    PARTICIPANT = "participant"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    SYNTHETIC = "synthetic"
    OBSERVED_EXECUTION = "observed_execution"


class RequirementKind(StrEnum):
    FUNCTIONAL = "functional"
    NONFUNCTIONAL = "nonfunctional"
    CONSTRAINT = "constraint"
    DOMAIN_RULE = "domain_rule"


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class VerificationMethod(StrEnum):
    AUTOMATED_TEST = "automated_test"
    INSPECTION = "inspection"
    ANALYSIS = "analysis"
    PARTICIPANT_EVALUATION = "participant_evaluation"


class DesignElementKind(StrEnum):
    COMPONENT = "component"
    INTERFACE = "interface"
    DATA = "data"
    BEHAVIOR = "behavior"
    DEPLOYMENT = "deployment"
    DECISION = "decision"


class TestLevel(StrEnum):
    UNIT = "unit"
    INTEGRATION = "integration"
    SYSTEM = "system"
    ACCEPTANCE = "acceptance"


class Automation(StrEnum):
    AUTOMATED = "automated"
    MANUAL = "manual"
    PLANNED = "planned"


class CaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"
    ERROR = "error"


class ExecutionStatus(StrEnum):
    PASSED = "passed"
    ASSERTION_FAILED = "assertion_failed"
    SETUP_FAILED = "setup_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    NOT_RUN = "not_run"


class DefectSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


# --------------------------------------------------------------------------- base


class Record(BaseModel):
    """Immutable, closed record.

    Type strictness (no "1" -> 1 coercion) is enforced by the JSON Schema layer in
    :mod:`sdlc.verification.schemas`; these models add the semantic rules.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=False,
        # The schema's relative-path pattern uses look-ahead, which pydantic's default
        # Rust regex engine does not support; Python's ``re`` matches the JSON Schema exactly.
        regex_engine="python-re",
    )

    def to_json_dict(self) -> dict[str, Any]:
        """Plain JSON-compatible dict with enums as strings; the input to canonical hashing."""
        return self.model_dump(mode="json")


# --------------------------------------------------------------------------- shared parts


class ArtifactRef(Record):
    """Exact artifact identity: id, revision and SHA-256. Never resolves to 'latest'."""

    artifact_id: Identifier
    revision: PositiveInt
    sha256: Sha256

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.artifact_id, self.revision, self.sha256)


class BundleRef(Record):
    bundle_id: Identifier
    sha256: Sha256


class Finding(Record):
    finding_id: Identifier
    code: NonEmpty
    severity: Severity
    message: NonEmpty
    related_refs: list[ArtifactRef]


class NormalizedError(Record):
    """Provider-independent failure description (handoff docs/05 s10, docs/06 s6)."""

    category: ErrorCategory
    provider_code: NonEmpty | None
    message: NonEmpty
    automatic_retry_now: bool
    retry_after_seconds: NonNegativeInt | None
    reset_at: DateTime | None
    completion_certainty: CompletionCertainty
    next_action: NonEmpty
    diagnostic_ref: ArtifactRef | None


class Usage(Record):
    input_tokens: NonNegativeInt | None
    output_tokens: NonNegativeInt | None
    cost_microunits: NonNegativeInt | None
    currency: NonEmpty | None
    measurement: Measurement


class TraceLink(Record):
    source_id: NonEmpty
    relation: Relation
    target_id: NonEmpty


class SideEffect(Record):
    operation_id: Identifier
    kind: NonEmpty
    external_id: NonEmpty | None
    evidence_ref: ArtifactRef | None
    outcome: SideEffectOutcome


# --------------------------------------------------------------------------- core records


class Artifact(Record):
    """Metadata for one immutable payload revision."""

    schema_version: SchemaVersion
    project_id: Identifier
    artifact_id: Identifier
    owner_team: OwnerTeam
    revision: PositiveInt
    kind: NonEmpty
    media_type: NonEmpty
    relative_path: RelativePath
    sha256: Sha256
    size_bytes: NonNegativeInt
    origin: Origin
    created_by_task: Identifier | None
    created_at: DateTime
    depends_on: list[ArtifactRef]

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(artifact_id=self.artifact_id, revision=self.revision, sha256=self.sha256)


class TaskLimits(Record):
    max_attempts: PositiveInt
    timeout_seconds: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_microunits: NonNegativeInt | None


class Task(Record):
    """Immutable execution intent. The current state is a runtime projection, not a field."""

    schema_version: SchemaVersion
    project_id: Identifier
    cycle_id: Identifier
    task_id: Identifier
    team_id: TeamId
    agent_instance_id: Identifier
    operation: OperationName
    operation_version: PositiveInt
    input_deliveries: list[Identifier]
    input_refs: list[ArtifactRef]
    depends_on: list[Identifier]
    parameters: dict[str, Any]
    expected_output_schema: NonEmpty
    backend_profile: Identifier
    workspace_ref: Identifier | None
    limits: TaskLimits

    @model_validator(mode="after")
    def _operation_belongs_to_team(self) -> "Task":
        if not self.operation.startswith(self.team_id.value + "."):
            raise ValueError(
                f"operation {self.operation!r} does not belong to team {self.team_id.value!r}"
            )
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        return self


class TaskResult(Record):
    schema_version: SchemaVersion
    task_id: Identifier
    attempt_id: Identifier
    outcome: Outcome
    artifact_refs: list[ArtifactRef]
    candidate_workspace_ref: Identifier | None
    findings: list[Finding]
    questions: list[NonEmpty]
    usage: Usage | None
    error: NormalizedError | None

    @model_validator(mode="after")
    def _error_matches_outcome(self) -> "TaskResult":
        if self.outcome == Outcome.COMPLETED and self.error is not None:
            raise ValueError("a completed result cannot carry a normalized error")
        return self


class HandoffBundle(Record):
    """Sealed, exhaustive artifact manifest crossing a team boundary."""

    schema_version: SchemaVersion
    bundle_id: Identifier
    project_id: Identifier
    cycle_id: Identifier
    source_team: OwnerTeam
    target_team: TeamId
    kind: BundleKind
    baseline_id: Identifier
    input_bundle_refs: list[BundleRef]
    artifact_refs: list[ArtifactRef] = Field(min_length=1)
    trace_links: list[TraceLink]
    summary: NonEmpty
    open_findings: list[Finding]
    decision_refs: list[Identifier]
    next_actions: list[NonEmpty]
    created_at: DateTime

    @model_validator(mode="after")
    def _distinct_teams(self) -> "HandoffBundle":
        if self.source_team.value == self.target_team.value:
            raise ValueError("a handoff must target a different team")
        if self.kind == BundleKind.MANUAL_INPUT and self.source_team != OwnerTeam.PARTICIPANT:
            raise ValueError("manual_input bundles must come from the participant")
        if self.kind != BundleKind.MANUAL_INPUT and self.source_team == OwnerTeam.PARTICIPANT:
            raise ValueError("the participant may only send manual_input bundles")
        keys = [ref.key for ref in self.artifact_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate artifact reference in bundle manifest")
        return self


class Delivery(Record):
    """Immutable transport envelope. Status lives in DeliveryReceipt, never here."""

    schema_version: SchemaVersion
    delivery_id: Identifier
    project_id: Identifier
    bundle_ref: BundleRef
    from_team: OwnerTeam
    to_team: TeamId
    #: The target team's currently accepted BUNDLE ID (or null = no baseline yet).
    #: Not HandoffBundle.baseline_id, which is the sender's logical label.
    expected_target_baseline: Identifier | None
    read_grants: list[ArtifactRef] = Field(min_length=1)
    created_at: DateTime

    @model_validator(mode="after")
    def _grants_unique(self) -> "Delivery":
        keys = [ref.key for ref in self.read_grants]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate read grant")
        if self.from_team.value == self.to_team.value:
            raise ValueError("a delivery must target a different team")
        return self


class DeliveryReceipt(Record):
    schema_version: SchemaVersion
    receipt_id: Identifier
    delivery_id: Identifier
    project_id: Identifier
    sequence: PositiveInt
    status: ReceiptStatus
    reason: NonEmpty | None
    recorded_at: DateTime


class BackendEvent(Record):
    schema_version: SchemaVersion
    event_id: Identifier
    project_id: Identifier
    task_id: Identifier
    attempt_id: Identifier
    team_id: TeamId
    sequence: PositiveInt
    kind: EventKind
    recorded_at: DateTime
    details: dict[str, Any]
    error: NormalizedError | None


class Checkpoint(Record):
    """Runtime-private recovery snapshot (handoff docs/06 s7)."""

    schema_version: SchemaVersion
    checkpoint_id: Identifier
    task_id: Identifier
    attempt_id: Identifier
    team_id: TeamId
    created_at: DateTime
    fencing_generation: PositiveInt
    task_graph_revision: PositiveInt
    input_deliveries: list[Identifier]
    input_refs: list[ArtifactRef]
    baseline_source_ref: ArtifactRef | None
    candidate_source_ref: ArtifactRef | None
    patch_ref: ArtifactRef | None
    intermediate_refs: list[ArtifactRef]
    last_event_id: Identifier | None
    backend_session_id: NonEmpty | None
    summary_ref: ArtifactRef
    outstanding_questions: list[NonEmpty]
    side_effects: list[SideEffect]
    reconciliation_required: bool

    @model_validator(mode="after")
    def _unknown_side_effects_force_reconciliation(self) -> "Checkpoint":
        if any(s.outcome == SideEffectOutcome.UNKNOWN for s in self.side_effects):
            if not self.reconciliation_required:
                raise ValueError("an unknown side-effect outcome requires reconciliation")
        return self


class DiagramRequest(Record):
    schema_version: SchemaVersion
    project_id: Identifier
    team_id: TeamId
    requested_by: RequestedBy
    source_refs: list[ArtifactRef] = Field(min_length=1)
    diagram_type: DiagramType
    purpose: NonEmpty
    scope_ids: list[NonEmpty]
    preferred_renderer: NonEmpty | None


# --------------------------------------------------------------------------- worker output


class ArtifactCandidate(Record):
    """A worker's proposed artifact: exactly one of content_text / workspace_path."""

    kind: NonEmpty
    media_type: NonEmpty
    relative_path: RelativePath
    content_text: str | None
    workspace_path: RelativePath | None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ArtifactCandidate":
        if (self.content_text is None) == (self.workspace_path is None):
            raise ValueError("candidate must supply exactly one of content_text or workspace_path")
        return self


class WorkerProposal(Record):
    """What a leader or worker returns. Never an authoritative TaskResult."""

    schema_version: SchemaVersion
    task_id: Identifier
    summary: NonEmpty
    artifact_candidates: list[ArtifactCandidate]
    findings: list[Finding]
    questions: list[NonEmpty]
    suggested_next_operations: list[NonEmpty]


# --------------------------------------------------------------------------- stage records


class EvidenceClaim(Record):
    claim_id: Identifier
    statement: NonEmpty
    basis: ClaimBasis
    source_refs: list[ArtifactRef]
    source_locators: list[NonEmpty]
    limitations: list[NonEmpty]


class ResearchEvidence(Record):
    schema_version: SchemaVersion
    query: NonEmpty
    retrieved_at: DateTime | None
    url: NonEmpty | None
    title: NonEmpty
    source_date: NonEmpty | None
    excerpts: list[NonEmpty]
    claims: list[EvidenceClaim]
    synthetic: bool
    generation_method: NonEmpty | None

    @model_validator(mode="after")
    def _synthetic_needs_method(self) -> "ResearchEvidence":
        if self.synthetic and self.generation_method is None:
            raise ValueError("synthetic evidence must record its generation_method")
        return self


class Requirement(Record):
    requirement_id: Identifier
    revision: PositiveInt
    kind: RequirementKind
    statement: NonEmpty
    rationale: NonEmpty
    priority: Priority
    source_claim_ids: list[Identifier]
    acceptance: list[NonEmpty] = Field(min_length=1)
    verification_method: VerificationMethod
    open_questions: list[NonEmpty]


class RequirementsSet(Record):
    schema_version: SchemaVersion
    scope: NonEmpty
    requirements: list[Requirement] = Field(min_length=1)
    claims: list[EvidenceClaim]
    assumptions: list[NonEmpty]

    @model_validator(mode="after")
    def _ids_resolve(self) -> "RequirementsSet":
        ids = [r.requirement_id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement_id")
        claim_ids = {c.claim_id for c in self.claims}
        for r in self.requirements:
            missing = set(r.source_claim_ids) - claim_ids
            if missing:
                raise ValueError(f"requirement {r.requirement_id} cites unknown claims {sorted(missing)}")
        return self


class DesignElement(Record):
    element_id: Identifier
    kind: DesignElementKind
    name: NonEmpty
    description: NonEmpty
    requirement_ids: list[Identifier]
    contract_refs: list[ArtifactRef]
    checks: list[NonEmpty]


class DesignSet(Record):
    schema_version: SchemaVersion
    requirements_ref: ArtifactRef
    elements: list[DesignElement] = Field(min_length=1)
    decisions: list[NonEmpty]
    open_questions: list[NonEmpty]


class TestCase(Record):
    case_id: Identifier
    requirement_ids: list[Identifier] = Field(min_length=1)
    level: TestLevel
    purpose: NonEmpty
    preconditions: list[NonEmpty]
    steps: list[NonEmpty] = Field(min_length=1)
    expected: list[NonEmpty] = Field(min_length=1)
    technique: NonEmpty
    automation: Automation
    recipe_id: Identifier | None


class TestPlan(Record):
    schema_version: SchemaVersion
    requirements_ref: ArtifactRef
    source_ref: ArtifactRef | None
    cases: list[TestCase] = Field(min_length=1)
    environment_requirements: list[NonEmpty]
    exit_criteria: list[NonEmpty]
    risks: list[NonEmpty]


class TestBench(Record):
    schema_version: SchemaVersion
    bench_id: Identifier
    plan_ref: ArtifactRef
    suite_snapshot_ref: ArtifactRef
    fixture_refs: list[ArtifactRef]
    recipe_ids: list[Identifier] = Field(min_length=1)
    environment_ref: ArtifactRef
    seed: NonNegativeInt | None
    clock_policy: NonEmpty
    cleanup_recipe_id: Identifier | None


class CaseObservation(Record):
    case_id: Identifier
    status: CaseStatus
    message: NonEmpty


class ExecutionRecord(Record):
    """Evidence written by the trusted process collector, never by a model."""

    schema_version: SchemaVersion
    execution_id: Identifier
    project_id: Identifier
    team_id: TeamId
    task_id: Identifier
    attempt_id: Identifier
    source_ref: ArtifactRef
    suite_ref: ArtifactRef | None
    environment_ref: ArtifactRef
    recipe_id: Identifier
    argv: list[NonEmpty] = Field(min_length=1)
    working_directory: NonEmpty
    environment_variable_names: list[NonEmpty]
    started_at: DateTime
    ended_at: DateTime | None
    status: ExecutionStatus
    exit_code: int | None
    stdout_ref: ArtifactRef | None
    stderr_ref: ArtifactRef | None
    report_refs: list[ArtifactRef]
    case_observations: list[CaseObservation]
    collector_id: Identifier
    fixture_mode: bool


class Defect(Record):
    schema_version: SchemaVersion
    defect_id: Identifier
    severity: DefectSeverity
    source_ref: ArtifactRef
    requirement_ids: list[Identifier]
    execution_refs: list[ArtifactRef] = Field(min_length=1)
    reproduction_steps: list[NonEmpty] = Field(min_length=1)
    expected: NonEmpty
    observed: NonEmpty
    suggested_owner: TeamId


class ReleaseManifest(Record):
    schema_version: SchemaVersion
    release_id: Identifier
    source_ref: ArtifactRef
    requirements_ref: ArtifactRef
    design_ref: ArtifactRef
    bench_refs: list[ArtifactRef]
    execution_refs: list[ArtifactRef] = Field(min_length=1)
    decision_refs: list[Identifier] = Field(min_length=1)
    known_issues: list[NonEmpty]
    runbook_ref: ArtifactRef
    rollback_ref: ArtifactRef


class CycleFixture(Record):
    """Package example envelope: embedded payloads plus records. Always fixture_mode."""

    schema_version: SchemaVersion
    fixture_mode: Literal[True]
    project_id: Identifier
    payloads: dict[str, str]
    artifacts: list[Artifact] = Field(min_length=1)
    bundles: list[HandoffBundle] = Field(min_length=1)
    deliveries: list[Delivery] = Field(min_length=1)
    receipts: list[DeliveryReceipt] = Field(min_length=1)
    tasks: list[Task]
    results: list[TaskResult]
    notes: NonEmpty


#: Name -> model, matching the ``$defs`` keys of the JSON Schema.
RECORD_TYPES: dict[str, type[Record]] = {
    "ArtifactRef": ArtifactRef,
    "BundleRef": BundleRef,
    "Finding": Finding,
    "NormalizedError": NormalizedError,
    "Usage": Usage,
    "Artifact": Artifact,
    "TaskLimits": TaskLimits,
    "Task": Task,
    "TaskResult": TaskResult,
    "TraceLink": TraceLink,
    "HandoffBundle": HandoffBundle,
    "Delivery": Delivery,
    "DeliveryReceipt": DeliveryReceipt,
    "BackendEvent": BackendEvent,
    "SideEffect": SideEffect,
    "Checkpoint": Checkpoint,
    "DiagramRequest": DiagramRequest,
    "ArtifactCandidate": ArtifactCandidate,
    "WorkerProposal": WorkerProposal,
    "EvidenceClaim": EvidenceClaim,
    "ResearchEvidence": ResearchEvidence,
    "Requirement": Requirement,
    "RequirementsSet": RequirementsSet,
    "DesignElement": DesignElement,
    "DesignSet": DesignSet,
    "TestCase": TestCase,
    "TestPlan": TestPlan,
    "TestBench": TestBench,
    "CaseObservation": CaseObservation,
    "ExecutionRecord": ExecutionRecord,
    "Defect": Defect,
    "ReleaseManifest": ReleaseManifest,
    "CycleFixture": CycleFixture,
}

#: Artifact kinds whose JSON payload must validate against a stage record (docs/07 s8).
ARTIFACT_KIND_SCHEMAS: dict[str, str] = {
    "requirements": "RequirementsSet",
    "design": "DesignSet",
    "test_plan": "TestPlan",
    "test_bench": "TestBench",
    "execution_record": "ExecutionRecord",
    "defect": "Defect",
    "release_manifest": "ReleaseManifest",
    "research_evidence": "ResearchEvidence",
}

__all__ = [name for name in RECORD_TYPES] + [
    "Record",
    "RECORD_TYPES",
    "ARTIFACT_KIND_SCHEMAS",
    "SCHEMA_VERSION",
    "TeamId",
    "OwnerTeam",
    "Origin",
    "Outcome",
    "BundleKind",
    "ReceiptStatus",
    "ErrorCategory",
    "EventKind",
    "Severity",
]
