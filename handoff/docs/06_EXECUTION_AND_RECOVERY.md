# Durable execution, interruption and recovery

This document specifies runtime behavior for the six isolated SDLC teams: `planning`, `requirements`, `design`, `implementation`, `testing`, and `maintenance`. It is an implementation contract, not evidence that the runtime already exists. The schema files are authoritative for wire-property names; algorithm names below describe behavior. Provider-specific authentication and process protocols belong in the integration design.

## 1. Execution boundary

The runtime is a deterministic coordinator, not a seventh conversational agent. It validates task graphs, grants capabilities, starts adapters, records events and routes published packages. A stage leader may propose tasks and synthesize results; it cannot bypass dependency checks, alter committed evidence, or write another team's state.

Each team has its own agent identities, conversations, memory, credentials references, inbox projection and repository snapshots. Workers may collaborate inside their team. Cross-team input consists exclusively of a validated immutable `HandoffBundle` carried by an immutable `Delivery` envelope. A task lists accepted `input_deliveries` and exact `ArtifactRef` entries in `input_refs`; the runtime grants only that subset and its explicitly declared dependency closure. Access to all project files simply because they share a parent directory is prohibited in strict isolation mode. Participant imports use the same ingress validation, with `participant` allowed as the source identity only at that boundary.

Model weights, tool implementations and an account connection may be reused. Provider conversation IDs and coding-backend sessions must never cross team boundaries. A later team needing clarification emits a new handoff containing a question or change request, with a delivery to the responsible team; it does not call that team's worker directly. Responses create new bundle versions. Rework never overwrites the baseline that earlier evidence describes.

## 2. Authoritative persistence

Use local SQLite metadata with foreign keys enabled, WAL journaling, `synchronous=FULL`, bounded busy timeouts and explicit migrations. Keep the database on a supported local filesystem; a network share is not a supported concurrency mechanism. Use immutable content files for documents, diagrams, source archives, execution logs and large backend events. Git is useful for coding history but is not the authoritative workflow journal.

The storage layer needs tables or equivalent projections for projects, team runs, tasks, dependencies, attempts, leases, artifact metadata, bundles, deliveries, backend events, task results, outbox entries and decisions. Projections may change through versioned transactions; published `Artifact`, `HandoffBundle`, `Delivery`, `TaskResult` and `BackendEvent` records are append-only. Corrections reference previous records. Redacted operational logs are separate from provider credentials.

Every mutation checks the expected revision with compare-and-swap (CAS). All stored timestamps use UTC; a monotonic clock governs active-process timeouts. Ordered event sequence numbers, not wall-clock ordering, settle event order. Task inputs bind artifact digests and bundle versions before execution. A readable filename is never sufficient to identify the input.

Workers cannot access the control database directly. Runtime methods enforce team scope and validate references. The coordinator may inspect all metadata for scheduling and recovery; that privileged access does not become model context.

## 3. Task state machine

Only the runtime changes `Task` state. Each execution has a distinct attempt identifier, even when it resumes the same logical task.

| State | Meaning and allowed next states |
|---|---|
| `pending` | Dependencies or scheduling remain; may become `running`, `needs_input`, `cancelled`. |
| `running` | A fenced attempt owns execution; may become `completed`, `retry_wait`, `paused`, `needs_input`, `interrupted`, `failed`, `cancelled`. |
| `retry_wait` | An eligible transient retry awaits its persisted time; may become `pending`, `paused`, `cancelled`. |
| `paused` | A recoverable external condition blocks progress; may become `pending`, `needs_input`, `cancelled`. |
| `needs_input` | A concrete unresolved decision or permission requires participant input; may become `pending`, `cancelled`. |
| `interrupted` | Process completion or side effects are uncertain; reconciliation may produce `completed`, `pending`, `paused`, `needs_input`, `failed`, `cancelled`. |
| `completed` | Validated result and referenced artifacts are committed; terminal for this task version. |
| `failed` | Automatic recovery is exhausted or inappropriate; terminal for this task version. |
| `cancelled` | Execution stopped and reconciliation recorded; terminal for this task version. |

A user-requested rerun of a terminal task creates a successor task linked to it. Completed upstream tasks stay complete after a quota pause. A changed requirement creates a revision and explicitly invalidates affected downstream applicability; it does not silently rewrite old results. Reuse unaffected results only after dependency and applicability checks. `interrupted` is runtime-only: do not invent a final `TaskResult` until reconciliation establishes an outcome. Supported result outcomes are `completed`, `needs_input`, `paused`, `failed` and `cancelled`.

## 4. Scheduling and leases

Validate leader-proposed task graphs for cycles, missing dependencies, unknown capabilities, forbidden cross-team references and excessive fan-out. Deterministic worker selection uses configured capability requirements and stable priority ordering. Parallelize independent work only within declared budgets and workspace boundaries.

Leases are scoped: a coordinator lease per project, execution lease per task attempt, workspace-write lease per coding workspace, and concurrency permits per provider connection. There is no project-wide lock held throughout an LLM request. A lease includes an owner, expiration and increasing fencing generation. Heartbeats renew it through CAS. Every result publication rechecks its generation; an expired worker cannot commit a late result.

Lease expiration does not prove the underlying process stopped. The supervisor marks the attempt `interrupted`, blocks reuse of its workspace and reconciles process liveness. A replacement coordinator must not run a duplicate coding operation while the first may still mutate files.

```text
schedule(project):
  acquire_or_renew_project_coordinator_lease()
  recover_expired_attempts_before_workspace_reuse()
  for task in stable_ready_order(project):
    validate_pinned_inputs_and_team_scope(task)
    transaction:
      CAS(task.revision, expected_state=pending)
      reserve_budget_and_connection_permit()
      create_attempt_with_fencing_generation()
      set_state(running)
    dispatch_outside_transaction(attempt)

complete(attempt, candidate):
  validate_candidate_schema_references_and_required_evidence()
  materialize_immutable_payloads_before_database_commit()
  transaction:
    assert_live_attempt_fence_and_pinned_input_versions()
    append_TaskResult_and_Artifact_metadata()
    set_task_completed_using_CAS()
    settle_budget_and_release_permits()
    append_required_outbox_records()
```

Never hold a database transaction open while awaiting a backend, network request, build or student response. Actual token usage may arrive late or be unavailable; distinguish measured costs, estimates and unknown usage. Reservations limit intended concurrency, not guaranteed provider billing.

## 5. Publication and delivery

Filesystem writes and database commits are not one atomic transaction. First write each payload to a temporary file on the destination volume, flush, calculate its digest and validate its format. Publish to a content-addressed destination using a no-overwrite atomic operation supported by the platform. Flush directory metadata where supported; document the supported filesystem's durability limits. If an existing destination has a different digest, stop and report corruption.

Next, in one SQLite transaction, register artifact references, append the sealed bundle and delivery envelopes, and create outbox entries. Consumers cannot see staged files through the model-facing API. A crash before the transaction may leave unreferenced files; a conservative garbage collector removes them only after the retention window and active-attempt checks. A crash after commit leaves a durable outbox entry.

An outbox worker delivers at least once. The recipient deduplicates on stable delivery identity and verifies the bundle digest, destination team, required artifacts, schema compatibility and `expected_target_baseline` using CAS. **`expected_target_baseline` is the target's accepted bundle ID**, compared with `team_baselines.baseline_bundle_id`; it is not `HandoffBundle.baseline_id`, which is the sender's human/logical baseline label. Null explicitly expects that the target has no accepted baseline. Delivery status is a `pending`, `accepted`, `rejected` or `stale` projection backed by append-only events; it does not mutate the immutable envelope. Artifact lifecycle status (`draft`, `reviewable`, `accepted`, `superseded`, `stale`) likewise lives in a projection, separate from content identity. Record recipient acceptance and mark the outbox item acknowledged transactionally when local, or use explicit acknowledgement and redelivery when transport is separated.

Each receipt transition appends the complete schema-valid `DeliveryReceipt` as an authoritative `events` ledger entry. Its receipt ID and per-delivery `sequence` survive permanently; the project's event sequence is separate. The SQL `delivery_receipts` table holds only the latest receipt projection. Append the receipt and CAS its projection in one transaction; duplicate receipt identities or sequence conflicts cannot overwrite history. An identical delivery retry returns the existing acceptance receipt.

```text
receive(delivery):
  verify_destination_and_bundle_content()
  transaction:
    if previously_accepted(delivery.id): return_existing_receipt()
    reject_conflicting_identity_or_unmet_baseline()
    append_receipt_event()
    create_team_inbox_entry_and_dependent_tasks_once()
```

Redelivery must not generate duplicate worker tasks or logical delivery identities; repeated transport attempts are allowed. An old valid delivery can be recorded as stale for current execution while remaining available in history. There is no exactly-once promise for external model requests, commands or side effects. Idempotency means deduplicated runtime state and explicit reconciliation where an external action might have occurred twice.

## 6. Error normalization and student experience

Adapters emit `BackendEvent` records and a `NormalizedError` when they fail. Preserve a redacted provider code and request identifier, retry advice and relevant evidence; never infer the category solely from friendly error prose or HTTP status. A provider's 429 may mean a transient rate limit or exhausted account quota.

| Category | Default behavior |
|---|---|
| `rate_limited` | Persist bounded exponential backoff with jitter, honoring reliable retry-after information; enter `retry_wait`. |
| `quota_exhausted` | Enter `paused`; stop requests with the affected `quota_scope_id`, including connection aliases that share that scope. |
| `auth_required` | Enter `paused`; ask the participant to authenticate through the configured connection method. |
| `provider_unavailable` | Bounded retries if the operation is safe; otherwise reconcile, then pause. |
| `invalid_output` | Preserve rejected output; allow bounded schema repair using the same inputs, then request intervention. |
| `context_limit` | Replan or compact into an explicit checkpoint within the same team; never discard required inputs silently. |
| `permission_required` | Enter `needs_input`; show the exact blocked operation and permitted alternatives. |
| `capability_missing` | Enter `needs_input`; offer a compatible adapter, imported evidence or manual task when it can satisfy the same requirement. Unenforceable strict isolation requires explicit selection of a supported isolated execution profile. |
| `cancelled` | Stop owned processes and reconcile before recording cancellation. |
| `unknown` | Record diagnostics and pause; do not repeatedly retry an unclassified failure. |

Retry budgets count attempts and elapsed time and survive restarts. A failure affecting one task must not pause unrelated teams or connections unnecessarily, though dependent tasks remain blocked. Switching providers requires a configured participant policy or explicit choice; record the switch and rebuild context from allowed artifacts rather than transferring hidden conversation state.

The status view must explain the stage, task, cause, preserved work, required action and next resume command. Example: “Implementation / reservation endpoint paused: account quota exhausted. The design baseline and reviewed database module are complete. The current patch is preserved for reconciliation. Restore this connection or select a configured alternative, then resume this task.” Never invent a reset time. A dry-run resume explains what will continue before making a billable call.

## 7. Checkpoints and coding interruption

A checkpoint records pinned inputs, validated intermediate artifacts, task graph revision, outstanding questions, workspace baseline and patch digests, backend identifier, last observed event, tool side effects and lease generation. It is an explicit task summary, not a requirement to capture private model reasoning. Opaque provider session identifiers are optional accelerators; recovery must remain possible from local artifacts when the provider cannot resume its conversation.

Implement these runtime-private records; neither is a cross-team communication channel:

- **`Attempt`:** `attempt_id`, `task_id`, `cycle_id`, `team_id`, `attempt_number`, `revision`, `fencing_generation`, `lease_owner`, `lease_expires_at`, `started_at`, nullable `ended_at`, `backend_id`, `connection_ref`, `resolved_connection_json`, `resolved_connection_sha256`, `account_binding_id`, `quota_scope_id`, nullable `workspace_lineage_id`, nullable `profile_override_decision_id`, nullable `backend_session_id`, `input_manifest_sha256`, nullable `workspace_id`, nullable `process_identity`, nullable `last_event_id`, nullable `last_checkpoint_id`, nullable `normalized_error`, and nullable `result_id`. `process_identity` contains supervisor instance, process ID and process creation timestamp; a PID alone cannot establish ownership after reuse. `revision` controls the mutable operational projection; significant changes also append journal events. Connection references never contain credentials.
- **`Checkpoint`:** `checkpoint_id`, `task_id`, `attempt_id`, `team_id`, `created_at`, `fencing_generation`, `task_graph_revision`, `input_deliveries`, `input_refs`, nullable `baseline_source_ref`, nullable `candidate_source_ref`, nullable `patch_ref`, `intermediate_refs`, nullable `last_event_id`, nullable `backend_session_id`, `summary_ref`, `outstanding_questions`, `side_effects`, and `reconciliation_required`. Artifact references identify immutable bytes; a checkpoint itself is immutable. Each side-effect entry contains `operation_id`, `kind`, nullable `external_id`, nullable `evidence_ref`, and `outcome` (`confirmed`, `not_performed`, or `unknown`). Unknown outcomes force reconciliation before repeating the operation.

Validate task/team ownership and all artifact grants on checkpoint load. A summary is not a substitute for the pinned inputs. A never-started `pending` task uses its input manifest; resuming interrupted execution requires a valid checkpoint or an explicit recovery record explaining how a new baseline was established. Never manufacture a checkpoint from an incomplete provider stream alone.

Freeze each attempt's cycle and connection binding before dispatch. `resolved_connection_json` contains the selected profile, authentication mode, backend/model/endpoint or CLI configuration, isolation profile, and credential references, never secret values. Its canonical bytes determine `resolved_connection_sha256`. `account_binding_id` identifies the observed or explicitly declared account binding; it cannot falsely claim provider verification. `quota_scope_id` identifies requests affected by the same quota, and `workspace_lineage_id` identifies the coding snapshot lineage, remaining null for nonrepository work. Check the current identity/configuration against this binding before continuing a native session.

`Task.backend_profile` remains the initially requested profile. An authorized override records a decision and creates a new attempt with its own frozen snapshot, `profile_override_decision_id`, and new backend session; never mutate the original task or repoint an old attempt. Copy `cycle_id` from the immutable task once. Session reuse requires matching project, cycle, team, agent identity, account/authentication binding, connection digest and workspace lineage. A new provider may consume allowed checkpoint artifacts, but it cannot inherit another binding's opaque session. Lease heartbeat and progress fields remain mutable through CAS; these identity/configuration fields do not.

Each coding task starts from its team's imported immutable source snapshot in an isolated clone or copy. A worktree within a team is permissible only when its shared Git metadata cannot expose another team's history. Do not maintain a mutable repository used simultaneously by implementation and testing. A tested source archive is handed to testing; changes proposed by testing return as a new package.

On coding interruption:

1. Stop or detach only through a documented backend procedure; inspect the process tree and supervisor registry. Quarantine the workspace if liveness is uncertain.
2. Capture tracked differences, declared untracked outputs, command results and tool receipts against the pinned baseline. Exclude credentials and unsupported file types through explicit export policy.
3. Validate the patch, check for out-of-scope changes and inspect partial files. An incomplete patch is a preserved candidate, never a completed implementation.
4. Determine whether the last external action completed. Query its existing identifier where supported; otherwise require a safe reconciliation action or participant decision.
5. Resume from a verified candidate snapshot in a new attempt, or restart from the baseline while retaining the abandoned candidate as evidence. Re-run affected checks.

Human takeover uses the same workspace ownership protocol. The participant claims the task, stops competing writers, edits an exported snapshot and imports the result with provenance. “Resume” validates the import and releases ownership; it never overwrites a person's uncommitted work automatically.

## 8. Real execution evidence and environment preparation

A test result is successful only when a runner actually executes against the recorded source digest and returns its observed exit status and report. Store command arguments, working directory, dependency/image digest, environment allowlist, timestamps, exit code, stdout/stderr references and parsed assertions. Distinguish failed assertions, setup failure, timeout, cancellation, unavailable runner and tests not executed. Failed assertions are valid test observations, not provider errors. LLM-written summaries reference these observations; they cannot establish a pass.

Separate dependency preparation from test execution. A permitted preparation task resolves the lockfile, downloads dependencies and constructs a versioned environment or image. Subsequent tests use that prepared environment with the specified network policy. Missing dependencies cause setup failure or a new preparation task; they do not justify silently enabling unrestricted network access. Fixtures, seed data, clocks and resource limits must be reproducible where the project requires them.

## 9. Isolation modes and recovery acceptance

Strict isolation requires an operating-system or container boundary exposing only the assigned team's permitted input files, output workspace and runtime broker. Keep credentials in the adapter/broker where practical. Native Codex or Claude Code workspace restrictions may control writes without blocking all reads; a shared host account or Git worktree alone is not proof of team isolation. If the execution profile cannot enforce the required boundary, record `capability_missing`, transition to `needs_input`, and make zero backend/process invocations until the participant explicitly selects a supported isolated profile. Prompt instructions supplement access control; they do not implement it.

An explicitly selected unisolated development mode may help an engineer debug adapters in a disposable project. Mark every such run and its results as nonconforming: it must never claim to implement the compliant six-team lifecycle, pass the isolation acceptance cases, or silently substitute for strict execution. Its artifacts require ordinary validated import before use in a compliant project; importing a result does not certify how it was originally generated.

Before declaring recovery complete, automated acceptance tests must exercise: process death before and after database commit; duplicate delivery; stale-worker publication; account quota interruption and later resume; malformed backend events; disconnected coding processes; source edits during human takeover; conflicting baseline delivery; failed environment preparation; and a fabricated test-pass claim without execution evidence. Assert both the resumed outcome and preserved audit history. A scenario with unavoidable external uncertainty passes only by reaching a clear blocked state with a usable reconciliation path, not by pretending to provide failure-free automation.
