# Verification and acceptance plan

**These tests are specified, not implemented or executed.** System verification is separate from tests the Testing team creates for generated applications. Passing application tests cannot prove system isolation, recovery, or evidence integrity.

**P0** denotes release-blocking correctness; **P1** denotes required release functionality. Mappings use [02_REQUIREMENTS.md](02_REQUIREMENTS.md).

## 1. Test strategy and fixtures

Run three suites separately:

1. **Deterministic suite:** unit, contract, persistence, CLI, and sandbox tests using scripted providers/fake coding processes; no paid accounts/network.
2. **Opt-in live smoke:** selected backend/credentials, stated budget, disposable project. Record versions/capabilities. Skips are not passes or compatibility evidence.
3. **Human walkthrough:** instructor, student, and engineer inspect comprehension, handoffs, and intervention.

Versioned fixtures include: `campus-booking/` brief, sourced excerpts, scanned/text PDFs, bilingual DOCX/tables, six team graphs, bundles/deliveries, diagrams, responses, and a defective application. Label synthetic research. Include account failures, malformed JSON, slow/unknown outcomes, wrong/unavailable identity, and malicious attachments. Exclude real credentials/personal information.

Fixture manifests record paths, lengths, SHA-256, purpose, and applicable license/source. Compute hashes from distributed bytes. Result manifests record source/configuration/fixture digests, adapter/runtime versions, timestamps, test IDs, outcomes, and evidence paths. Redact secrets before persistence. Assert against records/tool outputs, not model claims.

## 2. Executable acceptance cases

### AT-001 — Six isolated teams complete one lifecycle

**Mapping:** FR-001, FR-002, FR-006, FR-007, FR-008, FR-020; NFR-001. **Priority:** P0.

- **Fixture/setup:** Campus-booking brief; deterministic model scripts; all six team IDs registered: `planning`, `requirements`, `design`, `implementation`, `testing`, `maintenance`.
- **Action:** Execute through an initial release and initialize its maintenance baseline.
- **Expected:** Each team owns its leader/workers and sessions; task graphs are acyclic and concurrency bounded. Each successor consumes an explicitly delivered immutable `HandoffBundle`. Completion requires that team's gates; maintenance records the operational baseline rather than inventing production observations.
- **Asserted evidence:** Team/session ownership records, six stage histories, bundle hashes, delivery grants, gate decisions, and traceability from brief to tests.

### AT-002 — Team memory and filesystem isolation

**Mapping:** FR-001, FR-003; NFR-001, NFR-006. **Priority:** P0.

- **Fixture/setup:** Put unique canary strings in each team's private messages, scratch files, caches, and coding history. Reuse one backend/account across teams.
- **Action:** Request canaries from another team via tools, context, paths, traversal, symlinks, environment references, and resume identifiers.
- **Expected:** Deny unauthorized reads before model/tool execution; no canary reaches the recipient. Reused account credentials do not confer shared conversational memory. Legitimate delivered artifacts remain readable.
- **Asserted evidence:** Access decisions, sanitized invocation/context manifests, filesystem audit records, and negative canary assertions.

### AT-003 — Delivery exposes only its declared closure

**Mapping:** FR-003, FR-019; NFR-003. **Priority:** P0.

- **Fixture/setup:** A delivery references a bundle and exact permitted transitive artifact closure; add unrelated artifacts and newer revisions to storage.
- **Action:** Read granted objects, request guessed IDs, follow undeclared references, and replace a source object at its path.
- **Expected:** Only granted bytes and versions resolve. No global artifact lookup or automatic latest-version substitution. A digest mismatch blocks consumption.
- **Asserted evidence:** Granted manifest, denied access events, expected/observed digests, and unchanged recipient input snapshot.

### AT-004 — Runtime refuses unenforceable isolation

**Mapping:** FR-010, FR-013; NFR-007. **Priority:** P0.

- **Fixture/setup:** Configure a coding backend that cannot enforce the required workspace/process boundary.
- **Action:** Start an autonomous coding task, including an attempted bypass through a convenience tool.
- **Expected:** Persist `needs_input` with `capability_missing`, actionable instructions, and zero backend/process launches. A prompt claiming isolation is insufficient. Strict isolation requires explicit selection of a supported isolated profile before execution.
- **Asserted evidence:** Capability assessment, zero child-process/model invocations, state transition, and student-visible explanation.

### AT-005 — Research substitutes preserve epistemic labels

**Mapping:** FR-005, FR-007; NFR-010. **Priority:** P1.

- **Fixture/setup:** Published excerpts disagree; simulated expert/customer roles propose conflicting requirements.
- **Action:** Run planning and requirements research, then inspect prioritization and acceptance criteria.
- **Expected:** Separate sourced observations, synthetic responses, assumptions, and decisions. No fabricated interviews, respondent counts, statistical confidence, or stakeholder approval. Conflicts become explicit decisions or unresolved questions.
- **Asserted evidence:** Source locators and retrieval dates, quotation/excerpt provenance, simulation labels, decision rationale, and linked requirements.

### AT-006 — Flexible documents become explicit records

**Mapping:** FR-004; NFR-010, NFR-012. **Priority:** P1.

- **Fixture/setup:** Equivalent Markdown, DOCX with headings/tables/Korean text, text PDF, scanned PDF, encrypted PDF, and ambiguous specifications.
- **Action:** Import each and inspect extracted requirements.
- **Expected:** Preserve originals/hashes; record extraction method and source locations. DOCX headings, table values, Korean/English text, and requirement boundaries survive normalization. Missing OCR or inaccessible content produces `needs_input`, allowing supplied text. Never treat failed extraction as empty requirements.
- **Asserted evidence:** Original/extracted digests, PDF pages or DOCX paragraph/table locators, expected normalized fields, extraction gaps, and review decisions.

### AT-007 — Canonical gate rejects invalid handoffs

**Mapping:** FR-003, FR-019, FR-020; NFR-005, NFR-008. **Priority:** P0.

- **Fixture/setup:** Missing fields, duplicate IDs, unsupported schema versions, broken references, circular dependencies, invalid hashes, and malformed model output.
- **Action:** Submit handoffs and exceed the configured repair limit.
- **Expected:** Reject without publication. Repairs are bounded and logged; semantic ambiguity requires input. Canonical encoding produces stable digests for equivalent accepted objects while retaining raw source bytes separately.
- **Asserted evidence:** Machine-readable validation errors, repair count, canonical bytes/digests, and zero successor deliveries.

### AT-008 — Imported text cannot issue runtime commands

**Mapping:** FR-003; NFR-001, NFR-006. **Priority:** P0.

- **Fixture/setup:** PDF/web excerpts instruct agents to expose credentials, change permissions, bypass gates, or contact another team's tools.
- **Action:** Ingest and use excerpts during research/design.
- **Expected:** Treat directives as source content. Runtime authorization and team boundaries remain unchanged. Unauthorized tool requests are denied independently of model compliance.
- **Asserted evidence:** Provenance-tagged content, denied tool calls, unchanged grants/configuration, and secret-canary non-disclosure.

### AT-009 — Student adds and revises diagrams

**Mapping:** FR-009, FR-012; NFR-010, NFR-012. **Priority:** P1.

- **Fixture/setup:** Requirements specify actors, workflows, and constraints; design already contains a component diagram.
- **Action:** Request recommendations, add a sequence diagram, edit its source manually, and request an unsupported notation.
- **Expected:** Explain diagram choices against the specification; preserve editable source, rendered output, revision lineage, and element links. Accept manual additions for validation. Unsupported rendering yields an explicit limitation, not false success or discarded work.
- **Asserted evidence:** Recommendations, source/render hashes, render exit result, consistency findings, and participant edits.

### AT-010 — Manual takeover preserves accountability

**Mapping:** FR-012, FR-019, FR-020; NFR-004. **Priority:** P0.

- **Fixture/setup:** Pause design before handoff; engineer changes a contract and participant resolves an open requirement.
- **Action:** Import edits, review the diff, revalidate, and resume implementation.
- **Expected:** Preserve agent drafts and authored edits with identity/time/version. Do not overwrite manual work on resume. Changed baselines require revalidation; downstream artifacts cannot silently retain stale claims of compliance.
- **Asserted evidence:** Change request, diff, ownership metadata, approval/gate record, and successor's exact revised input digest.

### AT-011 — Quota exhaustion survives restart

**Mapping:** FR-016, FR-017, FR-018; NFR-002, NFR-011. **Priority:** P0.

- **Fixture/setup:** Script a hard quota failure after two completed tasks and partial output on a third.
- **Action:** Run, close the process, restart, then resume after availability returns.
- **Expected:** Persist `paused` with normalized reason and progress. Show what remains and how to resume. Completed tasks are not rerun; partial work remains distinguishable from accepted artifacts. No silent backend/account switch or automatic claim that availability has returned.
- **Asserted evidence:** Before/after checkpoints, invocation counts, retained artifact hashes, pause message, and resumed task lineage.

### AT-012 — Transient retries respect limits

**Mapping:** FR-017; NFR-005, NFR-011. **Priority:** P0.

- **Fixture/setup:** Retryable rate limit with retry-after, transport failure, bad credentials, and permanent invalid request.
- **Action:** Run under a virtual clock and restart during `retry_wait`.
- **Expected:** Retry only classified transient failures within configured attempt/time budgets. Persist next attempt; respect cancellation. Authentication and permanent errors stop with actionable states rather than infinite retries or backend fallback.
- **Asserted evidence:** Normalized error codes, virtual-clock schedule, retry counters, state sequence, and provider invocation log.

### AT-013 — Crash recovery avoids duplicate side effects

**Mapping:** FR-016, FR-018, FR-019; NFR-002, NFR-003, NFR-004. **Priority:** P0.

- **Fixture/setup:** Inject crashes after tool launch, artifact write, bundle commit, and delivery publication.
- **Action:** Restart and reconcile each interruption.
- **Expected:** No half-published bundle or duplicate delivery. Use operation IDs and durable records. An unknown external-call outcome is explicitly reconciled before a potentially duplicative retry; never assert exactly-once provider execution without support.
- **Asserted evidence:** Journal transactions, temporary/committed artifacts, delivery counts, reconciliation decisions, and `interrupted` recovery history.

### AT-014 — CLI identity, sessions, and cost are explicit

**Mapping:** FR-010, FR-011, FR-017, FR-024; NFR-001, NFR-005, NFR-006, NFR-008. **Priority:** P0.

- **Fixture/setup:** Codex/Claude Code fixtures report identity matching/mismatching `expected_account_id`, unavailable identity, expired login, missing capabilities, and unknown usage/cost.
- **Action:** Attempt coding and session continuation.
- **Expected:** Expected-account mismatch yields `needs_input` and zero invocations. Unavailable identity is `identity_unverified`; proceeding requires a declared authentication policy or explicit participant-selected connection accepting uncertainty. Never report verified identity without evidence. Supported login instructions do not collect passwords/scrape tokens. Resume only owned sessions; unknown cost stays unknown. No silent auth/model/account/backend substitution.
- **Asserted evidence:** Redacted preflight, identity comparison/status, policy or participant selection, session ownership, cost status, and invocation counts.

### AT-015 — API and local-model adapters share contracts

**Mapping:** FR-011, FR-022; NFR-008, NFR-009. **Priority:** P1.

- **Fixture/setup:** Scripted OpenAI, Anthropic, and local-compatible endpoints with different streaming, tools, and structured-output support.
- **Action:** Execute equivalent tasks, cancel streams, and request unsupported capabilities.
- **Expected:** Normalize results/errors without erasing provenance. Preflight rejects unsupported requirements or selects a documented validated mode. Endpoint/model selection remains explicit; local execution is not assumed to provide web search or a sandbox.
- **Asserted evidence:** Contract-test matrix, capability records, redacted requests, terminal events, and explicit limitations.

### AT-016 — Testing team builds executable testbenches

**Mapping:** FR-013, FR-014, FR-020; NFR-007, NFR-010. **Priority:** P1.

- **Fixture/setup:** Tiny application, design contracts, acceptance criteria, intentional defect, fixtures, and dependency stubs.
- **Action:** Produce test plan/testbench; run passing, failing, timed-out, skipped, and unexecuted cases.
- **Expected:** Link tests to requirements and record actual outcomes separately from predicted outcomes. Include environment, command, source revision, stdout/stderr, exit code, and timing. Tests expose the known defect; unexecuted tests never count as passed.
- **Asserted evidence:** Test sources/hashes, coverage links, raw runner records, defect report, and summary consistent with raw counts.

### AT-017 — Maintenance routes change through artifacts

**Mapping:** FR-015, FR-019; NFR-010. **Priority:** P0.

- **Fixture/setup:** Accepted release plus a defect and a new accessibility requirement.
- **Action:** Maintenance analyzes impact and routes documented requests to responsible teams; execute the revised requirements/design/implementation/testing path.
- **Expected:** No direct private conversation or covert patch by another team's worker. Retain old release and bundle versions. Trace revised requirements, design, patch, regression results, and new maintenance baseline.
- **Asserted evidence:** Change-request routing, affected-artifact graph, versioned deliveries, source diff, regression output, and closure decision.

### AT-018 — State machine and cancellation are consistent

**Mapping:** FR-016, FR-018; NFR-004, NFR-005. **Priority:** P0.

- **Fixture/setup:** Tasks cover `pending`, `running`, `retry_wait`, `paused`, `needs_input`, `interrupted`, `completed`, `failed`, and `cancelled`.
- **Action:** Exercise legal and illegal transitions, simultaneous resume attempts, cancellation, and late provider results.
- **Expected:** Enforce documented transitions and one active owner. Cancellation terminates managed work where supported, preserves evidence, and prevents late results from publishing handoffs. Resume requires a persisted, valid checkpoint.
- **Asserted evidence:** Transition log, rejected mutations, lease/ownership records, process termination evidence, and unchanged published baseline.

### AT-019 — Baseline export preserves integrity and privacy

**Mapping:** FR-021; NFR-006, NFR-010, NFR-012. **Priority:** P0.

- **Fixture/setup:** Two baseline revisions, linked diagrams/test evidence, Korean/English documents, private drafts, and secret canaries outside the selected export scope.
- **Action:** Export the older selected baseline, unpack into a clean directory, verify manifest/digests, and reopen supported documents. Corrupt one archived artifact and verify again.
- **Expected:** Exact selected revisions round-trip byte-for-byte with evidence labels and source references intact. Export excludes unrelated drafts, credentials, and native sessions; omission of a required dependency is an explicit error. Verification rejects tampering. Export/reopening does not grant broader team access or claim restoration of provider sessions.
- **Asserted evidence:** Selection/export manifests, file inventory and hashes, Unicode round-trip assertions, secret-canary scan, preserved provenance, and tamper failure.

## 3. Release decision and reviewer walkthrough

All P0/P1 cases must pass for the complete release. Previews disclose unimplemented features. Report failed/blocked/skipped/unexecuted cases separately with defects/owners. Deterministic tests do not substitute for actual-executor isolation tests.

For each advertised adapter, run a separately authorized **capability-specific smoke**: Brain produces a typed proposal; Retrieval returns sourced material; CodingBackend edits a disposable snapshot; ProcessRunner runs a test command. Only test capabilities the adapter declares. Apply relevant identity/capability preflight, cancellation/recovery, and budget checks; acknowledge unknown native cost. Capture redacted commands, versions, outcomes, and hashes. Paid live calls never belong in default contributor tests.

Separately run a **selected-profile lifecycle smoke** combining compatible adapters through all six teams, an observed application test, artifact-routed defect repair, maintenance revision, and injected quota/restart recovery. A Brain-only smoke cannot substantiate this end-to-end claim. If optional MCP is shipped, replay applicable service cases through MCP and confirm identical authorization/gates/checkpoints (FR-023). Engineer review also checks typed modular interfaces and documents adding one adapter without editing six teams (NFR-009).

The student identifies the active team, inspects its specification, adds a diagram, amends a requirement, observes a defect, and resumes after simulated quota exhaustion. The engineer reviews exact handoffs and runs documented commands without unpublished context. The instructor checks synthetic-evidence labels and understandable responsibilities. Record acceptance, changes, and limitations; this design package is not a tested implementation.
