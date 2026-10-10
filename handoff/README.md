# SDLC Agent Teams — implementation handoff

Version: **1.0.0-design**. Prepared: **9 October 2026 KST**.

This package is the planning, requirements and design handoff for an open-source system that runs six distinct SDLC teams using external LLMs and coding tools. It is intended for Codex, Claude Code and human engineers. It is not a completed application, an installed CLI or a live-provider test report.

**Start with [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)** for the complete narrative in one file. For incremental work, read the numbered documents, then the contracts and acceptance plan. [AGENTS.md](AGENTS.md) gives a coding assistant a concrete starting instruction. No previous chat is needed.

The six teams are Planning, Requirements, Design, Implementation, Testing and Maintenance. Each owns its agents, sessions, memory and workspaces. Cross-team information travels only in versioned specifications/artifact packages. Shared adapter code or a model account never means shared agents. Feedback and corrections also travel as packages.

## Package map

| Location | Purpose |
|---|---|
| `IMPLEMENTATION_GUIDE.md` | Consolidated planning-through-design guide |
| `docs/01_PROJECT_PLAN.md` | Scope, stakeholders, feasibility, risks and milestones |
| `docs/02_REQUIREMENTS.md` | Functional/nonfunctional requirements and business rules |
| `docs/03_ARCHITECTURE_AND_DESIGN.md` | Six-team architecture, boundaries, artifacts and operating decisions |
| `docs/04_TEAM_SPECIFICATIONS.md` | Leaders/workers, operations, outputs, diagrams, tests and gates for each team |
| `docs/05_BACKENDS_AND_CONNECTIONS.md` | Codex, Claude Code, OpenAI/Anthropic APIs and local-model connections |
| `docs/06_EXECUTION_AND_RECOVERY.md` | Durable state, retries, quota pauses, workspaces and resume |
| `docs/07_CONTRACTS_AND_SERVICE_INTERFACES.md` | Wire semantics, grants, service/CLI APIs and domain invariants |
| `docs/08_VERIFICATION_AND_ACCEPTANCE.md` | Nineteen acceptance scenarios, evidence and live/fixture distinctions |
| `docs/09_IMPLEMENTATION_PLAN.md` | Repository layout, milestones, coding tasks and assistant handover |
| `contracts/` | JSON Schema, operation-registry seed and executable reference SQLite DDL |
| `config/` | Safe fixture-default configuration and live connection placeholders |
| `diagrams/` | Editable Mermaid sources for topology, delivery and task states |
| `examples/` | Deterministic transport, quota, recovery and diagram-request fixtures |
| `templates/` | Editable handoff and bounded task briefs |
| `tools/validate_package.py` | Offline design-package checks using Python's standard library |
| `MANIFEST.json` / `VALIDATION_REPORT.json` | File integrity inventory and the checks actually performed |

## Authority and use

The numbered documents are the editable narrative sources; the consolidated guide is a generated reading copy. The JSON Schema governs field names/shapes, the contract document governs semantic constraints, and the requirements govern intended behavior. The SQL is a reference persistence design to implement with migrations. If these conflict, record and resolve a design defect before coding the affected behavior. Never silently choose an interpretation that weakens a mandatory requirement.

All provider-specific commands and fields are based on linked official documentation checked for this handoff. Implementers must pin and test actual installed versions and account capabilities. No model identifier, subscription entitlement or renderer installation is presumed. Codex is the preferred coding backend; its isolated launcher/login profile must pass an early capability spike. Fixture/core implementation can begin without an account.

The examples intentionally say `fixture` and `NOT RUN`. They illustrate valid transport, not a completed engineering cycle or approved release. A passed package validator means this handoff's files, contracts/examples and SQL checks passed; it does not mean the future application or provider adapters work.

## Verify this package

From an extracted package root with Python 3.11 or later:

```text
python tools/validate_package.py
```

The script is a deliberately limited validator for the JSON Schema subset used here. The implemented runtime should use a maintained Draft 2020-12 validator plus semantic authorization checks. Mermaid sources are editable; rendering remains a tool-installation step unless explicitly reported in the validation report. No account credentials are needed for package verification.

Course techniques are optional guidance. The system must support real engineering responsibilities, transparent simulated stakeholders, student edits, genuine executable testbenches and observable results. Do not mechanically generate every textbook diagram or pretend synthetic surveys are actual customer research.
