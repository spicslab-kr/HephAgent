# Implementation plan and repository guide

## 1. Build order and definition of done

Build a small complete lifecycle before expanding methods, diagrams or UI. Each milestone ends with working code, readable documentation and evidence relevant to that milestone. Schema fixtures do not substitute for a live backend smoke test; a live model response does not substitute for isolation or recovery tests.

| Milestone | Implement | Exit evidence | Dependencies |
|---|---|---|---|
| M0: contracts and skeleton | Package/CLI, domain models, schema validation, configuration loader, fixture backend, license decision | Fixtures accepted/rejected as specified; CLI help; importable modules; documented setup | This design reviewed |
| M1: ownership and delivery | Immutable store, SQL migrations, ingress, exact grants, route/decision services, restricted team views | Six identities; canary reads denied; wrong hash/route rejected; duplicate receipt idempotent | M0 |
| M2: durable runtime | Task DAG, attempts, leases/fences, outbox, retry budgets, checkpoints, status/resume | Crash-window, stale writer and quota recovery tests; no duplicated accepted task | M1 |
| M3: first two teams | Importers, research port, Planning and Requirements operations, provisional/accepted gates, basic diagrams | PDF/Markdown brief to traceable plan/SRS; synthetic provenance; participant revision | M2 |
| M4: real backends | Codex-first native adapter, OpenAI/Anthropic Brain adapters, isolated launcher, Claude Code alternative | Version-pinned live smokes; explicit auth source; streams/errors; no cross-team files or credentials in target runner | M1/M2; can develop alongside M3 |
| M5: design and implementation | Design team, coding task scopes, integration coordinator, manual takeover, snapshots | Accepted design to runnable candidate with attributable checks; partial coding recovery | M3/M4 |
| M6: testing | Independent Testing team, benches, runner environment prep, evidence parser, defect packages | Deliberately planted defect found/repaired; tests rerun on exact corrected snapshot | M5 |
| M7: maintenance and full cycle | Local release manifest, runbook, change classification, impact/stale propagation, maintenance loop | All six teams; change returns to earliest owner; prior evidence preserved; export round trip | M6 |
| M8: extensions | Local endpoint adapter, optional MCP facade, extra diagram/import formats, richer UI if wanted | Each extension uses same service gates; capability tests; no core dependence on optional tools | M7 |

Local API compatibility must be designed from M0 and exercised with a fixture; M8 adds a documented live local model profile. If local-first use is essential to a class, move that profile's live acceptance into M4. Do not delay the interface separation.

## 2. Proposed source tree

The tree below is for the **repository to build**, not a claim that these modules are supplied as implemented code.

```text
sdlc-agent-teams/
  pyproject.toml
  README.md
  AGENTS.md
  LICENSE
  .env.example
  config/project.example.yaml
  contracts/sdlc.schema.json
  src/sdlc/
    cli/{main,commands,render}.py
    application/{projects,imports,workflow,reviews,handoffs,changes,exports}.py
    domain/{models,states,policies,errors,identities}.py
    runtime/{scheduler,graphs,attempts,leases,recovery,budgets,supervisor}.py
    ports/{brain,coding,research,execution}.py
    adapters/
      brains/{openai,anthropic,openai_compatible,fixture}.py
      coding/{codex_cli,claude_cli,fixture}.py
      research/{provider_search,manual,fixture}.py
      execution/{container_runner,isolated_launcher}.py
    teams/
      planning/{operations,prompts,checks}.py
      requirements/{operations,prompts,checks}.py
      design/{operations,prompts,checks}.py
      implementation/{operations,prompts,checks}.py
      testing/{operations,prompts,checks}.py
      maintenance/{operations,prompts,checks}.py
    artifacts/{store,grants,manifests,imports,normalization,diagrams,snapshots}.py
    storage/{database,repositories,outbox,migrations}.py
    verification/{schemas,traceability,evidence,gates}.py
    mcp/server.py
  prompts/{planning,requirements,design,implementation,testing,maintenance}/
  templates/
  tests/{unit,contract,integration,acceptance,live}/
  docs/{architecture,connections,recovery,contributing}/
```

The optional `mcp` module is not imported by the CLI core. Keep prompt bodies as editable Markdown/YAML assets with versions, and keep Python logic in modules. Do not make a framework-specific agent graph the only place business rules exist. Register each operation with explicit schemas and capabilities so students can add a worker without editing the scheduler.

## 3. Suggested initial dependencies

Use standard-library SQLite, pathlib, asyncio, subprocess argument arrays and hashing. Select one readable CLI library and one typed validation approach. Pydantic plus a Draft 2020-12 JSON Schema validator are reasonable candidates; ensure schema parity rather than relying on approximate type checks. Provider SDKs are optional adapter extras. PDF/DOCX extraction, renderers, local model clients and MCP support are separate extras with license/version records. Use a lockfile and tested version ranges selected during implementation; this design intentionally does not invent future-compatible pins.

Offer a fixture-only developer setup requiring no account, plus isolated live execution instructions. The fixture mode must be visibly marked in status, artifacts and reports. Do not make dummy passing application tests the default demonstration of a release.

## 4. Work items suitable for Codex, Claude Code or a human

Every work item should carry input document sections, allowed modules, expected behavior, evidence and boundaries. The following sequence is a starting backlog; assign identifiers and refine estimates after M0 probes.

1. Implement enums, models and fixture validation from the schema; reject extra fields and unsupported versions. Add contract tests for invalid references and origin labels.
2. Implement a content store with safe relative paths and no-overwrite publication; test corruption, traversal and interrupted writes.
3. Implement SQL repositories and transactions; test migrations, foreign keys, CAS and event sequence behavior.
4. Implement ingress and delivery grants; demonstrate source documents can pass as PDF/Markdown and no worker can read undeclared artifacts.
5. Implement project/connection config with secret references, capability probes and one strict execution profile. Prove native-client read isolation before claiming native coding support.
6. Implement the scheduler, states, fenced attempts and recovery with a deterministic fixture backend and injected failures.
7. Implement CLI status/review/resume/manual takeover over application services; verify dry-run never launches an adapter.
8. Implement the first two team registries, source provenance, role simulation and diagram requests. Use a small library-reservation problem as the running example.
9. Implement Codex native coding and API Brain adapters, then Claude Code. Record actual tested versions and login behavior. Do not silently inherit conflicting API credential variables.
10. Implement Design outputs and Implementation workspaces/integration. Export one exact candidate snapshot plus instructions and developer evidence.
11. Implement Testing benches and real runner evidence; use seeded faults to prove a failing case becomes a routed defect and corrected retest.
12. Implement Maintenance release/change packages, stale propagation and export/import. Finish a full lifecycle with one quota pause and one maintenance revision.
13. Add the local compatible profile, MCP facade and additional diagram types only after the core acceptance obligations hold.

Parallel tasks may cover separate adapters, importers and team prompts after interfaces are stable. Do not parallelize changes to core contracts without a designated integrator and design decision. Coding workers use private workspaces and submit patches; one integrator serializes merges. The development team building this repository is distinct from the six simulated teams that the resulting software will run.

## 5. Handover between coding assistants

The source of truth is the repository, accepted design and task records, not this chat or one provider session. Include a short `WORK_LOG.md` in the implementation repository with current milestone, completed task IDs, exact branch/commit, commands actually run and their results, unresolved decisions and the next bounded task. Keep secrets and hidden reasoning out of it.

When switching from Codex to Claude Code or to a human, commit or export the reviewed work, record uncommitted changes explicitly, stop conflicting writers and hand over this guide plus the work log and tests. A new assistant must inspect the current tree and status before editing. If the design conflicts with implementation evidence, write a concise change proposal with affected requirements and tests; do not quietly weaken the six-team isolation or recovery guarantees.

## 6. Verification strategy and release gates

Use unit tests for state/grant/digest rules, contract fixtures for adapter normalization, integration tests for real SQLite/files/process boundaries, and acceptance scenarios for the complete user journey. Prefer failure-injection tests where the main risks are interruptions and cross-team leakage. Add tests for consequential behavior, not every trivial wrapper. Tests for the orchestration repository are separate from the target application's generated tests.

CI must run offline deterministic tests with no provider secrets, check formatting/types selected by the project, validate migrations/contracts, and build documentation/examples. Live backend smoke tests are opt-in and budgeted; record skipped/unavailable separately from passed. A supported backend claim requires its own live evidence on a declared platform/version. A successful fixture test never establishes a provider's current behavior.

The first usable release requires AT-001 through AT-019 dispositions, all mandatory failures resolved, a six-team walkthrough, one real small application's executed tests, one defect loop, one maintenance loop and durable quota recovery. Any platform/adapter not meeting its capability gate remains explicitly unsupported. Human review checks that the workflow is understandable and the evidence accurately describes what ran.

## 7. Documentation and contribution expectations

Document installation, official authentication, optional API billing, local model setup, backend capability matrix, isolation limitations, quotas, manual takeover, artifact formats and adding an operation. Provide one tiny end-to-end project and one intentionally failing recovery example. Students should be able to rewrite a prompt or worker and observe the corresponding artifacts without reverse engineering the scheduler.

Use concise docstrings for invariants and module responsibilities, useful type annotations and clear error messages. Avoid hidden global mutable state, provider-specific values in business logic and one giant prompt containing the entire lifecycle. Select an open-source license with the project owner before publication; do not embed third-party course slides, secrets or unrestricted copyrighted source extracts.

## 8. Design acceptance checklist

Before starting implementation, the supervisor can review these decisions asynchronously: the six team names, first supported isolation profile, default Codex/native versus API connection paths, collaborative gate policy, scope of the first real example, license and budget limits. They are review items, not a reason to halt fixture/core implementation. Unresolved cost/model choices remain explicit configuration placeholders. Implementation can begin at M0 with no paid account.

This package supplies requirements, design, contracts, fixtures and acceptance plans. It does not supply a completed application or evidence that the proposed CLI, native integrations or full lifecycle already work.
