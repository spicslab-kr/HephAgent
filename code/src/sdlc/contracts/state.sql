-- SDLC Agent Teams: SQLite metadata schema v1.
-- Reference DDL, not a substitute for the runtime's schema/authorization services.
-- All timestamp TEXT values are UTC RFC3339 strings. All *_json fields contain
-- schema-validated JSON; canonicalization and content hashes are service checks.
-- ArtifactRef {artifact_id, revision, sha256} maps to artifact_id,
-- artifact_revision, sha256 with project_id supplied by the authenticated scope.
--
-- APPEND-ONLY: schema_versions, artifacts, bundles, bundle_artifacts, deliveries,
-- delivery_grants, checkpoints, task_results, events, decisions.
-- MUTABLE PROJECTIONS: projects, team_baselines, tasks, attempts, artifact_status,
-- delivery_receipts, outbox, leases. Mutations use revision CAS and journal events.
-- SQLite has no application-level row authorization. Only the runtime broker may
-- write this database. Team agents must never receive direct database access.
-- No triggers are used: append-only enforcement, DAG cycles, same-team task
-- dependencies, process ownership, grant closure, hash equality, allowed state
-- transitions, and stale-worker fencing are mandatory service invariants.
-- Files become durable before metadata publication; publication and outbox
-- insertion commit together. SQL alone provides neither sandbox isolation nor
-- exactly-once external requests. Never silently migrate an existing database.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;

BEGIN IMMEDIATE;

CREATE TABLE schema_versions (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    configuration_json TEXT NOT NULL CHECK (json_valid(configuration_json))
);

CREATE TABLE team_baselines (
    project_id TEXT NOT NULL,
    team_id TEXT NOT NULL CHECK (team_id IN
        ('planning','requirements','design','implementation','testing','maintenance')),
    baseline_bundle_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, team_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (project_id, baseline_bundle_id) REFERENCES bundles(project_id, bundle_id)
    -- The accepted target baseline is a BUNDLE_ID, not the sender's logical
    -- HandoffBundle.baseline_id label. NULL means no accepted target baseline.
);

CREATE TABLE tasks (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    backend_profile TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('pending','running','retry_wait','paused','needs_input','interrupted',
         'completed','failed','cancelled')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    task_graph_revision INTEGER NOT NULL CHECK (task_graph_revision > 0),
    priority INTEGER NOT NULL DEFAULT 0,
    input_manifest_sha256 TEXT NOT NULL CHECK (length(input_manifest_sha256) = 64
        AND input_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    input_deliveries_json TEXT NOT NULL CHECK (json_valid(input_deliveries_json)),
    input_refs_json TEXT NOT NULL CHECK (json_valid(input_refs_json)),
    task_json TEXT NOT NULL CHECK (json_valid(task_json)),
    successor_of_task_id TEXT,
    next_attempt_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, successor_of_task_id) REFERENCES tasks(project_id, task_id),
    CHECK (successor_of_task_id IS NULL OR successor_of_task_id <> task_id)
    -- cycle_id and backend_profile match the immutable task_json request.
    -- backend_profile is the initial requested profile, not a mutable selector.
    -- Authorized profile changes are pinned on a NEW attempt, never this task.
);

CREATE TABLE task_dependencies (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    requires_task_id TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id, requires_task_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, requires_task_id) REFERENCES tasks(project_id, task_id),
    CHECK (task_id <> requires_task_id)
    -- Service rejects cycles and dependencies on a different team's task.
    -- Cross-team prerequisites must use accepted Delivery IDs instead.
);

CREATE TABLE attempts (
    project_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    fencing_generation INTEGER NOT NULL CHECK (fencing_generation > 0),
    lease_owner TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    backend_id TEXT NOT NULL,
    connection_ref TEXT NOT NULL,
    resolved_connection_json TEXT NOT NULL CHECK (json_valid(resolved_connection_json)),
    resolved_connection_sha256 TEXT NOT NULL CHECK (length(resolved_connection_sha256) = 64
        AND resolved_connection_sha256 NOT GLOB '*[^0-9a-f]*'),
    account_binding_id TEXT NOT NULL,
    quota_scope_id TEXT NOT NULL,
    workspace_lineage_id TEXT,
    profile_override_decision_id TEXT,
    backend_session_id TEXT,
    input_manifest_sha256 TEXT NOT NULL CHECK (length(input_manifest_sha256) = 64
        AND input_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT,
    process_identity_json TEXT CHECK (process_identity_json IS NULL
        OR json_valid(process_identity_json)),
    last_event_id TEXT,
    last_checkpoint_id TEXT,
    normalized_error_json TEXT CHECK (normalized_error_json IS NULL
        OR json_valid(normalized_error_json)),
    result_id TEXT,
    PRIMARY KEY (project_id, attempt_id),
    UNIQUE (project_id, task_id, attempt_number),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, last_event_id) REFERENCES events(project_id, event_id),
    FOREIGN KEY (project_id, last_checkpoint_id) REFERENCES checkpoints(project_id, checkpoint_id),
    FOREIGN KEY (project_id, result_id) REFERENCES task_results(project_id, result_id),
    FOREIGN KEY (project_id, profile_override_decision_id) REFERENCES decisions(project_id, decision_id)
    -- process_identity_json: supervisor instance, PID, process creation time.
    -- Service verifies task/team/session ownership and lease generation equality.
    -- Immutable attempt binding: cycle_id, backend_id, connection_ref,
    -- resolved_connection_json/sha256, account_binding_id, quota_scope_id,
    -- workspace_lineage_id and profile_override_decision_id. Never repoint them.
    -- The snapshot includes selected profile, auth mode, model/endpoint or CLI
    -- configuration, isolation profile and credential REFERENCES, never secrets.
    -- Canonicalize/hash it; verify cycle_id against the task at insertion.
    -- A profile change requires an authorizing decision, NEW attempt/session,
    -- and a fresh resolved snapshot. Existing checkpoint history stays intact.
);

CREATE TABLE checkpoints (
    project_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fencing_generation INTEGER NOT NULL CHECK (fencing_generation > 0),
    task_graph_revision INTEGER NOT NULL CHECK (task_graph_revision > 0),
    input_deliveries_json TEXT NOT NULL CHECK (json_valid(input_deliveries_json)),
    input_refs_json TEXT NOT NULL CHECK (json_valid(input_refs_json)),
    baseline_source_ref_json TEXT CHECK (baseline_source_ref_json IS NULL
        OR json_valid(baseline_source_ref_json)),
    candidate_source_ref_json TEXT CHECK (candidate_source_ref_json IS NULL
        OR json_valid(candidate_source_ref_json)),
    patch_ref_json TEXT CHECK (patch_ref_json IS NULL OR json_valid(patch_ref_json)),
    intermediate_refs_json TEXT NOT NULL CHECK (json_valid(intermediate_refs_json)),
    last_event_id TEXT,
    backend_session_id TEXT,
    summary_ref_json TEXT NOT NULL CHECK (json_valid(summary_ref_json)),
    outstanding_questions_json TEXT NOT NULL CHECK (json_valid(outstanding_questions_json)),
    side_effects_json TEXT NOT NULL CHECK (json_valid(side_effects_json)),
    reconciliation_required INTEGER NOT NULL CHECK (reconciliation_required IN (0,1)),
    PRIMARY KEY (project_id, checkpoint_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, attempt_id) REFERENCES attempts(project_id, attempt_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, last_event_id) REFERENCES events(project_id, event_id)
    -- Service resolves every JSON ArtifactRef by project+ID+revision AND sha256,
    -- checks its grant, and validates side-effect outcomes before retry.
);

CREATE TABLE artifacts (
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL CHECK (artifact_revision > 0),
    owner_team TEXT NOT NULL CHECK (owner_team IN
        ('participant','planning','requirements','design','implementation','testing','maintenance')),
    artifact_type TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    PRIMARY KEY (project_id, artifact_id, artifact_revision),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
    -- participant ownership is legal only through validated ingress.
    -- storage_key is a broker-resolved key, never an unchecked caller path.
);

CREATE TABLE artifact_status (
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','reviewable','accepted','superseded','stale')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (project_id, artifact_id, artifact_revision),
    FOREIGN KEY (project_id, artifact_id, artifact_revision)
        REFERENCES artifacts(project_id, artifact_id, artifact_revision),
    FOREIGN KEY (project_id, event_id) REFERENCES events(project_id, event_id)
);

CREATE TABLE bundles (
    project_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    source_team TEXT NOT NULL CHECK (source_team IN
        ('participant','planning','requirements','design','implementation','testing','maintenance')),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    sealed_at TEXT NOT NULL,
    bundle_json TEXT NOT NULL CHECK (json_valid(bundle_json)),
    PRIMARY KEY (project_id, bundle_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
    -- No mutable bundle status. Different bytes require a new bundle identity.
);

CREATE TABLE bundle_artifacts (
    project_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    role TEXT NOT NULL,
    PRIMARY KEY (project_id, bundle_id, artifact_id, artifact_revision),
    FOREIGN KEY (project_id, bundle_id) REFERENCES bundles(project_id, bundle_id),
    FOREIGN KEY (project_id, artifact_id, artifact_revision)
        REFERENCES artifacts(project_id, artifact_id, artifact_revision)
    -- Service must check sha256 equality to immutable artifacts before insertion.
);

CREATE TABLE deliveries (
    project_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL CHECK (length(bundle_sha256) = 64
        AND bundle_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_team TEXT NOT NULL CHECK (source_team IN
        ('participant','planning','requirements','design','implementation','testing','maintenance')),
    target_team TEXT NOT NULL,
    expected_target_baseline TEXT,
    created_at TEXT NOT NULL,
    delivery_json TEXT NOT NULL CHECK (json_valid(delivery_json)),
    PRIMARY KEY (project_id, delivery_id),
    FOREIGN KEY (project_id, bundle_id) REFERENCES bundles(project_id, bundle_id),
    FOREIGN KEY (project_id, target_team) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, expected_target_baseline) REFERENCES bundles(project_id, bundle_id)
    -- Immutable envelope; receipt state is a separate projection.
    -- Source identity and manifest hashes are checked against the sealed bundle.
    -- expected_target_baseline compares with team_baselines.baseline_bundle_id:
    -- it is the target's accepted BUNDLE_ID, NOT HandoffBundle.baseline_id.
    -- NULL is an explicit expectation that the target has no accepted baseline.
);

CREATE TABLE delivery_grants (
    project_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    grant_reason TEXT NOT NULL CHECK (grant_reason IN ('direct','dependency')),
    PRIMARY KEY (project_id, delivery_id, artifact_id, artifact_revision),
    FOREIGN KEY (project_id, delivery_id) REFERENCES deliveries(project_id, delivery_id),
    FOREIGN KEY (project_id, artifact_id, artifact_revision)
        REFERENCES artifacts(project_id, artifact_id, artifact_revision)
    -- Service computes exact declared closure, validates hashes, and rejects
    -- undeclared references. Task input_refs may narrow but never expand it.
);

CREATE TABLE delivery_receipts (
    project_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected','stale')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (project_id, delivery_id),
    FOREIGN KEY (project_id, delivery_id) REFERENCES deliveries(project_id, delivery_id),
    FOREIGN KEY (project_id, event_id) REFERENCES events(project_id, event_id)
    -- This table stores ONLY the latest receipt projection, not the receipt log.
    -- The authoritative append-only receipt ledger is events: event_type
    -- 'delivery.receipt', payload_json = the complete schema-valid DeliveryReceipt.
    -- That payload retains receipt_id, delivery_id, project_id, per-delivery
    -- sequence, status, reason, recorded_at and schema_version. sequence differs
    -- from events.sequence_number, which orders the entire project journal.
    -- Service enforces unique receipt IDs and monotonically increasing sequence
    -- per delivery. Append event and CAS this projection in one transaction.
    -- Acceptance, target-baseline CAS and dependent-task creation also commit
    -- together. Identical redelivery returns the existing receipt without a new
    -- acceptance event or duplicated tasks. Historical receipts are never lost.
);

CREATE TABLE events (
    project_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    team_id TEXT,
    task_id TEXT,
    attempt_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    PRIMARY KEY (project_id, event_id),
    UNIQUE (project_id, sequence_number),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, attempt_id) REFERENCES attempts(project_id, attempt_id)
    -- BackendEvent is one event_type family. Redact secrets before insertion.
    -- Large event payloads use immutable artifact references in payload_json.
);

CREATE TABLE task_results (
    project_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('completed','needs_input','paused','failed','cancelled')),
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    PRIMARY KEY (project_id, result_id),
    UNIQUE (project_id, attempt_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
    FOREIGN KEY (project_id, attempt_id) REFERENCES attempts(project_id, attempt_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id)
    -- interrupted is runtime-only and has no final result before reconciliation.
);

CREATE TABLE decisions (
    project_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    team_id TEXT,
    task_id TEXT,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
    PRIMARY KEY (project_id, decision_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (project_id, team_id) REFERENCES team_baselines(project_id, team_id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
);

CREATE TABLE outbox (
    project_id TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','in_flight','acked','dead')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    fencing_generation INTEGER NOT NULL DEFAULT 0 CHECK (fencing_generation >= 0),
    last_error_json TEXT CHECK (last_error_json IS NULL OR json_valid(last_error_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, outbox_id),
    UNIQUE (project_id, delivery_id),
    UNIQUE (project_id, operation_id),
    FOREIGN KEY (project_id, delivery_id) REFERENCES deliveries(project_id, delivery_id)
);

CREATE TABLE leases (
    scope_kind TEXT NOT NULL CHECK (scope_kind IN
        ('coordinator','task_attempt','workspace','connection_permit')),
    scope_key TEXT NOT NULL,
    project_id TEXT,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    fencing_generation INTEGER NOT NULL CHECK (fencing_generation > 0),
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (scope_kind, scope_key),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
    -- Scope keys are globally namespaced. Connection permits use connection+slot
    -- across projects; other keys include project identity. Never delete/reset a
    -- fencing generation during reacquisition. Expiry does not prove process exit.
);

CREATE INDEX tasks_ready ON tasks(project_id, state, priority, next_attempt_at);
CREATE INDEX dependencies_reverse ON task_dependencies(project_id, requires_task_id);
CREATE INDEX attempts_by_task ON attempts(project_id, task_id, attempt_number);
CREATE INDEX checkpoints_by_task ON checkpoints(project_id, task_id, created_at);
CREATE INDEX artifacts_by_owner ON artifacts(project_id, owner_team, artifact_type);
CREATE INDEX deliveries_by_target ON deliveries(project_id, target_team, created_at);
CREATE INDEX events_by_task ON events(project_id, task_id, sequence_number);
CREATE INDEX outbox_due ON outbox(status, next_attempt_at);
CREATE INDEX leases_expiring ON leases(expires_at);

INSERT INTO schema_versions(version, applied_at, description)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'Initial reference metadata schema');
PRAGMA user_version = 1;

COMMIT;
