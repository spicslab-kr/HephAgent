# 02. Software requirements specification

## Normative terms and assumptions

MUST denotes a required behavior for the first complete release. SHOULD denotes a default that may be replaced with a documented equivalent. OPTIONAL features cannot silently become prerequisites. Requirement IDs are stable within this design baseline.

The target software is the SDLC orchestration system. Its generated application has its own requirements and tests. This document fixes requirements for the orchestration system; the reservation fixture illustrates its behavior.

The environment is single-project-owner local execution with an optional human reviewer. Account/provider limitations, missing tools and ambiguous documents are normal conditions. The design does not assume that a model produces valid content, an external request executes exactly once or a native session lasts indefinitely.

## Actors and user journeys

Primary actor: participant, who supplies the brief, selects accounts/backends and makes or delegates decisions. Supporting actors: supervisor, human contributor, provider/coding tools, source retrieval services and isolated execution environments. Six stage teams are internal actors with disjoint identity/context, not interchangeable workers.

Core journeys: start/import a project; review and accept scope/requirements/design; request/edit a model; contribute code or take over a task; inspect/run tests; pause/resume after a limit; switch an unfinished task's compatible backend deliberately; and apply a maintenance change with impact analysis.

## Functional requirements

| ID | Requirement | Acceptance condition |
|---|---|---|
| FR-001 | MUST instantiate six distinct team namespaces: planning, requirements, design, implementation, testing and maintenance. | Every agent/task/session has one owning team; a worker is never reassigned across teams. |
| FR-002 | MUST support a leader and bounded specialist workers inside each team. | Persisted acyclic task plan declares role, dependencies, allowed capabilities and outputs; default worker concurrency is two. |
| FR-003 | MUST permit cross-team content access only through published HandoffBundle deliveries. | Receiver can read the exact accepted grant set; unpublished drafts, conversations and unrelated artifacts are inaccessible. |
| FR-004 | MUST retain original project inputs and normalize relevant content from Markdown/text, PDF and DOCX. | Source hash/locations and extraction gaps retained; unavailable OCR yields needs_input rather than empty success. |
| FR-005 | MUST support research and evidence-informed expert/customer role tasks. | Retrieved claims, role simulation and assumptions are distinguishable; no real survey/interview falsely claimed. |
| FR-006 | MUST produce a practical plan covering scope, feasibility, work, assumptions and risks. | Planning handoff contains the agreed minimum records without requiring every lecture technique. |
| FR-007 | MUST produce functional/NFR/rule records with verifiable acceptance conditions. | Stable IDs, evidence links and resolved/blocking ambiguity dispositions exist. |
| FR-008 | MUST produce implementable design decisions, responsibilities, interfaces/data and important behavior. | Accepted requirements map to realizing design elements or explicit unresolved findings. |
| FR-009 | MUST recommend useful diagram types from a specification and support requested additions/revisions. | Selected diagrams have purpose/scope, editable source, rendering/validation and trace references; manual edits preserved. |
| FR-010 | MUST use a CodingBackend for bounded repository work, preferring Codex by default. | Jobs operate on identified isolated snapshots, return events/diffs, and integrate through explicit checks. |
| FR-011 | MUST support Codex, Claude Code and direct/local Brain connection profiles through separate interfaces. | Switching a compatible adapter does not require modifying all stage modules; unsupported features are explicit. |
| FR-012 | MUST allow participant review, manual editing/takeover and submission. | Human contributions enter the same version/validation path, with author provenance and no silent overwrite. |
| FR-013 | MUST plan tests, generate executable testbenches and run them through an ExecutionBackend. | Harness, fixtures, doubles/drivers, setup/teardown, oracles and report collection are inspectable. |
| FR-014 | MUST link observed test results to case IDs and exact code/suite/environment snapshots. | Missing/skipped required cases and simulated runs cannot satisfy release gates. |
| FR-015 | MUST triage maintenance, analyze impact and route rework to owning teams. | Change packages reopen the earliest affected owner and stale dependent outputs; previous releases remain historical. |
| FR-016 | MUST persist task plans, attempts, outputs, workspace/session refs and decisions. | Restart reconstructs completed and unfinished work without relying on chat memory. |
| FR-017 | MUST detect usage/auth/network/output/tool failures and expose appropriate pause/input/error states. | Known quota exhaustion pauses affected account work without repeated calls; reset times are never invented. |
| FR-018 | MUST resume unfinished work after reconciliation. | Completed unchanged results retained, uncertain coding edits inspected, no duplicate writer launched. |
| FR-019 | MUST preserve immutable artifact revisions, hashes and dependency links. | Accepted upstream changes create new revisions and invalidate relevant downstream outputs. |
| FR-020 | MUST separate approval, task completion and execution evidence. | Model text cannot approve a baseline or fabricate measured results. |
| FR-021 | MUST export a selected baseline with manifests, specifications and supporting artifacts. | Export contains the exact selected versions and excludes credentials/private drafts outside scope. |
| FR-022 | MUST provide fixture-mode development and failure replay. | Contract/recovery development needs no paid account; fixture observations are labeled. |
| FR-023 | SHOULD expose the same services through an optional MCP adapter. | Tools cannot bypass delivery grants, gates, errors or checkpoint policies. |
| FR-024 | MUST document effective authentication and usage source for each backend. | Native mode is not silently overridden by inherited API variables; no automatic paid-provider fallback. |

## Nonfunctional requirements and engineering invariants

| ID | Requirement | Verification target |
|---|---|---|
| NFR-001 | Team identity, session context and ungranted payloads MUST remain isolated at the agent context/tool boundary. | Canary artifacts/messages and filesystem attempts across teams denied; same model/account reuse does not merge history. |
| NFR-002 | A reported completed operation MUST have durably recorded output/state. | Crash tests before/after publication and DB commit recover consistently. |
| NFR-003 | Logical-result publication and delivery acceptance MUST be idempotent. | Duplicate requests/events do not create duplicate accepted results or deliveries. External billing exactly-once is not promised. |
| NFR-004 | Concurrent workers MUST not overwrite each other or participant work. | Per-workspace writer lease and expected-base checks; conflicted integration pauses. |
| NFR-005 | All automatic loops MUST be bounded by effective supported limits. | Exhausted attempts/time/budget stop dispatch; unavailable hard native-tool limits are disclosed. |
| NFR-006 | Secrets MUST not appear in artifacts, ordinary logs, exports or generated-code environments. | Canary secret checks and child-environment inspection. |
| NFR-007 | Generated-code execution MUST use the selected constrained environment. | Missing isolation/runtime produces blocked capability; shell=False alone is not sufficient. |
| NFR-008 | Core state/contracts MUST be provider-independent and portable. | Native sessions may expire; fresh sessions resume from durable records. |
| NFR-009 | Code SHOULD be readable, typed at public boundaries and modular. | A new backend/worker/importer uses documented interfaces, not six copied implementations. |
| NFR-010 | Evidence MUST distinguish actual, imported, assumed, model-generated and fixture/simulated content. | Provenance survives rendering, delivery and export. |
| NFR-011 | Status and recovery MUST be understandable without provider-specific expertise. | Pause message names stage/task, saved progress, reason and exact continuation action. |
| NFR-012 | UTF-8 documents and Korean/English content MUST survive import/render/export. | Round-trip fixture preserving text and source references. |

Performance targets are bounded-work requirements rather than fabricated timing claims. The implementation team should measure status/query/import latency on the supported environment and document any target adopted. Provider latency is not under local runtime control. A quota reset time is externally supplied information.

## Cross-team communication rules

Only `participant` ingress and the six team IDs exist as content principals. Kernel/backend services operate on behalf of a scoped principal. A worker cannot select another principal.

Forward flow is Planning → Requirements → Design → Implementation → Testing → Maintenance. The arrow means one immutable artifact delivery. Feedback uses a new defect, clarification or change-request bundle to a named owner. It is not direct conversation or shared memory. Maintenance may reopen any earlier team according to impact.

Every forward bundle declares all artifacts required by the receiver, including earlier specifications explicitly forwarded by the sender. Provenance references alone do not grant content access. The runtime may hold metadata globally but must not inject global conversations or arbitrary artifact contents into team prompts.

## Business and policy rules

BR-001: Default mode is collaborative, with review at scope/requirements/release decisions; safe sub-operations do not need repeated confirmation.

BR-002: Continuous mode records policy decisions as policy decisions, never participant sign-off. Project policy selects which gates need review.

BR-003: A missing hard prerequisite blocks only dependent work; nonblocking uncertainty can proceed as an explicit assumption.

BR-004: Native sessions are scoped to project/cycle/team/task/profile. Reusing account credentials is allowed; reusing another team's conversation/session or global project memory is not.

BR-005: An agent can propose work/content, but only the runtime validates, publishes, delivers, accepts and changes execution state.

BR-006: A failed actual test is a valid observation and routes a defect. It is not a failed provider request to retry until a nicer answer appears.

BR-007: Release evidence must match the exact candidate. Changing a requirement, code snapshot, suite or relevant environment invalidates affected acceptance evidence.

BR-008: Local-model text compatibility does not imply coding, search, tool use or isolation compatibility. Capabilities are separately probed and declared.

## Minimum complete project and exception handling

A project may have short documents and few diagrams. It cannot omit the six responsibilities merely because a model can write code immediately. Required content is determined by project risk/acceptance policy, not a fixed page count. Exceptions record approver type, reason, affected IDs and consequences. An exception cannot relabel a simulated result as measured or bypass an explicitly required isolation boundary.

For a first release, success includes one real application, observed tests, one artifact-routed defect repair, one maintenance revision and one recovered quota interruption. The package's fixture data alone cannot satisfy that application-level release claim.

## Requirements traceability to design

| Requirement group | Design location | Verification location |
|---|---|---|
| FR-001–003, NFR-001 | `03_ARCHITECTURE_AND_DESIGN.md`, delivery contracts | Team identity/grant/session negative scenarios |
| FR-004–009, NFR-010/012 | Team specs, import/diagram service design | Import, research provenance and diagram scenarios |
| FR-010–012/024, NFR-004/006/008 | Backend connections and repository integration | Auth, participant edits, capability/session scenarios |
| FR-013–015/019/020, NFR-007 | Team specs, execution/recovery and contracts | Actual test, rework/stale and maintenance scenarios |
| FR-016–018, NFR-002/003/005/011 | Execution/recovery state machine | Quota, crash-window, duplicate replay and status scenarios |
| FR-021–023, NFR-009 | Service interfaces, implementation plan | Export, fixtures and optional MCP contract scenarios |

The detailed acceptance scenario document supplies test steps. Implementers should retain these IDs in code issues, scenario tests and release evidence.
