# Six SDLC team specifications

This document defines the behavior of the six distinct engineering teams. It is an implementation contract, not a requirement to reproduce every method in a textbook. Read the repository's architecture, schemas and runtime specification for transport and persistence rules; this document adds domain responsibilities without redefining `Task`, `TaskResult`, `Artifact`, `HandoffBundle`, `Delivery`, `BackendEvent` or `NormalizedError`.

## 1. Common team contract

The stable team IDs are `planning`, `requirements`, `design`, `implementation`, `testing` and `maintenance`. Each team has its own leader and its own workers. A worker role such as reviewer may occur in several teams, but each occurrence is a different agent with a different session, memory namespace and workspace. Bind every leader and worker identity permanently to its owning team; scope worker identities and sessions to the logical task, with attempts recorded separately. Never transfer a leader or worker conversation to another team.

Shared provider accounts, model weights, adapter classes, rendering tools and execution infrastructure are allowed. Shared agent memory and undeclared access to another team's working files are not. Every backend session is bound to one team and one logical task. It may resume across attempts of that same logical task only, after reconciliation; never resume it for a successor task, a different worker task, or another team. A successor task starts a fresh session with explicitly materialized task inputs.

All cross-team information passes through an immutable `HandoffBundle` and a runtime-recorded `Delivery`. Packages include specifications, selected evidence, diagram sources/renders, repository snapshots, executable tests, findings and change requests as applicable. The runtime may forward an accepted Requirements package to Testing for early test planning. That is a delivery, not a conversation between the two leaders. Downstream teams can inspect original materials only when their immutable references are explicitly included in their delivered input set.

The runtime routes packages, materializes allowed inputs, validates dependencies and persists results. It does not pass private reasoning or undocumented verbal advice between teams. A request for clarification becomes a small change/question package addressed to the responsible team; the answer returns in another package. Within a team, its leader can delegate bounded tasks and synthesize worker results.

### Common operation lifecycle

1. `TEAM.intake` inspects delivered artifacts, their revisions and extraction gaps; it proposes an input assessment.
2. `TEAM.plan_work` proposes a bounded task graph using registered operations, explicit dependencies, assigned roles and expected deliverables.
3. The runtime validates and dispatches it. Workers return artifacts and findings to their own leader.
4. `TEAM.review` checks semantic completeness and evidence. Deterministic services separately check schemas, references, hashes and applicable commands.
5. `TEAM.prepare_handoff` proposes the readable specification and artifact manifest. Runtime publication and delivery happen only after the applicable gate decision.

These are logical operation names to register, not permission for a model to invoke arbitrary functions. More specific operations appear below. Each can be used independently when its declared prerequisites hold.

### Input modes and gates

In **provisional mode**, a team can inspect incomplete imports, explore alternatives, draft tests or draw requested diagrams. Outputs retain unresolved assumptions and cannot silently qualify as accepted baseline evidence. In **accepted mode**, tasks pin exact accepted inputs. Accepting an upstream revision causes dependent outputs to be reassessed or marked stale; historical evidence remains intact.

Every handoff identifies its input baseline, artifact revisions, traceability, unresolved findings, assumptions, checks and decision authority. Completing a task is separate from accepting its output. Gate acceptance follows the participant's configured review policy; the model never fabricates human approval. Scope expansion, contradictory mandatory requirements, unknown critical contracts, invalid evidence and unmet required release conditions cannot be hidden by a positive prose summary.

A provider interruption preserves finished artifacts and unfinished work, emits the common normalized pause/error result and returns control to the runtime. Workers must not restart the whole cycle, switch providers silently or guess quota reset times. A useful partial output is retained as a draft, not promoted to a completed handoff.

## 2. Planning team — project direction and feasibility

**Human responsibility:** establish what problem is worth solving, for whom, with what constraints and a feasible development approach. Planning owns project direction, not detailed product requirements or implementation design.

**Agents:** planning leader; market/domain researcher; feasibility analyst; delivery/risk planner; planning reviewer. Small projects may instantiate only the roles needed, while keeping the team boundary intact.

**Inputs:** participant brief, intended users, constraints and success expectations; delivered existing-system snapshots or evidence; maintenance escalation when scope or feasibility changes. A natural-language brief is sufficient to begin provisionally. Missing budget or schedule is an unknown, not an invitation to invent an approved value.

| Operation | Responsibility and concrete result |
|---|---|
| `planning.frame_problem` | State problem, beneficiary, current alternatives, measurable success proposals, included and excluded scope. |
| `planning.research_context` | Retrieve market/domain evidence relevant to decisions; record source dates, claims, limitations and research questions. |
| `planning.assess_feasibility` | Compare technical, data, operating and resource constraints; propose bounded feasibility probes where needed. |
| `planning.plan_delivery` | Identify work packages, dependencies, resources, estimates and milestone evidence. |
| `planning.assess_risks` | Record risk, likelihood/impact rationale, owner, trigger and response; distinguish estimates from measurements. |

**Sequence:** clarify the problem from available evidence; research material uncertainties; propose scope and alternatives; assess feasibility; identify risks and delivery increments; reconcile contradictions and prepare the project plan. A feasibility probe produces observations in a planning-owned workspace. If a coding worker helps the probe, it is a planning worker, not an Implementation agent visiting Planning.

**Research and stakeholder substitution:** a simulated customer or domain expert can challenge the proposed value and operating assumptions after reading a bounded evidence packet. Their statements are labeled role simulation. Actual retrieved claims retain references. Invented interviews, survey counts and unsupported market-size estimates are forbidden. Search unavailability can result in imported evidence or an explicitly provisional assumption; it need not prevent drafting unrelated parts of the plan.

**Diagrams:** recommend a project context map when scope is unclear, a work/dependency graph when sequencing matters, or a schedule view when dates are meaningful. Compute dates and totals from recorded values. Do not generate a Gantt chart simply to fill a document section.

**Minimum outgoing package:** `PROJECT_PLAN.md` containing problem, users, success measures, boundaries, constraints, feasibility, work plan, risks and open decisions; linked research/evidence; accepted and proposed assumptions; selected diagram assets. Requirements receives the original brief as well as the plan so that a summary cannot erase an important constraint.

**Gate and participation:** the participant can choose scope, challenge estimates or author the plan manually. The acceptance gate requires an identifiable problem, scope and feasible path, plus dispositions for material risks. An unresolved resource impossibility blocks dependent commitments. A maintenance request that changes only code behavior without changing scope should not trigger a new Planning cycle.

## 3. Requirements team — observable product commitments

**Human responsibility:** transform goals into consistent, testable functional requirements, nonfunctional requirements, domain rules and acceptance conditions. It owns intended behavior; it does not infer requirements solely from existing code.

**Agents:** requirements leader; domain/customer-role researcher; requirements analyst; scenario/model author; requirements reviewer. Simulated expert roles are task-specific workers in this team.

**Inputs:** delivered Planning baseline, original brief and research, imported specifications, participant statements and any requirement-level change package. Provisional input supports exploration; acceptance requires resolution of conflicts affecting the proposed baseline.

| Operation | Responsibility and concrete result |
|---|---|
| `requirements.investigate_needs` | Identify actor goals, domain questions and evidence-backed simulated customer/expert objections. |
| `requirements.extract` | Produce stable requirements/rule IDs, statements, rationale, provenance and priorities from supplied materials. |
| `requirements.define_acceptance` | Specify observable expected outcomes and measurement conditions, including negative and failure behavior. |
| `requirements.model_scenarios` | Describe normal, alternate and failure flows; propose diagrams for the questions these flows raise. |
| `requirements.resolve_conflicts` | Present incompatible requests, scope tradeoffs and proposed decisions; retain disagreement until disposition. |
| `requirements.validate` | Detect ambiguity, contradiction, missing acceptance or source links, and untestable mandatory requirements. |

**Sequence:** identify stakeholders and unanswered questions; retrieve sources; conduct bounded role simulation; normalize proposed requirements; derive scenarios and acceptance; review consistency, priority and feasibility; establish the proposed requirements baseline. Web research informs product choices but does not independently authorize them. An LLM customer preference is a hypothesis until adopted as a participant decision or documented assumption.

If a student requests a mock survey, save the questionnaire, persona assumptions, synthetic raw responses and reproducible analysis. Label every result synthetic. A percentage of generated responses is not measured customer demand. Formal Delphi-like rounds are optional and do not gain evidential independence merely by using multiple models.

**Requirement quality:** each accepted requirement has an ID and revision, statement, kind, rationale/source, priority and acceptance conditions. Nonfunctional requirements name a metric, threshold, workload/environment and verification method where applicable. Unknown latency targets become decision questions, not fabricated commitments. Accessibility/usability requirements can use inspection or participant evaluation where automated measurement is inadequate.

**Diagrams:** recommend use cases for external actor goals and system boundaries; activity views for business workflows; state views for important lifecycle rules. A use-case drawing complements, rather than replaces, textual scenarios and nonfunctional requirements. Student requests such as “add cancellation failure paths from this PDF” must preserve source locations and flag unspecified policies.

**Minimum outgoing package:** `REQUIREMENTS.md` with scope, glossary, actors, functional/nonfunctional requirements, rules, acceptance and unresolved dispositions; canonical requirement records; selected scenarios/diagrams; evidence and decision references. Design and, when early testing is enabled, Testing receive separately recorded deliveries of the same immutable baseline.

**Gate and backflow:** accepted scope items must be testable and internally consistent. Material contradictions or missing required business decisions block affected commitments. New scope beyond the project plan is packaged to Planning. Design/Test questions return here as packages; clarification creates a revision with impact references rather than a hidden instruction to the other team.

## 4. Design team — implementable engineering decisions

**Human responsibility:** define architecture, interfaces, data, behavior and operating assumptions that realize the accepted requirements. Provide enough detail for another engineer to implement without inventing critical contracts.

**Agents:** design leader; architecture/options analyst; interface/data designer; interaction designer; repository/spike worker; design reviewer. Use a configured Codex coding backend by default for repository inspection and feasibility spikes; Claude Code or a local adapter can satisfy the same bounded job contract.

**Inputs:** delivered SRS and provenance, project constraints, accepted change scope, versioned existing code where relevant. A provisional design may compare options before all requirements are accepted, but cannot conceal those dependencies when handed to Implementation.

| Operation | Responsibility and concrete result |
|---|---|
| `design.inspect_system` | Describe existing interfaces, dependencies and conventions with exact snapshot references. |
| `design.compare_options` | Evaluate plausible structures/stacks against the actual constraints; issue decision records with rejected alternatives. |
| `design.define_architecture` | Allocate responsibilities, module boundaries, dependencies, deployment assumptions and NFR mechanisms. |
| `design.define_contracts` | Define APIs/messages, data constraints, invariants, errors, compatibility and migration rules. |
| `design.define_behavior` | Specify state transitions, important interactions, UI/CLI workflows and failure/recovery behavior. |
| `design.probe_feasibility` | Run a bounded prototype in a design workspace and report observed results and limitations. |
| `design.plan_implementation` | Decompose approved design into independently reviewable tasks with inputs, boundaries and validation obligations. |

**Sequence:** map accepted requirements to design obligations; inspect the existing system when supplied; select a supported approach; define module and data contracts; model consequential behavior; test uncertain assumptions with bounded spikes; review consistency and hand off implementation tasks. Exploratory code is labeled as such, with a base revision and observed commands. It becomes production code only through the later Implementation process.

**Diagrams:** choose component/dependency views for responsibility boundaries; sequence diagrams for ordering/errors; state diagrams for lifecycle invariants; ERD for persistence; class/object views for useful object models; deployment views for runtime placement. DFDs are appropriate when data transformation and trust boundaries need clarification. Explain the question each selected diagram answers. Rendering validates syntax; reviewers must additionally check references and meaning against contracts.

**Minimum outgoing package:** `DESIGN.md` with architecture, decisions, module responsibilities, interfaces, data, key behavior, operational/error handling, NFR realization and requirement mapping; interface/schema sources where applicable; selected diagram sources and renders; implementation task decomposition; spike evidence and known limitations. Define what each task can change and how it will be checked.

**Gate and participation:** all mandatory requirements need design coverage or a reasoned disposition. Critical interfaces and invariants must be concrete; conflicting diagrams/contracts block affected tasks. Students can select alternatives or modify diagrams; the team reconciles these edits before acceptance. An apparently infeasible requirement returns to Requirements as a change proposal. Design may not quietly weaken it or label a missing requirement “implementation detail.”

## 5. Implementation team — integrated, runnable candidate

**Human responsibility:** implement the accepted design, create developer tests, review changes and deliver a reproducible candidate. It owns source changes, not permission to rewrite the requirements.

**Agents:** implementation leader; bounded coding workers; developer-test worker; code reviewer; single logical integrator. Codex is the preferred coding adapter, not a hard dependency. Each coding session and worktree belongs to this team and one logical task. Session resume is permitted only across attempts of that task; a successor task starts a fresh session.

**Inputs:** delivered design, pinned requirements, task scope and source snapshot; executable prototype assets only if explicitly delivered; bug/change packages. Accepted mode authorizes scoped production implementation. Provisional work remains a draft candidate.

| Operation | Responsibility and concrete result |
|---|---|
| `implementation.plan_changes` | Resolve task dependencies, file ownership, interface prerequisites and check recipes. |
| `implementation.prepare_workspace` | Materialize exact inputs in isolated task workspaces with recorded base snapshots. |
| `implementation.code` | Apply one bounded change and emit candidate patches/snapshots with a work summary. |
| `implementation.developer_tests` | Implement unit/contract tests against specified behavior, including relevant failures. |
| `implementation.check` | Run registered build, lint/type and developer-test recipes; collect actual evidence. |
| `implementation.review_changes` | Evaluate scope, correctness, readability, maintainability and test adequacy from the diff and contracts. |
| `implementation.integrate` | Serialize accepted changes, detect stale bases/conflicts and rerun applicable integration checks. |

**Sequence:** plan changes; establish a runnable skeleton where useful; perform bounded coding and tests; review; integrate in dependency order; check the integrated candidate; document installation, configuration and limitations. Workers do not overwrite each other's directories. A stale patch requires reconciliation against the actual integration base. Participant changes follow the same snapshot/diff checks.

The runtime validates tool permissions. Generated application processes run without the coding agent's provider credentials. A coding agent's claim that it ran tests is insufficient: preserve execution collector records. Partial changes after quota exhaustion remain in their task workspace and must be reconciled before retry or provider handover.

**Diagrams:** maintain an as-built dependency or interaction view only when it helps understand the implementation. Differences from accepted design become explicit drift findings; updating a drawing is not authorization to alter the contract.

**Minimum outgoing package:** `IMPLEMENTATION.md` with exact candidate snapshot, build/run/dependency instructions, requirement implementation status, developer verification and known issues; source and lock/configuration files; developer-test assets and collected reports; reviewed change references. Include the original requirement/design versions needed by Testing, not only a developer-written summary.

**Gate and backflow:** a candidate must be reconstructable and correctly labeled. A diagnostic delivery to Testing may intentionally contain a failing build, but does not qualify as a release-ready candidate. Missing prerequisites, unauthorized contract changes or unresolved merge conflicts block affected integration. Ambiguous behavior returns to Design or Requirements through a documented package. Implementation does not negotiate directly with their workers.

## 6. Testing team — executable verification and release evidence

**Human responsibility:** independently evaluate the candidate against requirements and risks, construct a runnable testbench, report observed outcomes and make a release recommendation.

**Agents:** testing leader; test analyst; testbench developer; execution analyst; independent test/release reviewer. A testbench coding job is owned by Testing. It does not become an Implementation worker and cannot silently modify the production source under test.

**Inputs:** delivered SRS/acceptance rules, design, candidate snapshot, relevant developer evidence and environment profile. Early planning needs only a delivered requirements baseline; execution additionally requires an exact source candidate and runnable bench.

| Operation | Responsibility and concrete result |
|---|---|
| `testing.plan` | Choose levels, risks, techniques, environments, coverage obligations and exit criteria. |
| `testing.derive_cases` | Define IDs, source requirements, fixtures/inputs, expected outcomes, cleanup and oracle rationale. |
| `testing.design_bench` | Specify harness, drivers/stubs, state reset, instrumentation, environment, timeout and report recipes. |
| `testing.implement_bench` | Create executable tests and controlled fixtures/doubles in a testing-owned workspace. |
| `testing.preflight` | Collect tests; validate test-node-to-case mapping, dependencies, readiness and safe cleanup. |
| `testing.execute` | Invoke the execution backend against immutable source/suite inputs and collect raw/structured observations. |
| `testing.analyze` | Classify failures, defects, missing evidence and residual risk with links to actual observations. |
| `testing.assess_release` | Compare measured evidence with exit criteria and produce a recommendation for the exact candidate. |

**Sequence:** derive tests from requirements before relying on the implementation; select risk-relevant techniques; implement the bench; preflight collection; execute; investigate failures; issue defect packages; execute the required regression scope on delivered corrected candidates; assess release. Testing may reproduce a suspected fix in an exploratory workspace, but only an Implementation-owned delivered revision becomes the new production candidate.

**Testbench minimum:** system-under-test entry point; dependencies/environment; setup/readiness/reset/teardown; test inputs and fixtures; assertions or explicit evaluation rubric; controlled doubles and their limitations; timeout/resource bounds; seed where relevant; machine-readable results and raw logs. Map collected runner-node IDs to test cases and requirement/oracle revisions. Example: a reservation bench resets a test database, creates a capacity-one resource, submits competing booking requests and checks that the accepted capacity invariant holds; it must not merely assert that the API returned some response.

**Techniques and diagrams:** boundary partitions for limits, decision tables for rule combinations, state transition coverage for lifecycles, sequence views for integration failures and CFG views for code-driven structural coverage. Recommend the subset that answers current risks. Do not label line/branch measurement as complete path or condition coverage. A student can add a specific technique or test case through the same operation registry.

**Result truth and gate:** execution records identify source, suite, fixtures, environment, recipe, timestamps, exit status, counts and reports. Zero tests collected, skipped required cases, interrupted execution and unavailable environments are not passes. Synthetic runs and LLM-predicted outcomes never satisfy observed release criteria. A flaky rerun retains the earlier failure and reports instability. Bench defects are distinguished from product defects; uncertainty remains visible.

**Minimum outgoing package:** `TEST_REPORT.md`, test plan/cases, executable bench, exact tested candidate reference, collected evidence, defect dispositions, known limitations, release recommendation and deployment/rollback requirements. Participant usability evaluations can add actual evidence; role simulation remains labeled. Release acceptance belongs to the configured authority and runtime gate, not an optimistic testing narrative.

## 7. Maintenance team — controlled change and preserved history

**Human responsibility:** understand defects or evolving needs, assess impact, route changes to their owning teams and verify that a new release has adequate evidence. Maintenance coordinates the next cycle; it does not absorb the other five teams.

**Agents:** maintenance leader; incident/reproduction analyst; impact analyst; change/release planner; maintenance reviewer.

**Inputs:** delivered release baseline and test evidence, runtime observations, participant change requests, dependency/environment change evidence and historical accepted bundles. Imported bug reports can be incomplete and still begin triage provisionally.

| Operation | Responsibility and concrete result |
|---|---|
| `maintenance.triage` | Identify affected release, symptom/request, urgency, evidence and corrective/adaptive/perfective/preventive motivation. |
| `maintenance.reproduce` | Attempt a bounded reproduction against the exact release and retain observed results or uncertainty. |
| `maintenance.analyze_impact` | Trace affected requirements, contracts, source, tests, dependencies and operating assumptions. |
| `maintenance.propose_change` | Define scope, alternatives, risks, acceptance, regression obligations and migration/rollback concerns. |
| `maintenance.route_rework` | Prepare a package for the earliest affected owning team and enumerate downstream revalidation needs. |
| `maintenance.verify_closure` | Reconcile delivered revisions and testing evidence with the original request; prepare release notes and baseline proposal. |

**Routing decisions:** unchanged requirements/design with an implementation bug return to Implementation; an architectural/interface defect returns to Design; a changed business rule returns to Requirements; changed feasibility or project scope returns to Planning. Every return is a new explicit delivery. Unaffected accepted artifacts may be reused only with recorded impact rationale; their tests remain evidence about their original snapshots until rerun or justified otherwise.

**Sequence:** intake; reproduce or qualify uncertainty; recover missing context from delivered snapshots; analyze impact; propose/route the change; track runtime delivery status; inspect returned baseline and test packages; check migration/rollback when applicable; propose closure. Reverse-engineered intent must be labeled inferred rather than recovered fact. Maintenance does not instruct another team's coding session or merge its own invisible emergency patch.

**Diagrams:** an impact/dependency graph is useful for cross-cutting changes; before/after state or sequence diagrams explain behavior changes; as-built models can support undocumented legacy systems. Each view pins the old/new revisions it describes.

**Minimum outgoing package:** `CHANGE_SPEC.md` with motivation, reproduction, impact, earliest affected stage, proposed scope, acceptance and regression/migration obligations; after downstream completion, `MAINTENANCE_REPORT.md` with delivered revisions, observed verification, release notes and closure disposition. Preserve the former release and its evidence. A failure to reproduce yields a qualified finding and next investigation action, not a claim that the issue is fixed.

## 8. Student additions and minimum document structure

Every team exposes diagram requests through its own leader. `TEAM.request_diagram` records purpose, requested notation, scope and input revisions, then uses shared rendering infrastructure in the owning team's workspace. Recommend a useful subset; let participants add, replace or decline optional views. Retain editable source, render, reference mapping and validation. Unsupported notation returns a capability finding and an honest alternative; an invented lookalike is not a compliant result. Manual edits create revisions and require reconciliation before regeneration.

Readable deliverables may be one short Markdown document or several documents according to project size. Each needs: purpose/scope; exact inputs; decisions and substantive output; traceability/evidence; selected diagrams or an explanation that none is needed; unresolved issues and assumptions; checks and handoff disposition. The different team-specific contents above remain mandatory. Avoid blank ceremonial sections and exhaustive method inventories.

A participant may take over a task, supply a PDF, alter a diagram or implement a test manually. Normalize the submission, retain its original bytes/provenance, validate the relevant contract and return it to its owning team. Accept useful partial inputs without falsely declaring completeness. The goal is understandable engineering participation with explicit responsibility and reproducible evidence.
