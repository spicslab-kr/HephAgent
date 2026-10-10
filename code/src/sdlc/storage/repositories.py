"""Repository functions over the SQLite schema (parameterised SQL only).

These are thin; authorization, route checks and CAS semantics live in the application
services that call them inside one transaction. Append-only tables get ``insert_*``
functions only. Mutable projections expose CAS updates that take the expected revision.
"""

from __future__ import annotations

import json
from typing import Any

from sdlc.domain.errors import Conflict, StaleRevision
from sdlc.domain.models import (
    Artifact,
    ArtifactRef,
    Delivery,
    DeliveryReceipt,
    HandoffBundle,
)
from sdlc.storage.database import Database
from sdlc.verification.canonical import canonical_bytes


def _j(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


# ----------------------------------------------------------------------------- projects


def insert_project(db: Database, project_id: str, name: str, configuration: dict[str, Any], now: str) -> None:
    db.execute(
        "INSERT INTO projects(project_id, name, revision, created_at, updated_at, configuration_json) VALUES (?,?,1,?,?,?)",
        (project_id, name, now, now, _j(configuration)),
    )
    for team in ("planning", "requirements", "design", "implementation", "testing", "maintenance"):
        db.execute(
            "INSERT INTO team_baselines(project_id, team_id, baseline_bundle_id, revision, updated_at) VALUES (?,?,NULL,1,?)",
            (project_id, team, now),
        )


def get_project(db: Database, project_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM projects WHERE project_id = ?", (project_id,))
    return dict(row) if row else None


def get_team_baseline(db: Database, project_id: str, team: str) -> tuple[str | None, int]:
    row = db.one("SELECT baseline_bundle_id, revision FROM team_baselines WHERE project_id=? AND team_id=?", (project_id, team))
    if row is None:
        raise Conflict("team baseline row missing", project_id=project_id, team=team)
    return row["baseline_bundle_id"], row["revision"]


def cas_team_baseline(db: Database, project_id: str, team: str, expected_revision: int, bundle_id: str | None, now: str) -> int:
    cursor = db.execute(
        "UPDATE team_baselines SET baseline_bundle_id=?, revision=revision+1, updated_at=? WHERE project_id=? AND team_id=? AND revision=?",
        (bundle_id, now, project_id, team, expected_revision),
    )
    if cursor.rowcount != 1:
        raise StaleRevision("team baseline changed concurrently", team=team, expected_revision=expected_revision)
    return expected_revision + 1


# ----------------------------------------------------------------------------- events


def next_sequence(db: Database, project_id: str) -> int:
    row = db.one("SELECT COALESCE(MAX(sequence_number), 0) AS n FROM events WHERE project_id=?", (project_id,))
    return int(row["n"]) + 1


def insert_event(
    db: Database,
    project_id: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: str,
    *,
    team_id: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> int:
    sequence = next_sequence(db, project_id)
    db.execute(
        "INSERT INTO events(project_id, event_id, sequence_number, team_id, task_id, attempt_id, event_type, occurred_at, payload_json)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, event_id, sequence, team_id, task_id, attempt_id, event_type, now, _j(payload)),
    )
    return sequence


def list_events(db: Database, project_id: str, event_type: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events WHERE project_id=?"
    params: tuple = (project_id,)
    if event_type:
        sql += " AND event_type=?"
        params += (event_type,)
    rows = db.all(sql + " ORDER BY sequence_number", params)
    return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]


# ----------------------------------------------------------------------------- artifacts


def next_artifact_revision(db: Database, project_id: str, artifact_id: str) -> int:
    row = db.one(
        "SELECT COALESCE(MAX(artifact_revision), 0) AS n FROM artifacts WHERE project_id=? AND artifact_id=?",
        (project_id, artifact_id),
    )
    return int(row["n"]) + 1


def insert_artifact(db: Database, artifact: Artifact, storage_key: str, status_event_id: str, now: str) -> None:
    db.execute(
        "INSERT INTO artifacts(project_id, artifact_id, artifact_revision, owner_team, artifact_type, media_type, sha256,"
        " byte_length, storage_key, created_at, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            artifact.project_id,
            artifact.artifact_id,
            artifact.revision,
            artifact.owner_team.value,
            artifact.kind,
            artifact.media_type,
            artifact.sha256,
            artifact.size_bytes,
            storage_key,
            artifact.created_at,
            _j(artifact.to_json_dict()),
        ),
    )
    db.execute(
        "INSERT INTO artifact_status(project_id, artifact_id, artifact_revision, status, revision, updated_at, event_id)"
        " VALUES (?,?,?,'draft',1,?,?)",
        (artifact.project_id, artifact.artifact_id, artifact.revision, now, status_event_id),
    )


def get_artifact(db: Database, project_id: str, artifact_id: str, revision: int) -> Artifact | None:
    row = db.one(
        "SELECT metadata_json FROM artifacts WHERE project_id=? AND artifact_id=? AND artifact_revision=?",
        (project_id, artifact_id, revision),
    )
    return Artifact.model_validate(json.loads(row["metadata_json"])) if row else None


def get_artifact_by_ref(db: Database, project_id: str, ref: ArtifactRef) -> Artifact | None:
    artifact = get_artifact(db, project_id, ref.artifact_id, ref.revision)
    if artifact is None or artifact.sha256 != ref.sha256:
        return None
    return artifact


def list_artifacts(db: Database, project_id: str, owner_team: str | None = None) -> list[Artifact]:
    sql = "SELECT metadata_json FROM artifacts WHERE project_id=?"
    params: tuple = (project_id,)
    if owner_team:
        sql += " AND owner_team=?"
        params += (owner_team,)
    rows = db.all(sql + " ORDER BY created_at, artifact_id, artifact_revision", params)
    return [Artifact.model_validate(json.loads(r["metadata_json"])) for r in rows]


def get_artifact_status(db: Database, project_id: str, artifact_id: str, revision: int) -> tuple[str, int]:
    row = db.one(
        "SELECT status, revision FROM artifact_status WHERE project_id=? AND artifact_id=? AND artifact_revision=?",
        (project_id, artifact_id, revision),
    )
    if row is None:
        raise Conflict("artifact status missing", artifact_id=artifact_id, revision=revision)
    return row["status"], row["revision"]


def cas_artifact_status(
    db: Database, project_id: str, artifact_id: str, revision: int, expected: int, status: str, event_id: str, now: str
) -> None:
    cursor = db.execute(
        "UPDATE artifact_status SET status=?, revision=revision+1, updated_at=?, event_id=?"
        " WHERE project_id=? AND artifact_id=? AND artifact_revision=? AND revision=?",
        (status, now, event_id, project_id, artifact_id, revision, expected),
    )
    if cursor.rowcount != 1:
        raise StaleRevision("artifact status changed concurrently", artifact_id=artifact_id, revision=revision)


# ----------------------------------------------------------------------------- bundles


def insert_bundle(db: Database, bundle: HandoffBundle, sha256: str, now: str) -> None:
    db.execute(
        "INSERT INTO bundles(project_id, bundle_id, source_team, sha256, sealed_at, bundle_json) VALUES (?,?,?,?,?,?)",
        (bundle.project_id, bundle.bundle_id, bundle.source_team.value, sha256, now, _j(bundle.to_json_dict())),
    )
    for ref in bundle.artifact_refs:
        db.execute(
            "INSERT INTO bundle_artifacts(project_id, bundle_id, artifact_id, artifact_revision, sha256, role) VALUES (?,?,?,?,?,'manifest')",
            (bundle.project_id, bundle.bundle_id, ref.artifact_id, ref.revision, ref.sha256),
        )


def get_bundle(db: Database, project_id: str, bundle_id: str) -> tuple[HandoffBundle, str] | None:
    row = db.one("SELECT bundle_json, sha256 FROM bundles WHERE project_id=? AND bundle_id=?", (project_id, bundle_id))
    if row is None:
        return None
    return HandoffBundle.model_validate(json.loads(row["bundle_json"])), row["sha256"]


def list_bundles(db: Database, project_id: str) -> list[tuple[HandoffBundle, str]]:
    rows = db.all("SELECT bundle_json, sha256 FROM bundles WHERE project_id=? ORDER BY sealed_at, bundle_id", (project_id,))
    return [(HandoffBundle.model_validate(json.loads(r["bundle_json"])), r["sha256"]) for r in rows]


# ----------------------------------------------------------------------------- deliveries


def insert_delivery(db: Database, delivery: Delivery, now: str) -> None:
    db.execute(
        "INSERT INTO deliveries(project_id, delivery_id, bundle_id, bundle_sha256, source_team, target_team,"
        " expected_target_baseline, created_at, delivery_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            delivery.project_id,
            delivery.delivery_id,
            delivery.bundle_ref.bundle_id,
            delivery.bundle_ref.sha256,
            delivery.from_team.value,
            delivery.to_team.value,
            delivery.expected_target_baseline,
            delivery.created_at,
            _j(delivery.to_json_dict()),
        ),
    )
    for ref in delivery.read_grants:
        db.execute(
            "INSERT INTO delivery_grants(project_id, delivery_id, artifact_id, artifact_revision, sha256, grant_reason) VALUES (?,?,?,?,?,'direct')",
            (delivery.project_id, delivery.delivery_id, ref.artifact_id, ref.revision, ref.sha256),
        )


def get_delivery(db: Database, project_id: str, delivery_id: str) -> Delivery | None:
    row = db.one("SELECT delivery_json FROM deliveries WHERE project_id=? AND delivery_id=?", (project_id, delivery_id))
    return Delivery.model_validate(json.loads(row["delivery_json"])) if row else None


def list_deliveries(db: Database, project_id: str, to_team: str | None = None) -> list[Delivery]:
    sql = "SELECT delivery_json FROM deliveries WHERE project_id=?"
    params: tuple = (project_id,)
    if to_team:
        sql += " AND target_team=?"
        params += (to_team,)
    rows = db.all(sql + " ORDER BY created_at, delivery_id", params)
    return [Delivery.model_validate(json.loads(r["delivery_json"])) for r in rows]


def insert_receipt_projection(db: Database, receipt: DeliveryReceipt, event_id: str, now: str) -> None:
    db.execute(
        "INSERT INTO delivery_receipts(project_id, delivery_id, receipt_id, sequence, status, revision, updated_at, event_id, reason)"
        " VALUES (?,?,?,?,?,1,?,?,?)",
        (receipt.project_id, receipt.delivery_id, receipt.receipt_id, receipt.sequence, receipt.status.value, now, event_id, receipt.reason),
    )


def cas_receipt_projection(db: Database, receipt: DeliveryReceipt, expected_revision: int, event_id: str, now: str) -> None:
    cursor = db.execute(
        "UPDATE delivery_receipts SET receipt_id=?, sequence=?, status=?, revision=revision+1, updated_at=?, event_id=?, reason=?"
        " WHERE project_id=? AND delivery_id=? AND revision=? AND sequence<?",
        (
            receipt.receipt_id,
            receipt.sequence,
            receipt.status.value,
            now,
            event_id,
            receipt.reason,
            receipt.project_id,
            receipt.delivery_id,
            expected_revision,
            receipt.sequence,
        ),
    )
    if cursor.rowcount != 1:
        raise StaleRevision("receipt projection changed concurrently", delivery_id=receipt.delivery_id)


def get_receipt_projection(db: Database, project_id: str, delivery_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM delivery_receipts WHERE project_id=? AND delivery_id=?", (project_id, delivery_id))
    return dict(row) if row else None


def list_receipt_events(db: Database, project_id: str, delivery_id: str) -> list[DeliveryReceipt]:
    rows = db.all(
        "SELECT payload_json FROM events WHERE project_id=? AND event_type='delivery.receipt' ORDER BY sequence_number",
        (project_id,),
    )
    receipts = [DeliveryReceipt.model_validate(json.loads(r["payload_json"])) for r in rows]
    return [r for r in receipts if r.delivery_id == delivery_id]


def accepted_deliveries(db: Database, project_id: str, team: str) -> list[Delivery]:
    rows = db.all(
        "SELECT d.delivery_json FROM deliveries d JOIN delivery_receipts r"
        " ON r.project_id=d.project_id AND r.delivery_id=d.delivery_id"
        " WHERE d.project_id=? AND d.target_team=? AND r.status='accepted' ORDER BY d.created_at, d.delivery_id",
        (project_id, team),
    )
    return [Delivery.model_validate(json.loads(r["delivery_json"])) for r in rows]


# ----------------------------------------------------------------------------- outbox


def insert_outbox(db: Database, project_id: str, outbox_id: str, delivery_id: str, operation_id: str, now: str) -> None:
    db.execute(
        "INSERT INTO outbox(project_id, outbox_id, delivery_id, operation_id, status, revision, attempt_count, next_attempt_at,"
        " lease_owner, lease_expires_at, fencing_generation, last_error_json, created_at, updated_at)"
        " VALUES (?,?,?,?,'pending',1,0,?,NULL,NULL,0,NULL,?,?)",
        (project_id, outbox_id, delivery_id, operation_id, now, now, now),
    )


def pending_outbox(db: Database, project_id: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT * FROM outbox WHERE project_id=? AND status='pending' ORDER BY created_at, outbox_id", (project_id,))
    return [dict(r) for r in rows]


def ack_outbox(db: Database, project_id: str, delivery_id: str, now: str) -> None:
    db.execute(
        "UPDATE outbox SET status='acked', revision=revision+1, attempt_count=attempt_count+1, updated_at=? WHERE project_id=? AND delivery_id=?",
        (now, project_id, delivery_id),
    )


# ----------------------------------------------------------------------------- decisions


def insert_decision(db: Database, project_id: str, decision_id: str, actor_id: str, decision: dict[str, Any], now: str, team_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO decisions(project_id, decision_id, team_id, task_id, actor_id, created_at, decision_json) VALUES (?,?,?,NULL,?,?,?)",
        (project_id, decision_id, team_id, actor_id, now, _j(decision)),
    )


def get_decision(db: Database, project_id: str, decision_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM decisions WHERE project_id=? AND decision_id=?", (project_id, decision_id))
    return dict(row) | {"decision": json.loads(row["decision_json"])} if row else None
