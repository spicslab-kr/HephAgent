"""Build adapters from configured connections (one instance per connection name).

| connection type       | Brain adapter                    | research            |
|-----------------------|----------------------------------|---------------------|
| fixture               | FixtureBrain (recorded)          | FixtureResearch     |
| claude-cli            | ClaudeCliBrain (native login)    | none                |
| codex-cli             | CodexCliBrain (native login)     | none                |
| openai-responses      | OpenAIResponsesBrain (API key)   | none (M8: web tool) |
| anthropic-messages    | AnthropicMessagesBrain (API key) | none (M8: web tool) |
| openai-compatible     | OpenAICompatibleBrain (local)    | none                |

No silent fallback: asking for a connection that cannot be built raises
``capability_missing``. Coding backends (repository work) are a separate port and stay
blocked until the isolation launcher spike (see WORK_LOG).
"""

from __future__ import annotations

from pathlib import Path

from sdlc.adapters.brains.api import AnthropicMessagesBrain, OpenAICompatibleBrain, OpenAIResponsesBrain
from sdlc.adapters.brains.claude_cli import ClaudeCliBrain
from sdlc.adapters.brains.codex_cli import CodexCliBrain
from sdlc.adapters.brains.fixture import FixtureBrain, FixtureScript
from sdlc.adapters.coding.claude_cli import ClaudeCliCoding
from sdlc.adapters.coding.codex_cli import CodexCliCoding
from sdlc.adapters.coding.fixture import FixtureCoding
from sdlc.adapters.execution.local_runner import LocalProcessRunner
from sdlc.adapters.research.fixture import FixtureResearch
from sdlc.config import Connection, ConnectionType, ProjectConfig
from sdlc.domain.errors import CapabilityMissing
from sdlc.ports.brain import Brain
from sdlc.ports.coding import CodingBackend
from sdlc.ports.execution import ProcessRunner
from sdlc.ports.research import ResearchBackend


class AdapterRegistry:
    def __init__(self, config: ProjectConfig, project_root: Path, *, fixture_script: FixtureScript | None = None, fixture_research: dict | None = None) -> None:
        self.config = config
        self.root = project_root
        self._brains: dict[str, Brain] = {}
        self._research: dict[str, ResearchBackend] = {}
        self._coding: dict[str, CodingBackend] = {}
        self._runners: dict[str, ProcessRunner] = {}
        self._fixture_script = fixture_script
        self._fixture_research = fixture_research

    # ------------------------------------------------------------------ brains
    def brain(self, connection_name: str) -> Brain:
        if connection_name in self._brains:
            return self._brains[connection_name]
        connection = self.config.connections.get(connection_name)
        if connection is None:
            raise CapabilityMissing("connection is not configured", connection=connection_name)
        brain = self._build_brain(connection_name, connection)
        self._brains[connection_name] = brain
        return brain

    def _build_brain(self, name: str, connection: Connection) -> Brain:
        # Brain calls run in a throwaway directory under the system temp, never inside the
        # project or the host repository: native clients read AGENTS.md / CLAUDE.md from
        # their working directory upwards, and the orchestrator's own instructions must
        # not leak into a team's prompt.
        import tempfile

        scratch = Path(tempfile.gettempdir()) / "sdlc-brain"
        scratch.mkdir(parents=True, exist_ok=True)
        if connection.type == ConnectionType.FIXTURE:
            script = self._fixture_script
            if script is None and connection.script:
                script = FixtureScript.from_file(self.root / connection.script)
            return FixtureBrain(script)
        if connection.type == ConnectionType.CLAUDE_CLI:
            return ClaudeCliBrain(executable=connection.executable or "claude", model=connection.model, expected_account_id=connection.expected_account_id, scratch_root=scratch)
        if connection.type == ConnectionType.CODEX_CLI:
            return CodexCliBrain(executable=connection.executable or "codex", model=connection.model, expected_account_id=connection.expected_account_id, scratch_root=scratch)
        if connection.type == ConnectionType.OPENAI_RESPONSES:
            return OpenAIResponsesBrain(model=connection.model or "", credential_ref=connection.credential_ref)
        if connection.type == ConnectionType.ANTHROPIC_MESSAGES:
            return AnthropicMessagesBrain(model=connection.model or "", credential_ref=connection.credential_ref)
        if connection.type == ConnectionType.OPENAI_COMPATIBLE:
            return OpenAICompatibleBrain(model=connection.model or "", credential_ref=connection.credential_ref, base_url=connection.base_url)
        raise CapabilityMissing(f"no adapter for connection type {connection.type.value!r}", connection=name, type=connection.type.value)

    def register_brain(self, connection_name: str, brain: Brain) -> None:
        """Tests and live setups can inject an adapter instance directly."""
        self._brains[connection_name] = brain

    # ------------------------------------------------------------------ research
    def research(self, connection_name: str) -> ResearchBackend:
        if connection_name in self._research:
            return self._research[connection_name]
        connection = self.config.connections.get(connection_name)
        if connection is None:
            raise CapabilityMissing("connection is not configured", connection=connection_name)
        if connection.type != ConnectionType.FIXTURE:
            raise CapabilityMissing(
                f"no research adapter for connection type {connection.type.value!r}; import evidence instead (sdlc import ... research_evidence JSON)",
                connection=connection_name,
                type=connection.type.value,
            )
        script = self._fixture_research
        if script is None and self._fixture_script is not None and self._fixture_script.research:
            script = self._fixture_script.research
        if script is None and connection.script:
            from sdlc.verification.canonical import load_json_strict

            script = load_json_strict((self.root / connection.script).read_text(encoding="utf-8-sig")).get("research", {})
        backend = FixtureResearch(script)
        self._research[connection_name] = backend
        return backend

    def register_research(self, connection_name: str, backend: ResearchBackend) -> None:
        self._research[connection_name] = backend

    # ------------------------------------------------------------------ coding
    def coding(self, connection_name: str) -> CodingBackend:
        if connection_name in self._coding:
            return self._coding[connection_name]
        connection = self.config.connections.get(connection_name)
        if connection is None:
            raise CapabilityMissing("connection is not configured", connection=connection_name)
        if connection.type == ConnectionType.FIXTURE:
            script = self._fixture_script.coding if self._fixture_script is not None else {}
            backend: CodingBackend = FixtureCoding(script)
        elif connection.type == ConnectionType.CODEX_CLI:
            backend = CodexCliCoding(executable=connection.executable or "codex", model=connection.model)
        elif connection.type == ConnectionType.CLAUDE_CLI:
            backend = ClaudeCliCoding(executable=connection.executable or "claude", model=connection.model)
        else:
            raise CapabilityMissing(f"connection type {connection.type.value!r} cannot do repository work; bind codex-cli or claude-cli as the coding connection", connection=connection_name)
        self._coding[connection_name] = backend
        return backend

    def register_coding(self, connection_name: str, backend: CodingBackend) -> None:
        self._coding[connection_name] = backend

    # ------------------------------------------------------------------ execution
    def runner(self, connection_name: str, artifacts_writer=None, launcher=None) -> ProcessRunner:
        # A writer is attempt-specific (it registers stdout/stderr as artifacts of that
        # attempt), so a runner built with one is never cached or reused.
        if artifacts_writer is None and launcher is None and connection_name in self._runners:
            return self._runners[connection_name]
        connection = self.config.connections.get(connection_name)
        if connection is None:
            raise CapabilityMissing("connection is not configured", connection=connection_name)
        if connection.type not in (ConnectionType.FIXTURE, ConnectionType.LOCAL_PROCESS):
            raise CapabilityMissing(f"connection type {connection.type.value!r} is not an execution runner", connection=connection_name)
        runner: ProcessRunner = LocalProcessRunner(artifacts_writer=artifacts_writer, launcher=launcher)
        if artifacts_writer is None and launcher is None:
            self._runners[connection_name] = runner
        return runner
