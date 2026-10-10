"""Connection management (handoff docs/05 s3): add, bind, probe, record.

``add`` writes a reviewable connection into ``project.yaml`` with credential
*references* only. ``bind`` points a team role at a connection and, when the connection
is live, turns ``fixture_mode`` off. ``record_probe`` journals the result of a doctor
probe as a ``connection.probed`` event; the scheduler dispatches to a native or API
connection only after a successful live probe of the same executable version has been
recorded (readiness ``capability_check_required`` is a check to perform, not to skip).
"""

from __future__ import annotations

import dataclasses

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sdlc.application.projects import CONFIG_FILENAME, ProjectContext
from sdlc.config import Connection, ConnectionType, ProjectConfig, dump_config
from sdlc.domain.errors import Conflict, InvalidSchema
from sdlc.domain.teams import TEAM_ORDER, Team
from sdlc.ports.common import ProbeResult
from sdlc.storage import repositories as repo

AUTH_FOR_TYPE = {
    ConnectionType.FIXTURE: "none",
    ConnectionType.CODEX_CLI: "native",
    ConnectionType.CLAUDE_CLI: "native",
    ConnectionType.OPENAI_RESPONSES: "api_key",
    ConnectionType.ANTHROPIC_MESSAGES: "api_key",
    ConnectionType.OPENAI_COMPATIBLE: "local_profile",
    ConnectionType.LOCAL_PROCESS: "none",
}
DEFAULT_EXECUTABLE = {ConnectionType.CODEX_CLI: "codex", ConnectionType.CLAUDE_CLI: "claude"}
DEFAULT_CREDENTIAL = {ConnectionType.OPENAI_RESPONSES: "env:OPENAI_API_KEY", ConnectionType.ANTHROPIC_MESSAGES: "env:ANTHROPIC_API_KEY"}


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    connection: str
    ok: bool
    live: bool
    version: str | None
    identity: str
    messages: tuple[str, ...]
    recorded_at: str


class ConnectionService:
    def __init__(self, context: ProjectContext) -> None:
        self.ctx = context

    # ------------------------------------------------------------------ configuration
    def add(
        self,
        name: str,
        *,
        type: ConnectionType,
        model: str | None = None,
        executable: str | None = None,
        base_url: str | None = None,
        credential_ref: str | None = None,
        quota_scope_id: str | None = None,
        expected_account_id: str | None = None,
        replace: bool = False,
    ) -> Connection:
        raw = self._raw()
        if name in raw["connections"] and not replace:
            raise Conflict("connection already exists; pass replace to overwrite", connection=name)
        entry: dict[str, Any] = {"type": type.value, "auth": AUTH_FOR_TYPE[type], "quota_scope_id": quota_scope_id or f"{name}-scope"}
        if type in DEFAULT_EXECUTABLE:
            entry["executable"] = executable or DEFAULT_EXECUTABLE[type]
        if type in DEFAULT_CREDENTIAL:
            entry["credential_ref"] = credential_ref or DEFAULT_CREDENTIAL[type]
        if type == ConnectionType.OPENAI_COMPATIBLE:
            entry["base_url"] = base_url or "http://127.0.0.1:11434/v1"
            if credential_ref:
                entry["credential_ref"] = credential_ref
        if model:
            entry["model"] = model
        if expected_account_id:
            entry["expected_account_id"] = expected_account_id
        raw["connections"][name] = entry
        config = self._write(raw)
        return config.connections[name]

    def bind(self, *, role: str, connection: str, teams: list[Team] | None = None) -> ProjectConfig:
        raw = self._raw()
        if connection not in raw["connections"]:
            raise InvalidSchema("unknown connection", connection=connection)
        targets = teams or list(TEAM_ORDER)
        for team in targets:
            raw["teams"][team.value][role] = connection
        if raw["connections"][connection]["type"] != ConnectionType.FIXTURE.value:
            raw["project"]["fixture_mode"] = False
        return self._write(raw)

    def list(self) -> dict[str, dict[str, Any]]:
        probes = self.latest_probes()
        return {
            name: {
                "type": conn.type.value,
                "auth": conn.auth.value,
                "model": conn.model,
                "readiness": conn.readiness.value,
                "placeholders": conn.placeholders,
                "probe": dataclasses.asdict(probes[name]) if name in probes else None,
                "dispatchable": self.dispatchable(name),
            }
            for name, conn in self.ctx.config.connections.items()
        }

    # ------------------------------------------------------------------ probes
    def record_probe(self, connection: str, result: ProbeResult) -> None:
        with self.ctx.db.transaction():
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                "connection.probed",
                {"connection": connection, "ok": result.ok, "live": result.live, "version": result.version, "identity": result.identity, "messages": list(result.messages)},
                self.ctx.clock(),
            )

    def latest_probes(self) -> dict[str, ProbeRecord]:
        latest: dict[str, ProbeRecord] = {}
        for event in repo.list_events(self.ctx.db, self.ctx.project_id, "connection.probed"):
            p = event["payload"]
            latest[p["connection"]] = ProbeRecord(p["connection"], p["ok"], p["live"], p.get("version"), p.get("identity", "identity_unverified"), tuple(p.get("messages", [])), event["occurred_at"])
        return latest

    def dispatchable(self, connection: str) -> tuple[bool, str]:
        """Fixture connections always; others only after a successful *live* probe."""
        conn = self.ctx.config.connections.get(connection)
        if conn is None:
            return False, "unknown connection"
        if conn.type in (ConnectionType.FIXTURE, ConnectionType.LOCAL_PROCESS):
            return True, conn.type.value
        if conn.placeholders:
            return False, f"placeholders remain: {', '.join(conn.placeholders)}"
        probe = self.latest_probes().get(connection)
        if probe is None or not probe.live:
            return False, "no live probe recorded; run `sdlc doctor --project NAME --live`"
        if not probe.ok:
            return False, f"last live probe failed: {'; '.join(probe.messages)[:200]}"
        if probe.identity == "mismatch":
            return False, "logged-in account differs from expected_account_id"
        return True, f"live probe ok ({probe.version}, identity {probe.identity})"

    # ------------------------------------------------------------------ helpers
    def _path(self) -> Path:
        return self.ctx.root / CONFIG_FILENAME

    def _raw(self) -> dict[str, Any]:
        return yaml.safe_load(self._path().read_text(encoding="utf-8"))

    def _write(self, raw: dict[str, Any]) -> ProjectConfig:
        try:
            config = ProjectConfig.model_validate(raw)
        except ValueError as exc:
            raise InvalidSchema("resulting configuration is invalid", errors=[str(exc)]) from exc
        self._path().write_text(dump_config(config), encoding="utf-8")
        self.ctx.config = config
        with self.ctx.db.transaction():
            self.ctx.db.execute(
                "UPDATE projects SET configuration_json=?, revision=revision+1, updated_at=? WHERE project_id=?",
                (__import__("json").dumps(config.model_dump(mode="json"), sort_keys=True), self.ctx.clock(), self.ctx.project_id),
            )
        return config
