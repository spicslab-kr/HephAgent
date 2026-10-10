"""Project configuration (``config/project.example.yaml``), strictly validated.

Rules from handoff docs/05 section 3:

* store credential *references* (``env:NAME``), never secret values;
* placeholders such as ``UNCONFIGURED`` or ``USER_SELECTED_MODEL`` mean the connection is
  blocked until a capability probe passes, not that the check may be skipped;
* every team binds to named connections; a Brain binding is mandatory for all six.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sdlc.domain.errors import InvalidSchema
from sdlc.domain.teams import TEAM_ORDER, Team

PLACEHOLDER_MARKERS: tuple[str, ...] = ("UNCONFIGURED", "USER_SELECTED_", "USER_DECLARED_", "USER_INSTALLED_")


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER_MARKERS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectionType(StrEnum):
    FIXTURE = "fixture"
    CODEX_CLI = "codex-cli"
    CLAUDE_CLI = "claude-cli"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OPENAI_COMPATIBLE = "openai-compatible"
    LOCAL_PROCESS = "local-process"


class AuthMode(StrEnum):
    NONE = "none"
    NATIVE = "native"
    API_KEY = "api_key"
    LOCAL_PROFILE = "local_profile"


#: Which roles (ports) each connection type can serve. A CodingBackend is not
#: automatically a Brain and a Brain never provides repository tools (docs/05 s1).
ROLE_SUPPORT: dict[ConnectionType, frozenset[str]] = {
    ConnectionType.FIXTURE: frozenset({"brain", "coding", "research", "execution"}),
    ConnectionType.CODEX_CLI: frozenset({"brain", "coding"}),
    ConnectionType.CLAUDE_CLI: frozenset({"brain", "coding"}),
    ConnectionType.OPENAI_RESPONSES: frozenset({"brain", "research"}),
    ConnectionType.ANTHROPIC_MESSAGES: frozenset({"brain", "research"}),
    ConnectionType.OPENAI_COMPATIBLE: frozenset({"brain"}),
    ConnectionType.LOCAL_PROCESS: frozenset({"execution"}),
}


class Readiness(StrEnum):
    READY = "ready"
    CAPABILITY_CHECK_REQUIRED = "capability_check_required"
    UNCONFIGURED = "unconfigured"


class Connection(Strict):
    type: ConnectionType
    auth: AuthMode
    quota_scope_id: str = Field(min_length=1)
    executable: str | None = None
    base_url: str | None = None
    model: str | None = None
    credential_ref: str | None = None
    expected_account_id: str | None = None
    launcher_profile: str | None = None
    status: str | None = None
    capabilities: Literal["probe"] | None = None
    #: Fixture connections only: path (relative to the project directory) of a fixture
    #: script with recorded proposals/errors. Omitted = every call is unscripted.
    script: str | None = None

    @model_validator(mode="after")
    def _shape_for_type(self) -> "Connection":
        t = self.type
        if self.script is not None and t != ConnectionType.FIXTURE:
            raise ValueError("script is only valid for fixture connections")
        if t in (ConnectionType.FIXTURE, ConnectionType.LOCAL_PROCESS):
            if self.auth != AuthMode.NONE:
                raise ValueError(f"{t.value} connections use auth: none")
        elif t in (ConnectionType.CODEX_CLI, ConnectionType.CLAUDE_CLI):
            if self.auth != AuthMode.NATIVE:
                raise ValueError(f"{t.value} must use auth: native (login stays with the official client)")
            if not self.executable:
                raise ValueError(f"{t.value} requires an executable name")
        elif t in (ConnectionType.OPENAI_RESPONSES, ConnectionType.ANTHROPIC_MESSAGES):
            if self.auth != AuthMode.API_KEY:
                raise ValueError(f"{t.value} must use auth: api_key")
            if not self.credential_ref:
                raise ValueError(f"{t.value} requires a credential_ref such as env:OPENAI_API_KEY")
        elif t == ConnectionType.OPENAI_COMPATIBLE:
            if self.auth != AuthMode.LOCAL_PROFILE:
                raise ValueError("openai-compatible connections use auth: local_profile")
            if not self.base_url:
                raise ValueError("openai-compatible connections require base_url")
        if self.credential_ref is not None and not self.credential_ref.startswith("env:"):
            raise ValueError("credential_ref must be a reference like env:NAME, never a secret value")
        return self

    @property
    def placeholders(self) -> list[str]:
        return [
            name
            for name in ("model", "launcher_profile", "quota_scope_id", "executable", "base_url")
            if is_placeholder(getattr(self, name))
        ]

    @property
    def readiness(self) -> Readiness:
        if self.type in (ConnectionType.FIXTURE, ConnectionType.LOCAL_PROCESS):
            return Readiness.READY
        if self.placeholders:
            return Readiness.UNCONFIGURED
        if self.type in (ConnectionType.CODEX_CLI, ConnectionType.CLAUDE_CLI, ConnectionType.OPENAI_COMPATIBLE):
            # Native clients need the isolation launcher spike; local servers need a probe.
            return Readiness.CAPABILITY_CHECK_REQUIRED
        return Readiness.CAPABILITY_CHECK_REQUIRED

    def supports_role(self, role: str) -> bool:
        return role in ROLE_SUPPORT[self.type]


class RetryPolicy(Strict):
    initial_seconds: int = Field(ge=0)
    maximum_seconds: int = Field(ge=0)
    total_elapsed_seconds: int = Field(ge=0)
    jitter: bool


class RuntimeConfig(Strict):
    isolation: Literal["strict", "development"]
    launcher_profile: str
    max_parallel_workers: int = Field(ge=1)
    allow_recursive_workers: bool
    max_attempts: int = Field(ge=1)
    automatic_provider_fallback: Literal[False]
    default_timeout_seconds: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    daily_budget_microunits: int = Field(ge=0)
    currency: str = Field(min_length=1)
    retry: RetryPolicy


class ProjectSection(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    mode: Literal["collaborative", "continuous"]
    language: str = Field(min_length=1)
    fixture_mode: bool


class TeamBindings(Strict):
    brain: str = Field(min_length=1)
    research: str | None = None
    coding: str | None = None
    execution: str | None = None

    def items(self) -> list[tuple[str, str]]:
        return [(role, name) for role in ("brain", "research", "coding", "execution") if (name := getattr(self, role))]


class ImportsConfig(Strict):
    max_file_bytes: int = Field(ge=1)
    max_pdf_pages: int = Field(ge=1)
    max_archive_expanded_bytes: int = Field(ge=1)
    ocr: Literal["optional", "required", "disabled"]


class DiagramsConfig(Strict):
    renderers: list[Literal["mermaid", "plantuml"]]
    remote_includes: Literal[False]


class ExecutionConfig(Strict):
    network_during_tests: bool
    provider_credentials_in_target_process: Literal[False]
    dependency_preparation_requires_policy: bool
    #: Registered check recipes: name -> argument array run inside the candidate workspace.
    #: ``{python}`` is replaced with the interpreter running sdlc (the project environment).
    recipes: dict[str, list[str]] = Field(default_factory=lambda: {"pytest": ["{python}", "-m", "pytest", "-v", "-p", "no:cacheprovider"]})


class LauncherConfig(Strict):
    """An isolation launcher profile (handoff docs/06 s9): a container boundary for executions."""

    type: Literal["docker"]
    image: str = Field(min_length=1)
    python: str = "python3"  # interpreter inside the image, substituted for {python}
    executable: str = "docker"
    network: Literal[False] = False


class ProjectConfig(Strict):
    schema_version: Literal["1.0"]
    project: ProjectSection
    runtime: RuntimeConfig
    connections: dict[str, Connection]
    teams: dict[Team, TeamBindings]
    imports: ImportsConfig
    diagrams: DiagramsConfig
    execution: ExecutionConfig
    launchers: dict[str, LauncherConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bindings_resolve(self) -> "ProjectConfig":
        missing = [t.value for t in TEAM_ORDER if t not in self.teams]
        if missing:
            raise ValueError(f"every team needs bindings; missing {missing}")
        extra = [t for t in self.teams if t not in TEAM_ORDER]
        if extra:
            raise ValueError(f"unknown teams {extra}")
        for team, bindings in self.teams.items():
            for role, name in bindings.items():
                conn = self.connections.get(name)
                if conn is None:
                    raise ValueError(f"team {team.value}: {role} refers to unknown connection {name!r}")
                if not conn.supports_role(role):
                    raise ValueError(
                        f"team {team.value}: connection {name!r} ({conn.type.value}) cannot serve role {role!r}"
                    )
        if self.project.fixture_mode:
            non_fixture = {
                f"{team.value}.{role}": name
                for team, b in self.teams.items()
                for role, name in b.items()
                if self.connections[name].type != ConnectionType.FIXTURE
            }
            if non_fixture:
                raise ValueError(f"fixture_mode projects may only bind fixture connections; found {non_fixture}")
        if self.runtime.isolation == "strict" and is_placeholder(self.runtime.launcher_profile):
            # Allowed: documents can be generated; repository jobs will be capability-blocked.
            pass
        return self

    def connection_for(self, team: Team, role: str) -> Connection | None:
        name = getattr(self.teams[team], role)
        return self.connections[name] if name else None


def load_config(path: Path) -> ProjectConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InvalidSchema(f"configuration is not valid YAML: {exc}", path=str(path)) from exc
    if not isinstance(raw, dict):
        raise InvalidSchema("configuration must be a mapping", path=str(path))
    try:
        return ProjectConfig.model_validate(raw)
    except ValueError as exc:
        raise InvalidSchema("configuration is invalid", path=str(path), errors=[str(exc)]) from exc


def dump_config(config: ProjectConfig) -> str:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
