#!/usr/bin/env python3
"""Validate this design package, not an implementation or generated application.

Python 3.11+, standard library only. This intentionally limited JSON Schema
checker supports the keywords used by the included schema and rejects others.
Use a maintained Draft 2020-12 validator in the implemented runtime. SQL checks
load reference DDL in memory; they do not prove recovery, isolation, or routing.
Print one JSON report to stdout; write no files and contact no external services.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


class Invalid(ValueError):
    """Package validation failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Invalid(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def load_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_number(value: str) -> None:
        raise Invalid(f"non-JSON numeric constant: {value}")

    return json.loads(path.read_text(encoding="utf-8-sig"),
                      object_pairs_hook=pairs, parse_constant=invalid_number)


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return (left.keys() == right.keys()
                and all(json_equal(left[k], right[k]) for k in left))
    if isinstance(left, list) and isinstance(right, list):
        return (len(left) == len(right)
                and all(json_equal(a, b) for a, b in zip(left, right)))
    return left == right


def matches_type(value: Any, name: str) -> bool:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    finite = isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))
    kinds = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "null": value is None,
        "number": numeric and finite,
        "integer": numeric and finite and value == int(value),
    }
    require(name in kinds, f"unsupported schema type: {name}")
    return kinds[name]


def rfc3339(value: str) -> bool:
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})"
        r"(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))", value)
    if not match:
        return False
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    try:
        date(year, month, day)
    except ValueError:
        return False
    # RFC3339 admits leap-second syntax; this package does not maintain IERS data.
    return (hour <= 23 and minute <= 59 and second <= 60
            and (match[7] is None or (int(match[8]) <= 23 and int(match[9]) <= 59)))


class PackageSchema:
    KEYWORDS = {
        "$schema", "$id", "$defs", "$ref", "title", "description", "type",
        "const", "enum", "properties", "required", "additionalProperties",
        "items", "minItems", "minLength", "pattern", "minimum", "anyOf", "format",
    }

    def __init__(self, root: dict[str, Any]):
        self.root = root
        self.schema_nodes = 0
        self.audit(root, "schema")

    def resolve(self, ref: str) -> Any:
        require(ref.startswith("#/"), "only local JSON pointers are supported")
        value: Any = self.root
        for part in ref[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            require(isinstance(value, dict) and key in value, f"unknown $ref: {ref}")
            value = value[key]
        return value

    def audit(self, schema: Any, path: str) -> None:
        self.schema_nodes += 1
        if isinstance(schema, bool):
            return
        require(isinstance(schema, dict), f"{path}: schema is not an object/boolean")
        unknown = schema.keys() - self.KEYWORDS
        require(not unknown, f"{path}: unsupported schema keywords: {sorted(unknown)}")
        if "$ref" in schema:
            self.resolve(schema["$ref"])
        if "format" in schema:
            require(schema["format"] == "date-time", f"{path}: unsupported format")
        if "pattern" in schema:
            re.compile(schema["pattern"])
        for name in ("$defs", "properties"):
            for key, child in schema.get(name, {}).items():
                self.audit(child, f"{path}/{name}/{key}")
        for name in ("items", "additionalProperties"):
            if name in schema:
                self.audit(schema[name], f"{path}/{name}")
        for index, child in enumerate(schema.get("anyOf", [])):
            self.audit(child, f"{path}/anyOf/{index}")

    def validate(self, value: Any, schema: Any, path: str = "$") -> int:
        if isinstance(schema, bool):
            require(schema, f"{path}: prohibited by false schema")
            return 1
        count = 1
        if "$ref" in schema:
            count += self.validate(value, self.resolve(schema["$ref"]), path)
        if "type" in schema:
            kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            require(any(matches_type(value, kind) for kind in kinds), f"{path}: wrong type")
        if "const" in schema:
            require(json_equal(value, schema["const"]), f"{path}: const mismatch")
        if "enum" in schema:
            require(any(json_equal(value, item) for item in schema["enum"]),
                    f"{path}: enum mismatch")
        if "anyOf" in schema:
            matched = False
            for child in schema["anyOf"]:
                try:
                    count += self.validate(value, child, path)
                    matched = True
                    break
                except Invalid:
                    continue
            require(matched, f"{path}: no anyOf branch matches")
        if isinstance(value, dict):
            require(set(schema.get("required", [])) <= value.keys(),
                    f"{path}: required fields missing")
            props = schema.get("properties", {})
            additional = schema.get("additionalProperties", True)
            for key, item in value.items():
                count += self.validate(item, props.get(key, additional), f"{path}/{key}")
        if isinstance(value, list):
            require(len(value) >= schema.get("minItems", 0), f"{path}: too few items")
            if "items" in schema:
                for index, item in enumerate(value):
                    count += self.validate(item, schema["items"], f"{path}/{index}")
        if isinstance(value, str):
            require(len(value) >= schema.get("minLength", 0), f"{path}: string too short")
            if "pattern" in schema:
                require(re.search(schema["pattern"], value) is not None,
                        f"{path}: pattern mismatch")
            if schema.get("format") == "date-time":
                require(rfc3339(value), f"{path}: invalid RFC3339 date-time")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema:
                require(value >= schema["minimum"], f"{path}: below minimum")
        return count


def ref_key(ref: dict[str, Any]) -> tuple[str, int, str]:
    return ref["artifact_id"], ref["revision"], ref["sha256"]


def unique_index(items: list[dict[str, Any]], field: str) -> dict[str, Any]:
    result = {item[field]: item for item in items}
    require(len(result) == len(items), f"duplicate {field}")
    return result


def assert_dag(graph: dict[Any, list[Any]], label: str) -> None:
    active, visited = set(), set()

    def visit(node: Any) -> None:
        require(node in graph, f"{label}: unresolved dependency")
        require(node not in active, f"{label}: dependency cycle")
        if node in visited:
            return
        active.add(node)
        for dependency in graph[node]:
            visit(dependency)
        active.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def validate_cycle(fixture: dict[str, Any]) -> dict[str, int]:
    project = fixture["project_id"]
    artifacts = {ref_key(item): item for item in fixture["artifacts"]}
    require(len(artifacts) == len(fixture["artifacts"]), "duplicate artifact reference")
    require(len({key[:2] for key in artifacts}) == len(artifacts),
            "one artifact revision has multiple digests")
    require(fixture["fixture_mode"] is True, "example must remain fixture-only")
    paths = set()
    for key, artifact in artifacts.items():
        require(artifact["project_id"] == project, "artifact project mismatch")
        require(artifact["origin"] == "fixture", "fixture cannot claim actual evidence origin")
        require(artifact["kind"] not in {"release", "release_approval", "release_gate"},
                "fixture cannot approve an actual release")
        payload = fixture["payloads"].get(artifact["relative_path"])
        require(isinstance(payload, str), "missing fixture payload")
        data = payload.encode("utf-8")
        require(digest(data) == key[2], "fixture payload hash mismatch")
        require(len(data) == artifact["size_bytes"], "fixture payload size mismatch")
        paths.add(artifact["relative_path"])
        for ref in artifact["depends_on"]:
            require(ref_key(ref) in artifacts, "unresolved artifact dependency")
    require(paths == set(fixture["payloads"]), "unregistered fixture payload")
    assert_dag({key: [ref_key(r) for r in a["depends_on"]]
                for key, a in artifacts.items()}, "artifacts")
    bundles = unique_index(fixture["bundles"], "bundle_id")
    deliveries = unique_index(fixture["deliveries"], "delivery_id")
    unique_index(fixture["receipts"], "receipt_id")
    tasks = unique_index(fixture["tasks"], "task_id")
    latest: dict[str, Any] = {}
    sequences: set[tuple[str, int]] = set()
    for receipt in fixture["receipts"]:
        delivery_id = receipt["delivery_id"]
        require(delivery_id in deliveries, "receipt references missing delivery")
        require(receipt["project_id"] == project, "receipt project mismatch")
        sequence_key = delivery_id, receipt["sequence"]
        require(sequence_key not in sequences, "duplicate receipt sequence")
        sequences.add(sequence_key)
        if delivery_id not in latest or receipt["sequence"] > latest[delivery_id]["sequence"]:
            latest[delivery_id] = receipt
    for delivery in deliveries.values():
        bundle_ref = delivery["bundle_ref"]
        require(bundle_ref["bundle_id"] in bundles, "delivery bundle missing")
        bundle = bundles[bundle_ref["bundle_id"]]
        require(digest(canonical(bundle)) == bundle_ref["sha256"], "bundle hash mismatch")
        require(delivery["project_id"] == bundle["project_id"] == project,
                "delivery project mismatch")
        require((delivery["from_team"], delivery["to_team"]) ==
                (bundle["source_team"], bundle["target_team"]), "delivery route mismatch")
        grants = [ref_key(ref) for ref in delivery["read_grants"]]
        manifest = [ref_key(ref) for ref in bundle["artifact_refs"]]
        require(len(grants) == len(set(grants)), "duplicate grant")
        require(set(grants) == set(manifest), "grants differ from exact bundle manifest")
    order = ["planning", "requirements", "design", "implementation", "testing", "maintenance"]
    forward_routes = set(zip(order, order[1:]))
    for bundle_id, bundle in bundles.items():
        source, target = bundle["source_team"], bundle["target_team"]
        require(bundle["project_id"] == project, "bundle project mismatch")
        require(not bundle["decision_refs"], "fixture cannot claim engineering approvals")
        if bundle["kind"] == "forward":
            require((source, target) in forward_routes, "invalid forward stage route")
        if bundle["kind"] == "manual_input":
            require(source == "participant", "manual ingress must be participant-owned")
        require(source != target, "handoff must target a distinct team")
        forwarded = set()
        for parent_ref in bundle["input_bundle_refs"]:
            parent = bundles.get(parent_ref["bundle_id"])
            require(parent is not None, "input bundle missing")
            require(digest(canonical(parent)) == parent_ref["sha256"], "input bundle hash mismatch")
            matching = [d for d in deliveries.values()
                        if d["bundle_ref"] == parent_ref and d["to_team"] == source
                        and latest.get(d["delivery_id"], {}).get("status") == "accepted"]
            require(bool(matching), "forwarded input not accepted by source team")
            for delivery in matching:
                forwarded.update(ref_key(r) for r in delivery["read_grants"])
        manifest = [ref_key(ref) for ref in bundle["artifact_refs"]]
        require(len(manifest) == len(set(manifest)), "duplicate manifest reference")
        for key in manifest:
            require(key in artifacts, "manifest artifact missing")
            artifact = artifacts[key]
            require(artifact["owner_team"] == source or key in forwarded,
                    "source neither owns nor has an accepted grant to forwarded artifact")
            require({ref_key(r) for r in artifact["depends_on"]} <= set(manifest),
                    "manifest omits dependency closure")
    assert_dag({key: [r["bundle_id"] for r in b["input_bundle_refs"]]
                for key, b in bundles.items()}, "bundles")
    for task in tasks.values():
        require(task["project_id"] == project, "task project mismatch")
        require(task["operation"].startswith(task["team_id"] + "."),
                "task operation belongs to another team")
        grants = set()
        for delivery_id in task["input_deliveries"]:
            require(delivery_id in deliveries, "task delivery missing")
            delivery = deliveries[delivery_id]
            require(delivery["to_team"] == task["team_id"], "task delivery belongs to another team")
            require(latest.get(delivery_id, {}).get("status") == "accepted", "task input not accepted")
            grants.update(ref_key(r) for r in delivery["read_grants"])
        require({ref_key(r) for r in task["input_refs"]} <= grants,
                "task inputs exceed accepted delivery grants")
        for dependency in task["depends_on"]:
            require(dependency in tasks and tasks[dependency]["team_id"] == task["team_id"],
                    "task dependency missing or cross-team")
    assert_dag({key: t["depends_on"] for key, t in tasks.items()}, "tasks")
    for artifact in artifacts.values():
        creator = artifact["created_by_task"]
        if creator is not None:
            require(creator in tasks and tasks[creator]["team_id"] == artifact["owner_team"],
                    "artifact creator ownership mismatch")
    seen_attempts = set()
    for result in fixture["results"]:
        require(result["task_id"] in tasks, "result task missing")
        require(result["attempt_id"] not in seen_attempts, "duplicate result attempt")
        seen_attempts.add(result["attempt_id"])
        for ref in result["artifact_refs"]:
            require(ref_key(ref) in artifacts, "result artifact missing")
    return {"payloads": len(paths), "artifacts": len(artifacts), "bundles": len(bundles),
            "deliveries": len(deliveries), "receipts": len(fixture["receipts"]), "tasks": len(tasks)}


def safe_file(root: Path, relative: str) -> Path:
    require(not Path(relative).is_absolute() and "\\" not in relative,
            "package path must be relative POSIX notation")
    resolved = (root / relative).resolve()
    require(resolved.is_relative_to(root.resolve()), "path escapes package")
    require(resolved.is_file(), f"missing package file: {relative}")
    return resolved


def registry_checks(root: Path, validator: PackageSchema,
                    fixture: dict[str, Any]) -> dict[str, int]:
    registry = load_json(root / "contracts/operations.json")
    index: dict[tuple[str, int], Any] = {}
    teams = {"planning", "requirements", "design", "implementation", "testing", "maintenance"}
    for item in registry["operations"]:
        key = item["operation"], item["version"]
        require(key not in index, "duplicate operation/version registry key")
        require(item["owner_team"] in teams, "operation has unknown owner")
        require(item["operation"].startswith(item["owner_team"] + "."),
                "registry operation prefix does not match owner")
        require(isinstance(item["version"], int) and not isinstance(item["version"], bool)
                and item["version"] >= 1, "invalid operation version")
        validator.resolve(item["proposal_schema"])
        validator.audit(item["parameter_schema"], "operation/" + item["operation"])
        safe_file(root, item["domain_specification"])
        require(item["cross_team_reads"] == "accepted_delivery_grants_only",
                "operation expands cross-team access")
        index[key] = item
    for task in fixture["tasks"]:
        key = task["operation"], task["operation_version"]
        require(key in index, "fixture operation not registered")
        operation = index[key]
        require(task["team_id"] == operation["owner_team"], "fixture operation owner mismatch")
        output_ref = task["expected_output_schema"]
        if not output_ref.startswith("#/"):
            output_ref = "#/$defs/" + output_ref
        validator.resolve(output_ref)
        require(output_ref == operation["proposal_schema"], "fixture output schema mismatch")
        validator.validate(task["parameters"], operation["parameter_schema"], "task/parameters")
    return {"registered_operations": len(index), "fixture_task_parameters": len(fixture["tasks"])}


def markdown_checks(root: Path) -> dict[str, int]:
    files = list(root.rglob("*.md"))
    links, fences = 0, 0
    for path in files:
        opened: tuple[str, int] | None = None
        visible = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if match:
                marker, tail = match.groups()
                if opened is None:
                    opened = marker[0], len(marker)
                    fences += 1
                elif marker[0] == opened[0] and len(marker) >= opened[1] and not tail.strip():
                    opened = None
                continue
            if opened is None:
                visible.append(line)
        require(opened is None, f"unclosed Markdown fence: {path.relative_to(root)}")
        text = re.sub(r"`[^`\n]*`", "", "\n".join(visible))
        destinations = re.findall(r"\[[^\]\n]*\]\((<[^>]*>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", text)
        destinations += re.findall(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)", text, re.M)
        for destination in destinations:
            destination = destination.strip("<>")
            parsed = urlsplit(destination)
            if not parsed.path or parsed.scheme in {"http", "https", "mailto", "data"}:
                continue
            require(not parsed.scheme, f"unsupported local link URI: {destination}")
            target = (path.parent / unquote(parsed.path)).resolve()
            require(target.is_relative_to(root.resolve()) and target.exists(),
                    f"broken/escaping local Markdown link: {path.relative_to(root)} -> {destination}")
            links += 1
    return {"markdown_files": len(files), "fence_pairs": fences, "local_links": links}


def sql_checks(root: Path) -> dict[str, int]:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript((root / "contracts/state.sql").read_text(encoding="utf-8-sig"))
        require(connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "SQLite FK disabled")
        require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "SQLite FK check failed")
        require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed")
        count = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        require(count > 0, "SQL created no tables")
        return {"ddl_scripts": 1, "tables": count, "sqlite_integrity_checks": 2}


def source_checks(root: Path) -> dict[str, int]:
    python_files = list(root.rglob("*.py"))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    patterns = [re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}\b"),
                re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py", ".sql", ".txt"}:
            text = path.read_text(encoding="utf-8-sig")
            require(not any(p.search(text) for p in patterns),
                    f"possible hard-coded credential in {path.relative_to(root)} (value withheld)")
            count += 1
    return {"python_ast_files": len(python_files), "heuristic_secret_scan_files": count}


def manifest_checks(root: Path) -> dict[str, int | str]:
    path = root / "MANIFEST.json"
    if not path.exists():
        return {"manifests": 0, "availability": "not_present"}
    manifest = load_json(path)
    require(isinstance(manifest, dict) and isinstance(manifest.get("files"), list),
            "manifest must contain files array")
    seen = set()
    for entry in manifest["files"]:
        require(isinstance(entry, dict) and {"path", "sha256", "size_bytes"} <= entry.keys(),
                "invalid manifest entry")
        relative = entry["path"]
        require(relative not in seen and relative != "MANIFEST.json", "duplicate/self manifest entry")
        seen.add(relative)
        data = safe_file(root, relative).read_bytes()
        require(digest(data) == entry["sha256"], f"manifest digest mismatch: {relative}")
        require(len(data) == entry["size_bytes"], f"manifest size mismatch: {relative}")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
              and p.name != "MANIFEST.json" and "__pycache__" not in p.parts}
    require(seen == actual, "manifest inventory differs from package files")
    return {"manifests": 1, "manifest_files": len(seen)}


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    report: dict[str, Any] = {
        "scope": "design_package_only", "status": "pass", "checks": {}, "errors": [],
        "target_application_executed": False, "live_backends_tested": False,
        "limitations": ["Limited package JSON Schema subset; runtime needs maintained validator.",
                        "Markdown link scan is not a complete CommonMark parser; URL/anchor availability is not tested.",
                        "Credential scan is heuristic, not a security audit.",
                        "Date-time checks syntax/ranges, not historical leap-second validity.",
                        "Reference SQL execution does not test runtime recovery or authorization."],
    }

    def run(name: str, function: Any) -> Any:
        try:
            result = function()
            report["checks"][name] = {"status": "pass", **result}
            return result
        except Exception as error:
            report["status"] = "fail"
            report["checks"][name] = {"status": "fail"}
            report["errors"].append({"check": name, "message": str(error)})
            return None

    def contracts() -> dict[str, int]:
        validator = PackageSchema(load_json(root / "contracts/sdlc.schema.json"))
        mapping = {"minimal-cycle": "CycleFixture", "quota-task-result": "TaskResult",
                   "diagram-request": "DiagramRequest", "interrupted-checkpoint": "Checkpoint"}
        if (root / "examples/stale-delivery.json").exists():
            mapping["stale-delivery"] = "Delivery"
        require({p.stem for p in (root / "examples").glob("*.json")} == set(mapping),
                "example JSON inventory changed: assign each a named definition")
        nodes = 0
        values = {}
        for name, definition in mapping.items():
            values[name] = load_json(root / "examples" / (name + ".json"))
            nodes += validator.validate(values[name], {"$ref": "#/$defs/" + definition})
        run("fixture_semantics", lambda: validate_cycle(values["minimal-cycle"]))
        run("operation_registry", lambda: registry_checks(root, validator, values["minimal-cycle"]))

        def negative_checks() -> dict[str, int]:
            bad = copy.deepcopy(values["minimal-cycle"])
            bad["unexpected_schema_field"] = True
            try:
                validator.validate(bad, {"$ref": "#/$defs/CycleFixture"})
            except Invalid:
                pass
            else:
                raise Invalid("negative check did not reject unexpected schema field")
            bad = copy.deepcopy(values["minimal-cycle"])
            bad["artifacts"][0]["sha256"] = "0" * 64
            try:
                validate_cycle(bad)
            except Invalid:
                pass
            else:
                raise Invalid("negative check did not reject altered fixture hash")
            return {"negative_mutations_rejected": 2}

        run("negative_sanity", negative_checks)
        return {"schema_nodes_audited": validator.schema_nodes, "example_records": len(mapping),
                "instance_nodes_validated": nodes}

    run("contracts", contracts)
    run("sqlite", lambda: sql_checks(root))
    run("markdown", lambda: markdown_checks(root))
    run("source_hygiene", lambda: source_checks(root))
    run("manifest", lambda: manifest_checks(root))
    report["check_group_counts"] = dict(Counter(c["status"] for c in report["checks"].values()))
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
