"""Semantic checks for a ``CycleFixture`` (handoff docs/07 s8, tools/validate_package.py).

These are the cross-record invariants JSON Schema cannot express: payload hashes,
exact grant sets, forwarding authorization, dependency closure, acyclic graphs and
team-scoped task inputs. The same rules apply to live records in the artifact and
delivery services (M1); here they run over an in-memory fixture.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sdlc.domain.errors import InvalidSchema
from sdlc.domain.models import (
    Artifact,
    BundleKind,
    CycleFixture,
    Delivery,
    DeliveryReceipt,
    HandoffBundle,
    Origin,
    OwnerTeam,
    ReceiptStatus,
    Task,
)
from sdlc.domain.teams import Principal, Team, route_allowed
from sdlc.verification.canonical import digest, sha256_text

RefKey = tuple[str, int, str]


class FixtureInvalid(InvalidSchema):
    code = "fixture_invalid"


def _require(condition: bool, message: str, **details: Any) -> None:
    if not condition:
        raise FixtureInvalid(message, **details)


def assert_dag(graph: Mapping[Any, Iterable[Any]], label: str) -> None:
    """Raise if ``graph`` (node -> dependencies) has an unresolved node or a cycle."""
    active: set[Any] = set()
    visited: set[Any] = set()

    def visit(node: Any) -> None:
        _require(node in graph, f"{label}: unresolved dependency", node=str(node))
        _require(node not in active, f"{label}: dependency cycle", node=str(node))
        if node in visited:
            return
        active.add(node)
        for dependency in graph[node]:
            visit(dependency)
        active.remove(node)
        visited.add(node)

    for node in list(graph):
        visit(node)


def latest_receipts(receipts: Iterable[DeliveryReceipt]) -> dict[str, DeliveryReceipt]:
    """Latest receipt per delivery, by per-delivery sequence (not wall clock)."""
    latest: dict[str, DeliveryReceipt] = {}
    seen: set[tuple[str, int]] = set()
    for receipt in receipts:
        key = (receipt.delivery_id, receipt.sequence)
        _require(key not in seen, "duplicate receipt sequence", delivery_id=receipt.delivery_id)
        seen.add(key)
        current = latest.get(receipt.delivery_id)
        if current is None or receipt.sequence > current.sequence:
            latest[receipt.delivery_id] = receipt
    return latest


def check_fixture(fixture: CycleFixture) -> dict[str, int]:
    """Validate every cross-record rule of a fixture; return simple counts on success."""
    project = fixture.project_id
    artifacts: dict[RefKey, Artifact] = {}
    for artifact in fixture.artifacts:
        _require(artifact.ref.key not in artifacts, "duplicate artifact reference")
        artifacts[artifact.ref.key] = artifact
    _require(
        len({key[:2] for key in artifacts}) == len(artifacts),
        "one artifact revision has multiple digests",
    )

    paths: set[str] = set()
    for key, artifact in artifacts.items():
        _require(artifact.project_id == project, "artifact project mismatch", artifact_id=key[0])
        _require(artifact.origin == Origin.FIXTURE, "fixture cannot claim actual evidence origin")
        _require(
            artifact.kind not in {"release", "release_approval", "release_gate"},
            "fixture cannot approve an actual release",
        )
        payload = fixture.payloads.get(artifact.relative_path)
        _require(payload is not None, "missing fixture payload", path=artifact.relative_path)
        assert payload is not None
        _require(sha256_text(payload) == artifact.sha256, "fixture payload hash mismatch", artifact_id=key[0])
        _require(
            len(payload.encode("utf-8")) == artifact.size_bytes,
            "fixture payload size mismatch",
            artifact_id=key[0],
        )
        paths.add(artifact.relative_path)
        for ref in artifact.depends_on:
            _require(ref.key in artifacts, "unresolved artifact dependency", artifact_id=key[0])
    _require(paths == set(fixture.payloads), "unregistered fixture payload")
    assert_dag({k: [r.key for r in a.depends_on] for k, a in artifacts.items()}, "artifacts")

    bundles = _unique(fixture.bundles, lambda b: b.bundle_id, "bundle_id")
    deliveries = _unique(fixture.deliveries, lambda d: d.delivery_id, "delivery_id")
    _unique(fixture.receipts, lambda r: r.receipt_id, "receipt_id")
    tasks = _unique(fixture.tasks, lambda t: t.task_id, "task_id")
    latest = latest_receipts(fixture.receipts)
    for receipt in fixture.receipts:
        _require(receipt.delivery_id in deliveries, "receipt references missing delivery")
        _require(receipt.project_id == project, "receipt project mismatch")

    for delivery in deliveries.values():
        check_delivery_against_bundle(delivery, bundles.get(delivery.bundle_ref.bundle_id), project)

    for bundle in bundles.values():
        check_bundle(bundle, bundles, deliveries, latest, artifacts, project)
    assert_dag({k: [r.bundle_id for r in b.input_bundle_refs] for k, b in bundles.items()}, "bundles")

    for task in tasks.values():
        check_task_inputs(task, deliveries, latest, tasks, project)
    assert_dag({k: list(t.depends_on) for k, t in tasks.items()}, "tasks")

    for artifact in artifacts.values():
        creator = artifact.created_by_task
        if creator is not None:
            _require(
                creator in tasks and tasks[creator].team_id.value == artifact.owner_team.value,
                "artifact creator ownership mismatch",
                artifact_id=artifact.artifact_id,
            )

    seen_attempts: set[str] = set()
    for result in fixture.results:
        _require(result.task_id in tasks, "result task missing", task_id=result.task_id)
        _require(result.attempt_id not in seen_attempts, "duplicate result attempt")
        seen_attempts.add(result.attempt_id)
        for ref in result.artifact_refs:
            _require(ref.key in artifacts, "result artifact missing")

    return {
        "payloads": len(paths),
        "artifacts": len(artifacts),
        "bundles": len(bundles),
        "deliveries": len(deliveries),
        "receipts": len(fixture.receipts),
        "tasks": len(tasks),
        "results": len(fixture.results),
    }


def check_delivery_against_bundle(delivery: Delivery, bundle: HandoffBundle | None, project: str) -> None:
    """A delivery must carry exactly its sealed bundle, on the bundle's own route."""
    _require(bundle is not None, "delivery bundle missing", delivery_id=delivery.delivery_id)
    assert bundle is not None
    _require(digest(bundle) == delivery.bundle_ref.sha256, "bundle hash mismatch", bundle_id=bundle.bundle_id)
    _require(delivery.project_id == bundle.project_id == project, "delivery project mismatch")
    _require(
        (delivery.from_team.value, delivery.to_team.value)
        == (bundle.source_team.value, bundle.target_team.value),
        "delivery route mismatch",
        delivery_id=delivery.delivery_id,
    )
    grants = {ref.key for ref in delivery.read_grants}
    manifest = {ref.key for ref in bundle.artifact_refs}
    _require(grants == manifest, "grants differ from exact bundle manifest", delivery_id=delivery.delivery_id)


def check_bundle(
    bundle: HandoffBundle,
    bundles: Mapping[str, HandoffBundle],
    deliveries: Mapping[str, Delivery],
    latest: Mapping[str, DeliveryReceipt],
    artifacts: Mapping[RefKey, Artifact],
    project: str,
) -> None:
    """Route, authorship/forwarding authorization and dependency closure of one bundle."""
    source, target = bundle.source_team, bundle.target_team
    _require(bundle.project_id == project, "bundle project mismatch", bundle_id=bundle.bundle_id)
    _require(not bundle.decision_refs, "fixture cannot claim engineering approvals")
    _require(
        route_allowed(bundle.kind.value, Principal(source.value), Team(target.value)),
        "route not registered for this bundle kind",
        bundle_id=bundle.bundle_id,
        kind=bundle.kind.value,
        route=f"{source.value}->{target.value}",
    )
    forwarded: set[RefKey] = set()
    for parent_ref in bundle.input_bundle_refs:
        parent = bundles.get(parent_ref.bundle_id)
        _require(parent is not None, "input bundle missing", bundle_id=bundle.bundle_id)
        assert parent is not None
        _require(digest(parent) == parent_ref.sha256, "input bundle hash mismatch")
        matching = [
            d
            for d in deliveries.values()
            if d.bundle_ref == parent_ref
            and d.to_team.value == source.value
            and latest.get(d.delivery_id) is not None
            and latest[d.delivery_id].status == ReceiptStatus.ACCEPTED
        ]
        _require(bool(matching), "forwarded input not accepted by source team", bundle_id=bundle.bundle_id)
        for d in matching:
            forwarded.update(r.key for r in d.read_grants)
    manifest = {ref.key for ref in bundle.artifact_refs}
    for key in manifest:
        _require(key in artifacts, "manifest artifact missing", artifact_id=key[0])
        artifact = artifacts[key]
        _require(
            artifact.owner_team == source or key in forwarded,
            "source neither owns nor has an accepted grant to forwarded artifact",
            bundle_id=bundle.bundle_id,
            artifact_id=key[0],
        )
        _require(
            {r.key for r in artifact.depends_on} <= manifest,
            "manifest omits dependency closure",
            artifact_id=key[0],
        )


def check_task_inputs(
    task: Task,
    deliveries: Mapping[str, Delivery],
    latest: Mapping[str, DeliveryReceipt],
    tasks: Mapping[str, Task],
    project: str,
) -> None:
    """A task may read only what its own team's accepted deliveries grant."""
    _require(task.project_id == project, "task project mismatch", task_id=task.task_id)
    grants: set[RefKey] = set()
    for delivery_id in task.input_deliveries:
        _require(delivery_id in deliveries, "task delivery missing", task_id=task.task_id)
        delivery = deliveries[delivery_id]
        _require(
            delivery.to_team == task.team_id,
            "task delivery belongs to another team",
            task_id=task.task_id,
            delivery_id=delivery_id,
        )
        receipt = latest.get(delivery_id)
        _require(
            receipt is not None and receipt.status == ReceiptStatus.ACCEPTED,
            "task input not accepted",
            delivery_id=delivery_id,
        )
        grants.update(r.key for r in delivery.read_grants)
    _require(
        {r.key for r in task.input_refs} <= grants,
        "task inputs exceed accepted delivery grants",
        task_id=task.task_id,
    )
    for dependency in task.depends_on:
        _require(
            dependency in tasks and tasks[dependency].team_id == task.team_id,
            "task dependency missing or cross-team",
            task_id=task.task_id,
            dependency=dependency,
        )


def _unique(items: Iterable[Any], key: Any, field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        k = key(item)
        _require(k not in result, f"duplicate {field}", value=k)
        result[k] = item
    return result


__all__ = ["check_fixture", "FixtureInvalid", "assert_dag", "latest_receipts"]
