# 05 — Backends, accounts and DIY connections

Status: implementation design, 9 October 2026 KST. Package: `sdlc` in repository `sdlc-agent-teams`. The connection interfaces below are requirements to implement, not working adapters or live-tested credentials. Provider documentation was checked for this handoff; pin and record the versions actually tested by the implementer.

## 1. Connection boundary

The six principals are exactly `planning`, `requirements`, `design`, `implementation`, `testing`, and `maintenance`. Each has distinct leader/worker sessions and workspaces. Reusing adapter code, a provider account, or model weights does **not** permit sharing conversational history or mutable project context.

Cross-team engineering information enters only through an immutable, versioned `HandoffBundle` and its authorized `Delivery`. The adapter receives only the task-local context materialized from those deliveries and same-team work. It cannot search another team's files, memory, sessions, vector index, or Git worktree. A handoff may include source code and evidence alongside specifications.

| Port | Responsibility | Initial implementations |
|---|---|---|
| `Brain` | Produce a typed proposal from an explicit context packet | Codex read-only bridge; OpenAI Responses; Anthropic Messages; local compatible endpoint; fixture |
| `CodingBackend` | Inspect or modify the task's repository snapshot | Codex CLI first; Claude Code CLI alternative |
| `ResearchBackend` | Retrieve evidence and provenance | Configured native search; provider web search; imported evidence |
| `ProcessRunner` | Run declared build/test recipes and capture observations | Isolated execution runner specified elsewhere in this package |

Changing a model endpoint does not provide repository tools, web access, or execution isolation. Keep these ports separate. MCP exposes application operations to a host; it does not replace these adapters or grant generic access to a student's subscription.

## 2. Public adapter contracts

All adapters use the canonical schemas in this package: `Task`, `TaskResult`, `Artifact`, `HandoffBundle`, `Delivery`, `BackendEvent`, and `NormalizedError`. Do not create provider-specific replacements for these records.

The following signatures describe responsibilities; helper request types are internal implementation details, not additional persisted contracts:

| Method | Required behavior |
|---|---|
| `describe()` | Report adapter/version, capabilities and enforcement limits without contacting the provider |
| `probe(live=False)` | Check installation/configuration; only `live=True` may invoke a model |
| `Brain.generate(request: BrainRequest)` | Return a candidate payload plus provider metadata; never commit task completion |
| `CodingBackend.start(job)` / `resume(job, checkpoint)` | Yield `BackendEvent` records incrementally |
| `CodingBackend.cancel(attempt_id)` | Stop the identified attempt; preserve partial outputs and report reconciliation needs |
| `ResearchBackend.retrieve(request: ResearchRequest)` | Return retrieved evidence, retrieval failures and limitations |
| `ProcessRunner.execute(request: ExecutionRequest)` | Return captured execution observations; no model-authenticated environment |

The runtime supplies principal, task/attempt identity, deadline, input hashes, output schema, capability requirements and effective permissions. The runtime validates outputs, registers `Artifact` records, constructs `TaskResult`, and owns state changes. Provider text cannot authorize itself or declare acceptance.

Every adapter declares capabilities individually: schema-constrained output, streaming, native resume, interrupt, repository reading/writing, research, usage events, permission mediation, and enforceable call/token limits. Use `supported`, `unsupported`, or `unknown` plus the probe/version evidence. An endpoint's compatibility claim is insufficient proof.

## 3. Presets and configuration

Provide these inspectable presets:

| Preset | Leaders/content workers | Repository workers | Research |
|---|---|---|---|
| `codex-native` | Codex read-only structured bridge | Codex | Configured, probed native search or imported evidence |
| `api-and-codex` | OpenAI or Anthropic API | Codex | Independently configured |
| `claude-code` | CLI bridge or direct API | Claude Code | Independently configured |
| `local` | Compatible local endpoint | Explicitly selected coding adapter | Separate search or imported evidence |
| `fixture` | Deterministic recorded outputs | Recorded proposals | Recorded/imported evidence |

`codex-native` is the preferred user experience once its launcher passes the isolation/authentication acceptance checks; `fixture` is the default for automated CI. A native profile without a verified launcher is capability-blocked, not ready for autonomous team tasks. Direct/local Brain adapters remain first-class contributions. No silent paid-provider fallback is permitted.

Configuration must include: adapter ID, executable or base URL, model ID, authentication mode, credential reference, time/output limits, permission profile and team/role bindings. Store environment-variable names or credential-store references, never secret values. Model identifiers are selected during setup and pinned in the resolved run configuration; do not assume every account has the same models.

Also configure a nonsecret account binding, `quota_scope_id`, and optional `expected_account_id`/workspace identifier. Compare expected identity with supported native status output before dispatch and on resume. A mismatch yields `needs_input` and zero model invocations. When the backend cannot expose identity, record `identity_unverified`; proceed only under a declared policy or explicit participant selection that accepts this uncertainty. Never label an unverified account as verified. These are connection metadata, not new `NormalizedError` categories.

`sdlc connection add` explains the selected billing source and writes a reviewable configuration. `sdlc doctor` performs local checks. `sdlc doctor --live` makes a small, explicit account/server request and records supported features. Implement installation instructions separately for supported operating systems; these commands belong to the proposed repository until implemented.

## 4. Codex connection: preferred implementation

Install the official CLI using its current platform instructions. In the selected isolated launcher environment, the participant runs:

```text
codex --version
codex login
codex login status
```

Codex documents ChatGPT sign-in and API-key access as separate modes. Native sign-in remains with the official client. For API-key login, the documented `codex login --with-api-key` takes the key through stdin. Never ask a student to paste credentials into a project file. An API key bills the API account; it is not a conversion of subscription allowance. [S1]

For the initial subprocess adapter, construct an argv array equivalent to:

```text
codex exec --json --sandbox read-only --output-schema RESULT_SCHEMA_PATH -
```

Use `workspace-write` for authorized coding tasks. Pass the complete prompt through stdin and set `cwd` to the isolated task repository. Codex documents JSONL events, schema output and explicit `codex exec resume SESSION_ID`. Use the persisted session identifier; never a global “last session” shortcut. The CLI normally requires a Git repository, which the materializer should create. [S2]

The Brain bridge receives a proposal schema and read-only inputs. The coding bridge receives bounded change instructions and returns proposal/evidence metadata. Parse terminal errors as well as exit status. A zero exit status or final assistant message cannot independently complete the `Task`.

A later app-server adapter can use initialization, `thread/start`, `turn/start`, `thread/resume`, and `turn/interrupt`, matching requests to stored thread/turn IDs. Its richer lifecycle can improve permissions and cancellation; it must implement the same contracts. Never fork an upstream team's conversation to create a downstream team. [S3]

## 5. Claude Code connection

Install the official CLI, inspect `claude --version`, and use its native account flow inside the selected isolated launcher environment. Current CLI documentation includes `claude auth login` and `claude auth status`; an adapter must check the installed version before assuming flag availability. [S4]

Documented invocation patterns include:

```text
claude -p "Process the supplied task context." --output-format stream-json --verbose
claude -p "Return the proposed result." --output-format json --json-schema SCHEMA_JSON
claude -p "Continue this task." --resume SESSION_ID
```

The real wrapper passes each argument separately, sends context via the supported stdin mechanism, and extracts structured output from the provider wrapper rather than parsing the whole wrapper as the application result. Test the chosen schema/stream combination against the pinned CLI. [S5]

Configure tool availability and automatic approval separately: an automatic-approval list is not necessarily a tool whitelist. Do not use permission-bypass flags as the default. Unsupported permission requirements pause the task. [S4]

Keep individual local CLI use distinct from embedding an authenticated service. Anthropic's Agent SDK overview instructs third-party developers to use API-key authentication unless offering Claude account login has been approved. This repository must not implement its own Claude OAuth, extract native tokens, or promise that every Claude plan supplies generic API access. Native client authentication and embedded SDK/API mode are separate connection options. [S6]

## 6. Direct API Brains

Use official SDKs with explicit timeouts and `max_retries=0`; the runtime owns retries and their budgets. Construct fresh requests from the authorized context packet. Server conversation IDs, where used, are scoped to the same task/team.

| Adapter | Credential source | API shape to implement |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` reference | `client.responses.create(model=..., input=..., text={"format": {"type": "json_schema", "name": ..., "strict": True, "schema": ...}}, max_output_tokens=...)` |
| Anthropic | `ANTHROPIC_API_KEY` reference | `client.messages.create(model=..., system=..., messages=..., max_tokens=..., output_config={"format": {"type": "json_schema", "schema": ...}})` |

The OpenAI Responses structured-output field is `text.format`. Treat refusal and incomplete output separately from valid parsed data. [S7] Anthropic's current raw Messages field is `output_config.format`; avoid copying the older `output_format` raw-request field. Its documentation also identifies a citations/strict-JSON incompatibility, so retrieval and typed synthesis should be separate calls when needed. [S8]

Project schemas can be richer than provider-supported JSON Schema subsets. Generate a supported transport projection, then validate the returned object against the full application schema and domain rules. Missing capabilities must not silently weaken gates. Preserve rejected candidates and report bounded repair attempts.

## 7. DIY local models

1. Install a local server and a model suitable for available RAM/VRAM.
2. Start the server and obtain its actual model identifier and endpoint.
3. Configure the compatible Brain adapter, URL, model and credential reference.
4. Probe a small structured response, truncation behavior and any required tools.
5. Run fixture-based adapter tests, then a small live task before a full SDLC cycle.

Ollama documents an OpenAI-compatible base URL such as `http://localhost:11434/v1/`; its compatibility example uses an ignored placeholder key. Select the wire API explicitly and test that server/model combination rather than assuming all OpenAI features work. [S9] LM Studio's documented example uses `http://localhost:1234/v1`; obtain the loaded model ID from the local server. [S10] When LM Studio authentication is enabled, configure its actual API token; a dummy key then does not suffice. [S11]

Prefer Chat Completions compatibility for the initial local Brain where verified. Supporting a Responses endpoint is a separate capability. For an unauthenticated loopback server, label any SDK-required placeholder as nonsecret. For a remote lab server use its configured authentication; never forward a cloud-provider secret to an arbitrary custom URL.

A local Brain does not become a CodingBackend. Supply a tested local coding agent or separately configure a supported coding client/provider route and verify editing, tool, session and isolation behavior. Otherwise repository tasks pause as `capability_missing`; students can complete them manually. Model OOM or server unavailability preserves the task and suggests a smaller model, smaller context or restored server.

## 8. Evidence and research adapters

Provider-native web search is optional: OpenAI documents a Responses web-search tool and Anthropic documents its web-search tool separately. Configure supported model/tool versions and record actual tool results, source URLs and retrieved spans. [S12, S13]

Return evidence artifacts with query, retrieval time, source locator, claim supported, origin and limitations. A model remembering a URL is not retrieval. Imported evidence is a valid alternative. Role-play remains labeled synthetic; successful search does not turn generated customer opinions into measurements.

## 9. Process, isolation and recovery requirements

The session ownership key is exactly project/cycle/team/logical-task/profile/account-binding/workspace-lineage. Pin the profile's resolved backend configuration hash; record worker and attempt IDs in associated invocation records. Resume may reuse a native handle only across attempts of the **same logical task** with the full ownership key unchanged and the workspace reconciled. A successor task, including a rerun of a terminal task, starts a fresh session. Changed provider, account binding or profile also requires a fresh session. Only the runtime resolves handles; reject mismatched ownership before contacting the backend. Rebuild fresh sessions from durable allowed inputs, outputs and reconciled partial work, never from another backend's private transcript.

The ProcessRunner must drain stdout/stderr concurrently, bound event sizes, preserve ordered diagnostic references, enforce deadlines, stop the process tree, and hold a single-writer lease. Use argv with `shell=False`; this alone is not isolation. Persist start intent before dispatch. On uncertain interruption, reconcile the process and filesystem before retrying; remote requests may already have consumed usage.

Restrict filesystem access to delivered inputs, this team's task workspace and necessary tools. Separate or disable cross-project memory, automatic session discovery, unreviewed plugins and broad MCP connections. Inspect effective instruction/configuration sources. Sharing an account is allowed, but the credential mechanism must not expose sibling teams' transcripts or mutable state. If a CLI/profile cannot enforce this boundary, return `capability_missing` and require a controlled runner/profile. Do not claim isolation from merely changing `cwd`.

Before enabling native support, run a launcher spike covering official login/status, same-task resume, cross-team file/session/memory canaries and secret isolation from generated-code execution. Record the OS/container image, CLI version and effective settings. One candidate is official login inside a scoped container profile with runtime-controlled persistent client state; its viability is unverified until the spike passes. Another is a supported brokered credential route. Do not parse or copy arbitrary native auth files, mount the host home/session store into every team, or invent a token-extraction workaround. A failed/unavailable spike leaves the native profile capability-blocked and offers a verified profile or manual work.

Build/test execution receives neither provider credentials nor native session stores. Construct child environments explicitly; inherited API variables must not silently override a chosen native account or billing mode. Inspect supported authentication status without reading/copying native credential files. Exported diagnostics redact secrets; usage/cost fields may remain unknown.

## 10. Failure normalization and acceptance

Preserve original status/code, sanitized message, retry/reset metadata, request/session IDs and partial-output references in the canonical records. The following is routing policy, not a new error schema:

| `NormalizedError` category | Runtime response |
|---|---|
| `rate_limited` | Bounded `retry_wait` with provider delay/jitter; then `paused` |
| `quota_exhausted` | `paused`; preserve completed work; show reset only if supplied |
| `auth_required` | `paused`; explain the native-login or credential action |
| `provider_unavailable` | Bounded retry when replay is safe; otherwise reconcile/pause |
| `invalid_output` | Preserve candidate; bounded repair; then `needs_input` or `failed` |
| `context_limit` | Reduce context with preserved provenance; otherwise `needs_input` |
| `permission_required` | `needs_input`; show requested operation and scope |
| `capability_missing` | `needs_input`; explain adapter/manual alternatives |
| `cancelled` | `cancelled` for deliberate cancellation; checkpoint partial work |
| `unknown` | Preserve diagnostics; avoid blind coding replay; `paused` |

HTTP 429 alone does not distinguish temporary throttling from exhausted credit. A missing terminal event is not completion: record `interrupted`, reconcile, then resume safely. Only runtime validation can set `completed`.

Circuit breakers use nonsecret `quota_scope_id`, independent of team context. Derive or configure its account/organization/endpoint/model scope only where provider evidence supports those limits; broad account quota must not be partitioned into model scopes. Configuration aliases using the same account limit explicitly share a scope. When scope/identity is unknown, do not infer independent allowance: retain the known pause, flag unresolved alias relationships and require a configured resolution before dispatching a potentially equivalent connection. Never rotate profiles/accounts automatically to evade a quota pause. A participant may deliberately select an independently authorized alternative; preserve the original pause and start a fresh attempt/session with new provenance.

Required adapter acceptance fixtures: malformed/truncated JSON, refusal, authentication failure, rate versus quota failure, session mismatch, partial coding edits, stream loss, process crash, unknown cost, unavailable local model and attempted cross-team file/session access. Demonstrate one successful live connection per supported adapter/version separately; fixture success is not live verification.

## Sources

These links establish provider capabilities; isolation, contracts and routing above are this project's design decisions.

- S1: [Codex authentication](https://developers.openai.com/codex/auth)
- S2: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- S3: [Codex app-server](https://developers.openai.com/codex/app-server)
- S4: [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- S5: [Claude Code programmatic use](https://code.claude.com/docs/en/headless)
- S6: [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- S7: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- S8: [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- S9: [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- S10: [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat)
- S11: [LM Studio authentication](https://lmstudio.ai/docs/developer/core/authentication)
- S12: [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- S13: [Anthropic web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
