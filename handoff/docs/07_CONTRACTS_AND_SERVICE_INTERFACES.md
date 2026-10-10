# Contracts and service interfaces

## 1. Authority and compatibility

`contracts/sdlc.schema.json` is the machine authority for external field names and basic shapes. This document specifies semantic constraints that JSON Schema cannot express. `contracts/state.sql` is the initial SQLite persistence design, not a substitute for service authorization. The operation registry adds operation-specific parameter/output schemas. Schema version `1.0` is local to this proposed design; it does not assert an existing released API.

Reject unsupported major schema versions with a structured finding. For the first release, use exact version matches; add explicit tested migrations later. Unknown properties are rejected at fixed contract boundaries. `parameters` and event `details` are intentionally extensible only through a registered operation/event schema. Preserve rejected payloads as redacted diagnostics. One bounded model repair may correct malformed structured output; it may not change pinned input identity or hide missing facts.

Identifiers are opaque stable strings. Timestamps are UTC RFC 3339 strings. Hashes are lowercase SHA-256 of actual payload bytes. Hash sealed JSON manifests using UTF-8, lexicographically sorted object keys, compact separators, Unicode preserved, no NaN and no trailing newline. Manifest schemas avoid floating point values; this design's canonicalization is deliberately narrower than a general cross-language canonical JSON standard. Store and transport those exact canonical bytes; do not hash a pretty-printed reserialization.

## 2. Core records

| Record | Meaning and additional invariant |
|---|---|
| `ArtifactRef` | Exact artifact ID, revision and SHA-256; never resolve to latest implicitly |
| `Artifact` | Immutable payload metadata; owner, provenance, kind and relative path; hash/size checked on ingest |
| `Task` | Immutable execution intent, team, operation/version, input deliveries/refs, task dependencies and limits; current state is a projection |
| `TaskResult` | Append-only final outcome of a particular attempt, artifact refs, findings, questions and normalized error where applicable |
| `HandoffBundle` | Sealed exhaustive artifact manifest, source and destination teams, baseline, input bundles, findings, decisions and next actions |
| `Delivery` | Immutable transport envelope for one bundle and destination, exact read grants and target-baseline precondition |
| `DeliveryReceipt` | Append-only event establishing pending/accepted/rejected/stale status; latest event drives the projection |
| `BackendEvent` | Ordered attempt-local event from normalized adapter output; bounded/redacted details with large logs as artifacts |
| `NormalizedError` | Provider-independent category, retry metadata, completion certainty and recommended next action |
| `Checkpoint` | Runtime-private recovery snapshot, pinned inputs, workspace candidate, side-effect receipts and last observed event |
| `DiagramRequest` | Student/agent request with purpose, scope, diagram type and exact source references |

An immutable Delivery has **no mutable status field**. `DeliveryReceipt` and the SQL projection hold status; this prevents a receipt update from changing the published envelope's identity. Likewise, task states and artifact applicability are projections backed by events.

Artifact ownership is a team, except participant-owned ingress documents. `source_team: participant` is allowed only for validated manual ingress, never as a way for a team to impersonate a user. Bundle source/destination must agree with the delivery envelope. The producer must be authorized for every artifact in the manifest. `read_grants` is the full exact manifest set for this version; a task can request a subset. Every `input_refs` item must be granted by an accepted declared input delivery or be a same-team output explicitly bound from a completed dependency task. The latter is team-internal reuse, not cross-team access.

`Task.backend_profile` is the initial requested profile. An authorized resume override is stored in a new attempt with an immutable resolved configuration hash, account binding and quota scope; it does not mutate the Task. A changed binding starts a fresh provider session.

`Task.depends_on` contains same-team tasks only. Materialize dependency outputs and freeze a successor task intent before its first attempt when outputs were unknown during planning. Never mutate the inputs of a running task. A leader's plan is a proposal until this binding succeeds. Parameters containing file paths are resolved only against the granted view or owned workspace; they cannot expand authorization.

## 3. Gate and routing semantics

Register these first-release routes: participant to any named team for explicit manual input; Planning to Requirements; Requirements to Design and Testing; Design to Implementation; Implementation to Testing; Testing to Maintenance; Testing to Implementation for defects; downstream team to an earlier owner for clarification/change; and that owner back to the requesting team for an answer. A feedback route is a typed request/response package and does not authorize skipping revised baseline gates. Project policy may further restrict routes. Arbitrary broadcast to every team is disallowed.

Receipt acceptance means integrity, routing, authorization and baseline preconditions passed. An engineering decision separately approves/rejects/requires changes to exact artifacts. A decision records actor kind (participant/policy), actor ID, policy revision if applicable, scope, exact bundle/artifact digests, rationale and timestamp. No model text can create a participant decision. A policy decision is generated by deterministic gate evaluation under a policy already selected by the participant.

`HandoffBundle.baseline_id` is the sender's descriptive/logical baseline label. `Delivery.expected_target_baseline` is instead the **bundle ID** currently accepted as the target team's baseline (SQL `team_baselines.baseline_bundle_id`), or null when none exists. Never compare it with the sender's label. Accepting a new team baseline uses CAS against that current target baseline bundle ID. If it changed, record a stale delivery/decision and request reconciliation. Do not implement last-writer-wins. A current baseline may reference only an accepted decision and valid receipt. Transport acceptance does not make a diagnostic failing candidate release-ready.

## 4. Application service API

These are in-process typed methods for the first release. An optional MCP facade must call the same methods under the same principal and return resource references rather than raw credentials or unrestricted paths. No HTTP server is required.

| Service operation | Request / response | Main checks |
|---|---|---|
| `create_project` | Name, policy/profile refs → project ID and config location | Unique project and validated configuration |
| `import_material` | Project, target team, local file handle, declared purpose → original/extraction artifacts and ingress delivery | Type, size, safe extraction, provenance |
| `propose_stage_plan` | Team, accepted delivery IDs, objective → proposal reference | Team-scoped view; bounded capabilities |
| `approve_plan` | Proposal digest, expected revision, actor decision → task graph ID | Acyclic, registered operations, budget, authority |
| `run_ready` | Project/team, optional task ID → run ID | Leases, pinned inputs, capability probe, policy |
| `get_status` | Project/team/task selector → states, blocked reasons, next actions | Read scope; no secret/session contents |
| `review_artifact` | Exact refs, expected revision, actor, disposition → decision ID | No implicit latest; actor provenance |
| `publish_handoff` | Team, candidate manifest, decision refs, idempotency key → BundleRef | Complete manifest, grants, evidence/gates |
| `deliver_handoff` | BundleRef, expected target baseline, idempotency key → Delivery ID | Allowed route, exact target, deduplication |
| `request_diagram` | DiagramRequest → task ID | Source grant, renderer capability, scope |
| `resume_task` | Task ID, dry-run flag, selected profile if changed → plan or attempt ID | Reconcile prior liveness/side effects; stable input hashes |
| `take_over` / `return_work` | Task ID and expected workspace digest / imported candidate → ownership or revision | Exclusive writer; no silent human edit overwrite |
| `open_change` | Team target, affected refs, requested change → input delivery | Preserve old baseline; routed owner |
| `export_project` | Included teams/artifacts, destination → archive+manifest | No credentials; scoped history and hashes |

Use typed exceptions/results for service validation failures: invalid_schema, missing_input, denied_scope, stale_revision, conflict, unsupported_version and corrupt_artifact. These are runtime/service findings, not additional provider error categories. Do not map a stale delivery to provider_unavailable.

## 5. CLI surface to implement

All commands below are **proposed**, not available in this package. Use argument arrays internally and return nonzero status on invalid requests. Interactive rendering may be friendly, but `--json` must return a stable envelope with command, success, data and errors. Do not put progress prose into machine JSON output.

```text
sdlc init NAME --policy collaborative
sdlc connection add PROFILE --type codex-cli --auth native
sdlc doctor --project PROJECT --profile PROFILE
sdlc import --project PROJECT --team planning --file brief.pdf
sdlc stage plan --project PROJECT --team planning
sdlc stage run --project PROJECT --team planning
sdlc status --project PROJECT --json
sdlc review --bundle BUNDLE --decision accept --expected-revision REV
sdlc handoff deliver --bundle BUNDLE --expected-target-baseline BASELINE
sdlc diagram request --team requirements --type use_case --source ARTIFACT_REF --purpose TEXT
sdlc task resume TASK --dry-run
sdlc task resume TASK --profile APPROVED_PROFILE
sdlc task take-over TASK
sdlc task return-work TASK --snapshot ARCHIVE
sdlc change open --project PROJECT --team requirements --file CHANGE.md
sdlc export --project PROJECT --output project-export.zip
```

Omit `--expected-target-baseline` only when the service verifies there is no current target baseline. Typed CLI parsing must define how an ArtifactRef is entered, for example `ID@REV:SHA256`; it must not silently accept an unpinned bare filename.

## 6. Port interfaces and result handling

Use the following shapes as implementation guidance; complete types come from domain models and port modules, not copied vendor SDK classes.

```python
class Brain(Protocol):
    async def generate(self, request: BrainRequest) -> BrainReply: ...

class ResearchBackend(Protocol):
    async def retrieve(self, request: ResearchRequest) -> ResearchReply: ...

class CodingBackend(Protocol):
    async def start(self, job: CodingJob) -> AsyncIterator[BackendEvent]: ...
    async def resume(self, job: CodingJob, checkpoint: Checkpoint) -> AsyncIterator[BackendEvent]: ...
    async def cancel(self, attempt_id: str) -> CancelReceipt: ...

class ProcessRunner(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionRecord: ...
```

Requests carry principal, task/attempt IDs, operation scope, materialized view handles, capabilities, limits and schema where relevant. The runtime creates these, not the model. A BrainReply includes content/structured value, refusal/failure, provider metadata and nullable usage. A CodingBackend streams observations; it does not itself mark a task completed. A ProcessRunner record is trusted evidence only when issued by the configured collector and linked to the actual invocation.

Brain calls support provider-supported structured output where available plus local schema validation. Coding tools can edit a private workspace and return a patch/snapshot; native final text is merely one input to candidate review. Research is its own capability and cannot be assumed from a model name. Provider capability discovery is cached by profile/version and rechecked when changed.

## 7. Storage contract

The SQL file supplies explicit tables, keys and constraints. Services add transaction boundaries, route checks, ownership validation, CAS revision checks, migrations and immutable-file handling. Events have a stable stream and increasing sequence, not an assumed ordering from timestamps. Never store a raw API key in SQL, a task or an export. Store a credential reference or native auth mode; OS/CLI credential storage remains external.

`attempts` and `checkpoints` are runtime-private records. Each attempt binds task, ordinal, fence, backend profile/session reference, workspace, source baseline, process identity, status, last event and timestamps. Each checkpoint additionally records observed candidate digest, intermediate artifact refs, unresolved questions and side-effect receipts. A PID alone is insufficient for liveness; bind process start identity or supervisor/container identity to avoid PID reuse mistakes. Store provider sessions as opaque access-controlled metadata; never forward them in handoff bundles.

Use parameterized SQL. Migration files must have versions and checksums; back up before an incompatible migration and refuse newer databases. SQLite commit and filesystem publication have a documented crash boundary; see the recovery design. Large logs belong in immutable files with references, not unbounded JSON columns.

## 8. Examples and contract validation

The schema also defines `WorkerProposal`, `ResearchEvidence`, `RequirementsSet`, `DesignSet`, `TestPlan`, `TestBench`, `ExecutionRecord`, `Defect` and `ReleaseManifest`. Store these as JSON artifacts alongside the human-readable stage specifications. This separates deterministic fields from explanatory prose. Requirement IDs referenced by design/tests must exist in the pinned RequirementsSet; test case IDs in observations must exist in the pinned TestPlan. Release evidence must reference the exact released source, matching bench/environment and accepted requirements. These cross-record checks are service invariants.

Workers return `WorkerProposal`, never an authoritative TaskResult. Each ArtifactCandidate supplies exactly one of `content_text` or `workspace_path`; the runtime validates that exclusive choice, resolves paths inside the owned workspace, validates kind-specific content and assigns identity/hash/provenance. A candidate cannot claim to be an executed-test record: only the trusted collector creates authoritative ExecutionRecord artifacts. Models may draft test plans or summarize collected records.

`contracts/operations.json` enumerates the initial team operations and common parameter shape. The Task's `expected_output_schema` is the proposal shape; the operation's prerequisite policy and artifact-kind registry define the required actual deliverables. Implement these policies from the team specification before enabling an operation. A registry entry alone is not a working capability. Associate artifact kinds `requirements`, `design`, `test_plan`, `test_bench`, `execution_record`, `defect`, `release_manifest` and `research_evidence` with their corresponding schema definitions; Markdown companion documents use template/content checks. Renderer and process recipes add capability requirements beyond the proposal-generation capability.

`examples/minimal-cycle.json` is a deterministic six-team fixture containing embedded payloads, artifacts, bundles, deliveries and receipts. Its apparent progression demonstrates transport only: fixture-origin evidence must fail real release gates. `quota-task-result.json` is a recoverable paused result. `stale-delivery.json` uses a non-null expected target baseline bundle ID and must be rejected as stale when that baseline is not current. `stale-delivery.json` uses a non-null expected target baseline bundle ID and must be rejected as stale when that baseline is not current. `stale-delivery.json` uses a non-null expected target baseline bundle ID and must be rejected as stale when that baseline is not current. `diagram-request.json` demonstrates explicit diagram selection by a participant. `specification.md` is a human-readable input fixture.

The package validator checks the schema subset used by these examples, reference/hash consistency, route scope, SQL execution and package integrity. It is a design-package check, not the target application's test suite. Implementers should use a maintained Draft 2020-12 validator in the runtime and compile the same contracts into Python models without creating contradictory duplicate schemas.
