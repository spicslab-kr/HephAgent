# 01. Project plan

## Purpose and project boundary

Build **SDLC Agent Teams**, an open-source local system that helps participants carry a software project through planning, requirements, design, implementation, testing and maintenance. Each stage is a distinct agent team. Teams exchange explicit specifications and supporting artifacts. Participants can review, edit, contribute code, assume a worker task or change a backend.

This package is the planning, requirements and design handoff for that system. The generated applications are separate software projects with separate repositories, tests and release versions. A document generator alone does not satisfy the objective.

Working repository name: `sdlc-agent-teams`. Python import package/CLI: `sdlc`. Package baseline: `1.0.0-design`, dated 9 October 2026, Asia/Seoul. This is a proposed implementation baseline, not evidence of a built application or supervisor sign-off.

## Stakeholders and success

| Stakeholder | Need | Observable success |
|---|---|---|
| Project participant/student | Experience actual engineering decisions and contribute useful work | Can import a brief, inspect handoffs, change a diagram, implement a task, run tests and maintain a release |
| Human engineer | Implement without reconstructing chat history | Contracts, modules, state machines, error behavior and acceptance scenarios are explicit |
| Codex/Claude Code implementer | Continue work after another implementer stops | Versioned design package plus durable implementation status, no hidden chat dependency |
| Supervisor/maintainer | Assess architecture and progress | Traceable requirements, decisions, evidence and open issues |
| Repository contributor | Add a backend/worker/importer/test technique | Small documented interface and fixture-driven contract checks |

Success is a real small application release plus one maintenance revision, with coherent documents and observed tests. The runtime must survive a provider usage-limit pause and a process restart without losing completed work or mixing stage-team context.

## Scope

The first complete release includes all six teams; flexible Markdown/PDF/DOCX input; source-backed research and simulated stakeholder roles; selected editable diagrams; Codex-first repository work with Claude Code and model API/local alternatives; a constrained build/test runner; artifact-only cross-team handoff; participant review/takeover; durable state, pause and resume; and an end-to-end reservation-service example.

Methods follow project needs. A WBS, interview simulation, state diagram, CFG or formal estimation method is selected when useful or requested. Course materials inform available techniques, not mandatory features or stage exit checklists.

Out of initial scope: multi-tenant hosting, billing administration, a custom social/collaboration platform, automatic real-customer recruitment, production publishing without explicit project policy, arbitrary languages/runtimes, formal proof of correctness, unlimited recursive agents and replacing native provider authentication with a universal login.

## Delivery model

Use Python 3.11+, CLI first, SQLite metadata and inspectable files. Use six fixed team namespaces with scoped leaders/workers. Keep the orchestration kernel deterministic. Initial application execution profile: a small Python API service with SQLite and pytest, built/tested in a pinned isolated image. Add other profiles through the same contracts.

Initial developer support: Windows 11 with WSL2/Docker Desktop and Linux; macOS can follow the same CLI/container profile once verified. The project configuration must identify the selected supported environment. Do not claim support for a platform based solely on provider CLI availability. If the chosen isolation profile is unavailable, document generation can continue but repository jobs requiring that profile pause.

One coordinator owns a project; independent within-team workers may run concurrently. Human collaboration occurs through Git contributions and imported/accepted artifacts, rather than requiring a hosted server. Default concurrency is two workers per project, configurable by account/hardware limits. Default delegation depth is leader to workers only.

## Feasibility decisions

| Concern | Decision | Remaining verification |
|---|---|---|
| Repository-aware code work | Wrap existing Codex/Claude Code capabilities instead of rebuilding a coding agent | Test supported native events, isolation, cancellation and auth mode |
| LLM variation | Typed internal contracts and semantic checks, with bounded repair | Representative model quality and schema capability probes |
| Local models | Explicit Brain adapter; coding/search are separate capabilities | Installed model/endpoint compatibility and capacity |
| Missing real customers/experts | Retrieved evidence plus clearly labeled role simulation | Human product decisions and uncertainty remain visible |
| Resource/usage limits | Task checkpoints and normalized pause/resume | Crash-window and partial-edit tests |
| Team context separation | Handoff delivery grants and isolated working context | Negative tests for arbitrary artifact/session/file access |
| Complexity | Small Python modules, finite queues and local files | Avoid optional UI/framework work delaying the full cycle |

No purchased services or measured budget figures are assumed. Provider subscriptions, API billing and local compute costs depend on the chosen profiles. Track known usage, unknown charges and participant-defined limits; never infer zero cost from missing metadata.

## Milestones and gates

| Milestone | Deliverable | Demonstration |
|---|---|---|
| M0 | Package skeleton, domain contracts, fixture engine, six team principals | Schema/examples load; invalid references rejected |
| M1 | Artifact store, publication/delivery grants, team context isolation | Requirements team cannot read unpublished Design drafts or sessions |
| M2 | Durable task graph, attempts, leases, quota pause/resume | Completed sibling task survives quota failure; interrupted writer reconciles |
| M3 | Importers and Planning/Requirements operations | PDF/Markdown input becomes traceable requirements; missing OCR produces actionable pause |
| M4 | Codex and alternative backend adapters, scoped workspaces | Actual repository inspection/edit with correct auth source and events |
| M5 | Design, diagrams and implementation integration | Selected models and bounded code changes form a runnable candidate |
| M6 | Testbench creation, isolated runner and measured reports | Seeded defect produces observed failure, followed by a verified repair |
| M7 | Maintenance, participant takeover, release and full cycle | One complete project plus regression-verified change |
| M8 | DIY connection polish, contributor examples and optional MCP/viewer | Backend/worker replacement without altering the six-team contracts |

M7 is the first complete system. Recovery and separation belong before rich generation features. Dates and person-week estimates should be set by the implementation team after M0/M1 expose the environment and integration work; this design does not invent an empirical schedule.

## Risk register

| Risk | Trigger | Response/owner |
|---|---|---|
| Attractive but inconsistent artifacts | Conflicting requirement/interface/diagram IDs | Domain validation and stage review; owning leader |
| Hidden cross-team context | Shared native session, workspace or global memory | Stop job, revoke context, rerun isolated; runtime/backend owner |
| Provider protocol changes | Unknown terminal event, parameter or error | Preserve diagnostics, pause capability-dependent work; adapter owner |
| Partial code after interruption | Lost terminal result or coordinator | Reconcile process/worktree before replay; recovery owner |
| Simulated market/verification presented as real | Missing provenance or tool evidence | Reject affected claim/gate; requirements/test owner |
| Overbuilt infrastructure | Hosted UI or framework blocks M7 | Return to CLI and contracts; project lead |
| Unsupported isolation on a student's machine | Cannot enforce required read/write boundary | Offer supported profile; pause sensitive operation, not silent downgrade |
| Student work overwritten | Changed integration base or manual diagram edit | CAS revision checks and explicit reconciliation; integration service |

## Participation and governance

Default policy `collaborative` reviews scope, requirements and release decisions; individual safe operations run without repeated approval. `continuous` progresses under preauthorized scope, still pausing for real blockers. Decisions record actor type (`participant` or `policy`) and exact hashes. Models cannot forge participant approval.

Human engineers and coding assistants share an issue/task list and commit history. At each implementation pause, update the repository's implementation status with completed work, branch/commit, verification actually run, blocked items and next actions. Do not treat this design package as already implemented status.

Choose the repository license before public release. Example assets should be authored for distribution; do not bundle private course slides, credentials or student projects by default.

## Completion criteria

The system fulfills the requirement baseline in `02_REQUIREMENTS.md`, passes the applicable scenarios in `08_VERIFICATION_AND_ACCEPTANCE.md`, demonstrates the M7 cycle, and exposes unsupported/live-untested capability honestly. A successful document-generation call, fixture playback or zero-exit coding turn is not the completion criterion.
