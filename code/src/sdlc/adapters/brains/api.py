"""Direct API Brains: OpenAI Responses, Anthropic Messages, OpenAI-compatible local servers.

Per handoff docs/05 s6-7:

* official SDKs, explicit timeouts, ``max_retries=0`` (the runtime owns retries);
* credentials come from the configured ``credential_ref`` (``env:NAME``) only;
* a transport projection of the schema is sent, the reply is validated locally against
  the full contract by the runtime; refusal and truncation are distinct from valid data;
* usage is ``measured`` when the provider returns token counts (cost stays unknown
  unless a price table is configured; none is);
* local servers: Chat Completions compatibility, placeholder key labeled nonsecret.

The SDK clients are injectable so the adapters are fully unit-testable offline. The SDK
imports happen lazily; a missing SDK is a ``capability_missing`` reply, not a crash.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from sdlc.adapters.cli_common import classify, measured_usage, normalized_error, sanitize, unknown_usage
from sdlc.adapters.brains.claude_cli import _extract_json, _render_prompt
from sdlc.domain.models import CompletionCertainty, ErrorCategory
from sdlc.ports.brain import BrainReply, BrainRequest
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.verification.schemas import transport_projection

ClientFactory = Callable[[], Any]


def _credential(credential_ref: str | None) -> tuple[str | None, str | None]:
    """Resolve ``env:NAME`` to a value without ever logging it. Returns (value, variable_name)."""
    if credential_ref is None:
        return None, None
    if not credential_ref.startswith("env:"):
        raise ValueError("credential_ref must be env:NAME")
    name = credential_ref[4:]
    return os.environ.get(name), name


def _schema_for(request: BrainRequest) -> dict[str, Any]:
    schema = request.output_schema
    if "$ref" in schema and schema["$ref"].startswith("#/$defs/"):
        return transport_projection(schema["$ref"][len("#/$defs/") :])
    return schema


def _error_reply(exc: Exception, provider: dict[str, Any]) -> BrainReply:
    message = f"{type(exc).__name__}: {exc}"
    status = getattr(exc, "status_code", None)
    category = classify(message)
    if status == 429:
        category = ErrorCategory.QUOTA_EXHAUSTED if "quota" in message.lower() or "insufficient" in message.lower() else ErrorCategory.RATE_LIMITED
    elif status in (401, 403):
        category = ErrorCategory.AUTH_REQUIRED
    elif status and status >= 500:
        category = ErrorCategory.PROVIDER_UNAVAILABLE
    retry_after = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers and headers.get("retry-after", "").isdigit():
        retry_after = int(headers["retry-after"])
    certainty = CompletionCertainty.UNKNOWN if category in (ErrorCategory.PROVIDER_UNAVAILABLE, ErrorCategory.UNKNOWN) else CompletionCertainty.NOT_COMPLETED
    return BrainReply(None, None, normalized_error(category, message, provider_code=str(status) if status else type(exc).__name__, certainty=certainty, retry_after=retry_after), unknown_usage(), provider)


class _ApiBrainBase:
    adapter_id = "api-brain"
    connection_type = "api"
    sdk_module = ""
    cost_source = "api_account"

    def __init__(self, *, model: str, credential_ref: str | None, base_url: str | None = None, client_factory: ClientFactory | None = None, timeout_seconds: int = 120) -> None:
        self.model = model
        self.credential_ref = credential_ref
        self.base_url = base_url
        self._client_factory = client_factory
        self.timeout_seconds = timeout_seconds
        self.invocations = 0

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=self.adapter_id,
            adapter_version="1",
            connection_type=self.connection_type,
            auth_mode="api_key" if self.credential_ref else "local_profile",
            cost_source=self.cost_source,
            capabilities=(
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "provider JSON-schema output + local validation"),
                CapabilityStatus("diagram_source_generation", Support.SUPPORTED, "text output"),
                CapabilityStatus("usage_events", Support.SUPPORTED, "token counts; cost unknown"),
                CapabilityStatus("native_resume", Support.UNSUPPORTED, "fresh request from the context packet each time"),
                CapabilityStatus("scoped_repository", Support.UNSUPPORTED, "a Brain never provides repository tools"),
                CapabilityStatus("isolated_execution", Support.UNSUPPORTED),
                CapabilityStatus("research_or_imported_evidence", Support.UNSUPPORTED, "provider web search is a separate capability (not enabled)"),
            ),
            limits={"max_retries": 0, "timeout_seconds": self.timeout_seconds},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        messages: list[str] = []
        try:
            __import__(self.sdk_module)
        except ImportError:
            return ProbeResult(ok=False, live=False, messages=(f"SDK {self.sdk_module!r} not installed: pip install -e .[{self.sdk_module}]",))
        value, name = _credential(self.credential_ref)
        if self.credential_ref and not value:
            return ProbeResult(ok=False, live=False, messages=(f"environment variable {name} is not set (credential reference only; never put the key in config)",))
        messages.append(f"model {self.model}" + (f" at {self.base_url}" if self.base_url else ""))
        if not live:
            return ProbeResult(ok=True, live=False, messages=tuple(messages), identity="identity_unverified")
        try:
            reply = await self.generate(_probe_request())
        except Exception as exc:  # pragma: no cover - network
            return ProbeResult(ok=False, live=True, messages=(*messages, sanitize(str(exc))), identity="identity_unverified")
        if reply.error:
            return ProbeResult(ok=False, live=True, messages=(*messages, f"{reply.error.category.value}: {reply.error.message}"), identity="identity_unverified")
        return ProbeResult(ok=True, live=True, messages=(*messages, "structured probe reply received"), identity="identity_unverified")

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return self._build_client()

    def _build_client(self) -> Any:  # pragma: no cover - needs SDK
        raise NotImplementedError


def _probe_request() -> BrainRequest:
    from sdlc.domain.identities import AgentPrincipal
    from sdlc.domain.teams import Team

    return BrainRequest(
        principal=AgentPrincipal("probe", "probe", Team.PLANNING, "doctor"),
        task_id="probe",
        attempt_id="probe",
        operation="planning.intake",
        operation_version=1,
        system_prompt="Reply with a minimal valid object.",
        instructions="Return summary='probe' and empty lists.",
        documents=(),
        output_schema={"$ref": "#/$defs/WorkerProposal"},
        output_definition="WorkerProposal",
        max_output_tokens=300,
        timeout_seconds=60,
    )


# ----------------------------------------------------------------------------- OpenAI Responses


class OpenAIResponsesBrain(_ApiBrainBase):
    adapter_id = "openai-responses-brain"
    connection_type = "openai-responses"
    sdk_module = "openai"

    def _build_client(self) -> Any:  # pragma: no cover - needs SDK
        import openai

        value, _ = _credential(self.credential_ref)
        return openai.OpenAI(api_key=value, timeout=self.timeout_seconds, max_retries=0)

    async def generate(self, request: BrainRequest) -> BrainReply:
        self.invocations += 1
        provider: dict[str, Any] = {"adapter": self.adapter_id, "origin": "api", "model": self.model}
        schema = _schema_for(request)
        try:
            client = self._client()
            response = client.responses.create(
                model=self.model,
                instructions=request.system_prompt,
                input=_render_prompt(request),
                text={"format": {"type": "json_schema", "name": request.output_definition, "strict": True, "schema": schema}},
                max_output_tokens=request.max_output_tokens,
            )
        except ImportError:
            return BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, "openai SDK not installed", provider_code=None), unknown_usage(), provider)
        except Exception as exc:
            return _error_reply(exc, provider)
        provider["response_id"] = getattr(response, "id", None)
        usage_obj = getattr(response, "usage", None)
        usage = measured_usage(getattr(usage_obj, "input_tokens", None), getattr(usage_obj, "output_tokens", None), None)
        status = getattr(response, "status", "completed")
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "incomplete")
            category = ErrorCategory.CONTEXT_LIMIT if "max_output_tokens" in str(reason) else ErrorCategory.INVALID_OUTPUT
            return BrainReply(None, getattr(response, "output_text", None), normalized_error(category, f"response incomplete: {reason}", provider_code=str(reason)), usage, provider)
        refusal = _openai_refusal(response)
        if refusal:
            return BrainReply(None, refusal, normalized_error(ErrorCategory.INVALID_OUTPUT, f"model refused: {refusal}", provider_code="refusal"), usage, provider, refusal=refusal)
        text = getattr(response, "output_text", "") or ""
        structured = _extract_json(text)
        if structured is None:
            return BrainReply(None, text, normalized_error(ErrorCategory.INVALID_OUTPUT, "output_text is not a JSON object", provider_code="no_json"), usage, provider)
        return BrainReply(structured, text, None, usage, provider)


def _openai_refusal(response: Any) -> str | None:
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", "refused")
    return None


# ----------------------------------------------------------------------------- Anthropic Messages


class AnthropicMessagesBrain(_ApiBrainBase):
    adapter_id = "anthropic-messages-brain"
    connection_type = "anthropic-messages"
    sdk_module = "anthropic"

    def _build_client(self) -> Any:  # pragma: no cover - needs SDK
        import anthropic

        value, _ = _credential(self.credential_ref)
        return anthropic.Anthropic(api_key=value, timeout=self.timeout_seconds, max_retries=0)

    async def generate(self, request: BrainRequest) -> BrainReply:
        self.invocations += 1
        provider: dict[str, Any] = {"adapter": self.adapter_id, "origin": "api", "model": self.model}
        schema = _schema_for(request)
        try:
            client = self._client()
            message = client.messages.create(
                model=self.model,
                system=request.system_prompt,
                messages=[{"role": "user", "content": _render_prompt(request)}],
                max_tokens=request.max_output_tokens,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except ImportError:
            return BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, "anthropic SDK not installed", provider_code=None), unknown_usage(), provider)
        except Exception as exc:
            return _error_reply(exc, provider)
        provider["message_id"] = getattr(message, "id", None)
        usage_obj = getattr(message, "usage", None)
        usage = measured_usage(getattr(usage_obj, "input_tokens", None), getattr(usage_obj, "output_tokens", None), None)
        stop = getattr(message, "stop_reason", None)
        text = "".join(getattr(block, "text", "") for block in getattr(message, "content", []) or [] if getattr(block, "type", None) == "text")
        if stop == "refusal":
            return BrainReply(None, text, normalized_error(ErrorCategory.INVALID_OUTPUT, "model refused", provider_code="refusal"), usage, provider, refusal=text[:300] or "refused")
        if stop == "max_tokens":
            return BrainReply(None, text, normalized_error(ErrorCategory.CONTEXT_LIMIT, "output truncated at max_tokens", provider_code="max_tokens"), usage, provider)
        structured = _extract_json(text)
        if structured is None:
            return BrainReply(None, text, normalized_error(ErrorCategory.INVALID_OUTPUT, "message text is not a JSON object", provider_code="no_json"), usage, provider)
        return BrainReply(structured, text, None, usage, provider)


# ----------------------------------------------------------------------------- OpenAI-compatible local


class OpenAICompatibleBrain(_ApiBrainBase):
    """Chat Completions against a local or lab server (Ollama, LM Studio, ...)."""

    adapter_id = "openai-compatible-brain"
    connection_type = "openai-compatible"
    sdk_module = "openai"
    cost_source = "local"

    def _build_client(self) -> Any:  # pragma: no cover - needs SDK
        import openai

        value, _ = _credential(self.credential_ref)
        # An unauthenticated loopback server still needs a placeholder; it is nonsecret.
        return openai.OpenAI(api_key=value or "local-nonsecret-placeholder", base_url=self.base_url, timeout=self.timeout_seconds, max_retries=0)

    async def generate(self, request: BrainRequest) -> BrainReply:
        self.invocations += 1
        provider: dict[str, Any] = {"adapter": self.adapter_id, "origin": "local", "model": self.model, "base_url": self.base_url}
        schema = _schema_for(request)
        try:
            client = self._client()
            completion = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system_prompt + "\nRespond with a single JSON object only."},
                    {"role": "user", "content": _render_prompt(request)},
                ],
                max_tokens=request.max_output_tokens,
                response_format={"type": "json_schema", "json_schema": {"name": request.output_definition, "schema": schema}},
            )
        except ImportError:
            return BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, "openai SDK not installed", provider_code=None), unknown_usage(), provider)
        except Exception as exc:
            reply = _error_reply(exc, provider)
            if "response_format" in str(exc) or "json_schema" in str(exc):
                reply = BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, f"server rejects json_schema response_format: {sanitize(str(exc))}", provider_code="response_format"), unknown_usage(), provider)
            return reply
        usage_obj = getattr(completion, "usage", None)
        usage = measured_usage(getattr(usage_obj, "prompt_tokens", None), getattr(usage_obj, "completion_tokens", None), None)
        choice = (getattr(completion, "choices", None) or [None])[0]
        if choice is None:
            return BrainReply(None, None, normalized_error(ErrorCategory.INVALID_OUTPUT, "no choices returned", provider_code="no_choices"), usage, provider)
        finish = getattr(choice, "finish_reason", None)
        text = getattr(getattr(choice, "message", None), "content", "") or ""
        if finish == "length":
            return BrainReply(None, text, normalized_error(ErrorCategory.CONTEXT_LIMIT, "completion truncated (finish_reason=length)", provider_code="length"), usage, provider)
        structured = _extract_json(text)
        if structured is None:
            return BrainReply(None, text, normalized_error(ErrorCategory.INVALID_OUTPUT, "completion is not a JSON object", provider_code="no_json"), usage, provider)
        return BrainReply(structured, text, None, usage, provider)


__all__ = ["OpenAIResponsesBrain", "AnthropicMessagesBrain", "OpenAICompatibleBrain"]
