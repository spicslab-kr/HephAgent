# Instructions for the implementing engineer or coding assistant

You are implementing `sdlc-agent-teams`, Python package `sdlc`, from this design handoff. Read README, requirements, architecture, contracts, recovery and the current milestone before editing. The supplied files are design inputs; create the proposed source repository deliberately. Do not represent the reference DDL/fixtures as a functioning application.

## First work item

Start at M0 in `docs/09_IMPLEMENTATION_PLAN.md`: create a small package/CLI, typed domain models, configuration validation and a deterministic fixture backend. Validate the supplied examples and add negative contract tests. Then implement grants/isolation and recovery before live autonomous coding. Keep a concise WORK_LOG with current commit, task, checks actually run and the next bounded action.

## Invariants to preserve

- Exactly six distinct teams: planning, requirements, design, implementation, testing, maintenance. Reusable classes do not mean reusable cross-team agent instances or sessions.
- Cross-team content is accessible only through exact accepted delivery grants. Provenance links, a shared parent folder and model memory do not grant access.
- The runtime owns state, permissions, retries, receipts and completion. Leaders/workers propose work and content.
- Treat participant documents flexibly: preserve originals and extraction evidence, normalize for internal contracts, and surface unreadable portions.
- Codex/Claude/native API/local connections are separate adapters with explicit capabilities, account mode and cost source. No silent provider fallback or secret copying.
- Native session resume is only for the same logical task under the same ownership/configuration binding. Durable artifacts/checkpoints must support a fresh session.
- Preserve partial coding work, reconcile uncertain side effects, and never overwrite a person's edits or run duplicate writers.
- Tests count as executed only with collector evidence on exact source/test/environment versions. Synthetic research, fixture records and unexecuted tests remain labeled.
- Mutable projections cannot rewrite immutable payloads/results or fabricate human approval.

## Implementation style

Keep domain models independent of SDKs. Use explicit service methods, small modules, registered operations, editable prompts and argument arrays for subprocesses. Prefer readable code that students can change. Do not introduce a large framework or hosted service without an actual need.

Use the JSON Schema and semantic contract together. Implement operation prerequisite/gate policies before enabling them. Treat capability placeholders as blocked, not permission to ignore a check. Add tests for meaningful state, grant, crash, quota and execution-evidence risks. A green fixture suite does not authorize claiming a live integration works.

When requirements and implementation evidence conflict, make a scoped change proposal with affected IDs and checks. Preserve the accepted baseline until disposition. Record unsupported platforms/backends and skipped live tests honestly. Continue independent work while an unrelated account/decision is unavailable.

## Handover and review

Give the next engineer the repository revision, work log, outstanding changes, design decisions and evidence. Do not depend on hidden chat context or copy private model reasoning. A human supervisor should be able to reproduce checks from written commands and inspect exact artifacts.
