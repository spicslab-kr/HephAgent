# Work log

Keep this current. Newest entry first. Record commands actually run and their results,
not intentions. Secrets and private model reasoning never belong here.

## 2026-10-11 — branch chatkhu_enabled: ChatKHU as a third Brain (preset, no adapter code)

- ChatKHU (Kyung Hee University generative-AI platform) exposes an OpenAI-compatible
  API Gateway (`https://factchat-cloud.mindlogic.ai/v1/gateway`, Bearer key; also an
  Anthropic-compatible `/claude` route). The existing `openai-compatible` connection
  type and `OpenAICompatibleBrain` cover it, so the change is a preset:
  `sdlc brain NAME chatkhu [--model M] [--stage S]` creates connection `chatkhu`
  (base URL preset, `credential_ref: env:CHATKHU_API_KEY`, quota scope `chatkhu`),
  refuses with instructions when the variable is unset, probes live, binds.
- Documented in `code/README.md` section 11, the root README badge, the student guide
  and `.env.example`. The `openai` client is an opt-in extra (`pip install -e .[local]`).
- Switching between claude, codex and chatkhu is per project or per stage, at any time;
  finished work and baselines are untouched; each has its own quota scope.
- Not verified live: no ChatKHU key in this session. Open point to check with a key:
  whether the gateway honours `response_format: json_schema`; if not, the brain reports
  `capability_missing` and the runtime's text-JSON validation path applies.
- Not done (option B): routing the Claude Code *coding* client through the gateway via
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`; it touches the native adapter's
  credential stripping and needs its own review.
- Tests: `test_brain_chatkhu_records_only_a_key_reference` (refusal without the key, no
  key in the project file, model override); full suite (real exit status): passed, 2 skipped.

## 2026-10-10 — Hands-on walkthrough: fixes found by the instructor

- `sdlc show NAME` crashed with `'ProbeRecord' object has no attribute '__dict__'` on
  any project whose clients had been probed live (`sdlc new` with claude/codex). The
  connection list serialised the slotted dataclass with `__dict__`; it uses
  `dataclasses.asdict` now. Regression test: `test_show_works_after_a_recorded_probe`.
  Not caught before because tests create projects with `--brain fixture` (no probe) and
  the live demo used `show --stage`, which does not list connections.
- Student guide: explicit `(.venv)` prompt check, the Korean "not recognised" text and
  the by-path fallback; account checks (`claude auth status`, `codex login status`,
  `sdlc doctor --live`) right after login; `continue` takes no `--ask`.

## 2026-10-10 — M8 extras + isolation launcher (done; launcher unverified on this machine)

### Completed

- Local model profile: `sdlc brain NAME local --base-url URL --model NAME` creates an
  `openai-compatible` connection (`local-model`) and binds it as a Brain; needs the
  `openai` client (`pip install -e .[local]`, new `local` extra). Documents only.
- Inputs: HTML (headings kept, scripts/styles dropped), JSON (pretty-printed verbatim),
  CSV (Markdown table with per-cell locators) next to Markdown/TXT/PDF/DOCX.
- Diagrams: `artifacts.diagrams.render` renders Mermaid/PlantUML to SVG when `mmdc` /
  `plantuml` is installed; otherwise the source stays the deliverable (`unrendered`).
- Overview page: `sdlc export NAME --to DIR --html` writes a self-contained
  `index.html` (stages, documents with status/origin/evidence labels, tasks, releases,
  open questions) linking to the exported files.
- Isolation launcher (`runtime/launcher.py`): `docker` profile wraps every execution as
  `docker run --rm --network none -v WS:/work -w /work IMAGE ...` with a minimal
  environment; a canary probe (workspace readable, outside file and project home
  unreadable, network blocked, no `*_API_KEY` leak) is journaled as `launcher.probed`
  and strict mode refuses executions until it passed. `sdlc isolation NAME strict
  --launcher docker --image python:3.12`, `sdlc isolation NAME probe`. Execution
  records made through a launcher carry `conformance: conforming` and no
  nonconforming finding; the runner strips host-only pytest config and pins
  `--rootdir /work`. Coding clients are still not launched inside the boundary;
  strict mode refuses coding with the reason (an image with the client and its login
  would be required). `.cmd` shims are launched through `cmd /c` on Windows.
- Not shipped: MCP facade (optional in the plan), WSL2 profile (cannot isolate the
  host filesystem; would fail the canary by design).

### Checks actually run

```text
pytest tests/integration/test_m8_extras.py -> 8 passed
  (html/json/csv extraction; csv memo reaches maintenance; render only with a renderer
   present (fake mmdc); brain local refuses without the client/server address; docker
   wrap; probe pass/fail/engine-failure; strict mode end to end through a fake docker:
   refused until a probe passes, coding refused with the precise reason, a check runs
   inside the boundary with conforming environment and an empty host environment;
   export --html)
full offline suite (real exit status, after every fix): passed, 2 skipped
live: NOT verified. This machine has no Docker/WSL2; the launcher ran only against a
fake engine. The local-model profile was not probed against a real server either.
```

### Design notes / deviations

- A launcher that passes the canary makes *executions* conforming; the lifecycle gate
  stays unsatisfied until coding runs inside a boundary too. The release manifest and
  environment records say which part is conforming.
- Test shims on Windows must be written in the ANSI code page: this repository's path
  contains Korean characters and `cmd` reads batch files in that code page.

## 2026-10-10 — M7: release gate, Maintenance team, change loop, export round trip (done)

### Completed

- `application/release.py`: `ReleaseGate` computed from records and documents, never
  from prose alone: the Testing team's release assessment must say RELEASE, every
  declared case must be executed and passed on the same candidate, fixture-origin
  records are refused. `ReleaseService.create` records a `ReleaseManifest`
  (contract-validated: candidate, requirements, design, benches, execution records,
  decision, runbook = implementation document, rollback = test report), a RELEASE.md
  summary and a `release.created` event with `conformance` (`nonconforming` under
  development isolation; the compliant gate is never claimed). Releases are history:
  never superseded, never stale.
- `teams/maintenance`: triage (`triage_note`), reproduce (`reproduction_report`, no
  claims without records), analyze_impact (`impact_analysis` JSON: earliest owner,
  classification, affected documents that the task actually received, rationale,
  revalidation), propose_change (`CHANGE_SPEC.md`), route_rework (`rework_package`),
  verify_closure (`MAINTENANCE_REPORT.md`); required headings enforced; prompts under
  `prompts/maintenance/`. Simulated operator input must be labelled as such (the
  course has no real operators; memos stand in, as the planning stage's role
  simulation does).
- `application/changes.py`: the change package (`change_request` bundle) goes to the
  earliest affected owner as an explicit delivery with a journaled decision; the
  owner's and every downstream team's current outputs become `stale`
  (`baseline.invalidated` events; evidence preserved, applicability withdrawn).
  Stale documents are skipped by planning, handoff composition, worker context and
  the everyday export; snapshots stay usable as repair baselines.
- Cycle runner: `maintenance` is an implemented stage; after Testing, a release step
  (recorded when the gate allows, otherwise the reasons are printed); after
  Maintenance, the change loop re-runs owner..testing on fresh plans with forced
  handoffs, the defect loop and a new release, then Maintenance verifies closure
  (`--change-loops N`, default 1). A deferred handoff now checks that the inbox holds
  the *previous stage's* package (a memo alone no longer suppresses it).
- `application/export.py`: `sdlc export NAME --release [ID] --to DIR` writes exactly
  one release's closure with `EXPORT_MANIFEST.json` (ids, revisions, SHA-256, owner,
  kind, origin, status, evidence labels such as `nonconforming_isolation` and
  `fixture-evidence`); `sdlc verify-export DIR` recomputes hashes and lists missing,
  tampered and extra files. Bytes round-trip exactly (Korean text checked).
- Everyday commands: `sdlc request NAME memo.md`, `sdlc release NAME`,
  `sdlc verify-export DIR`, `sdlc export --release`, `sdlc run --change-loops N`.
- Fixture brain replays sequences by the runtime's attempt counters
  (`_task_attempts_before`, `_operation_attempts_before`), so scripted multi-pass
  flows survive process restarts (needed for `request` + `run` as separate commands).
- The library-reservation fixture now covers all six teams: a change request (empty
  member id) routed to Implementation, a third implementation/testing pass with
  TC-004, release-002, closure. Sample memo: `examples/library-reservation/change-request.md`.

### Checks actually run

```text
pytest tests/integration/test_m7_maintenance.py -> 6 passed
  (release refused with reasons until evidence allows; six-team cycle: release-001,
   change routed to implementation, stale propagation to implementation+testing,
   release-002 on the exact new candidate, closure report; prior release and evidence
   preserved; export of the *older* release round-trips byte-for-byte, verify passes,
   tampering/extra/missing detected; quota pause inside maintenance, resume after
   --quota-restored with zero blind replays)
full offline suite (real exit status, after every fix above): passed, 2 skipped
live (demo project, Codex as maintenance leader/analysts), final state:
  `sdlc release demo` refused with reasons (HOLD; 16 declared cases not executed) ->
  `sdlc request demo change-request.md` -> deferred testing->maintenance handoff ->
  leader planned 6 tasks -> triage, reproduction, impact (earliest_owner
  implementation, corrective, REQ ids cited), change spec, rework package, handoff
  all completed -> change package b-change-011 delivered to implementation; 34
  implementation and 23 testing artifacts marked stale, releases untouched; the
  automatic re-run was disabled (--change-loops 0), so the Codex repair/re-test
  round of the change was not run live (the offline fixture covers it).
```

### Fixes forced by the live run (Codex as maintenance team)

1. The reproduction analyst raised a blocking finding because it cannot execute
   code: the policy now tells every maintenance operation that executing nothing is
   expected (record uncertainty), with `blocking` reserved for an impossible
   operation.
2. The impact analyst cited documents outside its inputs: an unknown `affected` item
   is now a warning (`unverified_affected`), the routing decision stands; the JSON may
   arrive fenced; the required-kind instruction now describes `impact_analysis` as a
   JSON object (it fell into the Markdown description before).
3. Codex writes section titles as bold lines (`**Steps**`) or setext underlines, not
   `#` headings; `markdown_headings` accepts all three and every section check uses it.
4. A task resumed by a person got no repair attempt (the bounded repair counted
   absolute attempts): one repair is now allowed after each first invalid output.
5. With `--change-loops 0` the runtime stopped *before* delivering the change package
   while telling the user to run the cycle; the package is now delivered and dependents
   are staled first, then the re-run is left to the participant.

### Design notes / deviations

- Stale propagation is coarse by design for v1: the owner and every team downstream
  of it are reopened; the impact analysis narrows what the owner changes, not what
  is marked stale. Narrowing by trace links is a later refinement.
- Reproduction is document-only (no execution); a bounded live reproduction would be
  a Testing execution on the released candidate, a later refinement.
- Runbook and rollback references point at the implementation document and the test
  report rather than separate documents; a dedicated runbook kind is a later refinement.

## 2026-10-10 — M6: Testing team, benches, result truth, defect loop (done)

### Completed

- `sdlc.teams.testing`: policies for `plan` (TestPlan JSON), `derive_cases`
  (`{"cases": [TestCase]}`, each validated, unique ids), `design_bench` (Markdown),
  `implement_bench` (coding job in a testing-owned workspace), `preflight` and
  `execute` (runner-driven), `analyze` (`defect_report` + one `defect` JSON per
  defect; every cited execution record must exist; a failed case without a defect is
  refused), `assess_release` (`test_report`; exactly one of RELEASE/HOLD/REJECT;
  RELEASE refused unless every case executed and passed; the runtime appends the
  collector's raw counts). Prompts under `prompts/testing/`.
- `teams/testing/mapping.py`: deterministic node-id -> case-id mapping (`test_tc_003_*`
  realises `TC-003`); per-case verdicts from records, worst observation wins, a case
  without an observation is `not_executed` and never a pass.
- Scheduler execution dispatch generalised: `testing.preflight` runs a collect-only
  pass and writes `preflight_report` (mapping, unmapped cases/tests; zero tests is a
  blocking finding); `testing.execute` runs the recipes on the bench with
  `source_ref` = the delivered candidate and `suite_ref` = the bench snapshot, writes
  `EXECUTION_REPORT.md` with one verdict per declared case, a `test_bench` record and
  flags cases that failed earlier on the same candidate+bench (instability retains the
  failure). A bench that modifies or deletes candidate files is refused at capture
  (`bench_modified_source`, needs_input).
- Same-team dependency outputs are now resolved transitively (an assessment that
  depends on the analysis still sees the execution records). Workspace baselines:
  newest same-team snapshot first; among pinned snapshots the freshest delivery wins,
  then team preference (`CONTINUES_OWN_CANDIDATE`: Testing always starts a bench on
  the delivered candidate, every other team continues its own newest candidate).
- Cycle runner: `testing` is an implemented stage; after it, open critical/major
  defects become a `defect` bundle delivered to Implementation (artifact-only, policy
  decision journaled), Implementation re-plans and repairs, the corrected candidate is
  force-published to Testing and re-tested. `sdlc run ... --defect-loops N`
  (default 1; 0 stops and asks).
- Fixture brain: `["@INBOX@"]` expands to every inbox delivery, `@DOC:<artifact_id>@`
  and `@REF:<kind>@` resolve to exact references, so scripted defects cite real
  execution records. Fixture coder: operation selectors matched with a stray colon.
- Catalogue clarification (`contracts/operations.json`): `testing.implement_bench`
  requires `scoped_repository`, `testing.preflight` requires `isolated_execution`.
- The library-reservation fixture now plants a defect (a cancelled copy can never be
  reserved again), a bench that exposes it (TC-003), the defect loop and the repair.

### Checks actually run

```text
pytest tests/integration/test_m6_testing.py -> 11 passed
  (mapping; unexecuted/skipped never pass; planted defect found -> defect package ->
   repair under -r2 ids -> re-test on the exact corrected snapshot -> RELEASE with
   runtime counts; bench editing the candidate refused; zero tests blocks preflight;
   missing defect refused; optimistic RELEASE refused)
pytest tests/integration/test_m5_*.py -> 21 passed (baseline/ranking changes)
full offline suite (real exit status): passed, 2 skipped
live (demo project, Codex leader + Codex bench coder, local runner), final state:
  plan (6 tasks) -> cases (31 TestCase records) -> bench design -> bench build: Codex
  wrote an independent bench (9 files under tests/independent_bench/, adds only) ->
  execute: 163 tests collected and passed by the collector on candidate
  implementation-developer-tests-r3 -> release assessment HOLD (31 cases not_executed:
  see fix 5 below) -> execute-r2 after the mapping fix: 15 cases passed, 16 declared
  cases reported not_executed (the bench did not realise them; honest) -> handoff.
```

### Fixes forced by the live run (Codex as testing leader, analyst and bench coder)

1. The testing leader refused to plan with blocking findings about a failing developer
   check and an open review in the delivered package. Context notes now tell every
   testing operation that such facts are inputs to test, never reasons to stop, and
   the common plan prompt reserves `blocking` for "cannot plan at all".
2. OpenAI's structured-output validator began rejecting `const` without `type`
   (`schema_version`): the transport projection now adds a `type` next to every
   `const`/`enum`.
3. The analyst wrote `test_cases` as Markdown: the required-kind instructions described
   any kind without a top-level schema as Markdown. `STRUCTURED_LIST_KINDS` describes
   `test_cases` as `{"cases": [TestCase]}` and supplies the TestCase schema; a bare
   list or a differently keyed list is normalised instead of refused.
4. A finding `related_refs` entry with an empty `sha256` consumed the single repair
   attempt; such references are dropped before validation (they carry nothing).
5. The analyst numbered cases `TC-M0M1-007`, the bench named tests `test_tc_007_*`:
   every case came out `not_executed`. Mapping now falls back to the prefix+number
   token when no exact id matches (`0071` never matches `071`). The live leader also
   skipped `testing.preflight`, which would have caught this before execution; the
   leader note now fixes the operation order.

### Design notes / deviations

- The runner, not the model, decides every per-case verdict; LLM-written reports only
  narrate records and are checked against them.
- Re-testing re-plans the whole testing stage on the corrected candidate (the bench is
  rebuilt). A narrower regression scope is a later refinement.
- Open from M5: no OS/container isolation launcher; all results stay nonconforming.

## 2026-10-09 — M5: Implementation team, coding backends, workspaces (done, development isolation)

**Isolation spike result (this machine, Windows 11):** no WSL2 (not installed), no Docker.
There is no OS/container boundary available, so strict mode keeps refusing repository
work and M5 ships under the handoff's explicitly labeled **development isolation mode**:
the coding client's own sandbox inside a private task workspace is the only boundary,
every result carries a `nonconforming_isolation` warning, and nothing produced this way
can pass the compliant lifecycle gate. `sdlc isolation NAME strict|development` switches.

### Completed

- `sdlc.artifacts.snapshots`: zip snapshots with an inner manifest (relative paths +
  SHA-256), deterministic order, safe extraction (traversal, symlink, size and hash
  checks), `diff_trees` producing a unified patch and added/modified/deleted lists.
- `sdlc.runtime.workspaces.WorkspaceManager`: per-task workspace from the pinned (or
  dependency-bound) `source_snapshot`, granted documents materialised read-only under
  `.sdlc-inputs/`, git initialised, writer lease per workspace, `capture` -> new
  `source_snapshot` + `patch` + `change_summary` artifacts, take-over export.
- `sdlc.runtime.graphs.dependency_outputs` + journaled `task.inputs_bound`: a successor
  task binds its completed dependency's snapshot at start (outputs unknown at planning).
- Coding backends: `FixtureCoding` (scripted files, interrupt simulation),
  `CodexCliCoding` (`codex exec --json --sandbox workspace-write -C WS --output-schema
  -o - `, JSONL items -> BackendEvents, thread id kept for same-task resume),
  `ClaudeCliCoding` (`claude -p --permission-mode acceptEdits --tools ... --json-schema`,
  cwd confinement, permission denials surfaced).
- `LocalProcessRunner`: trusted collector for registered recipes (`execution.recipes`,
  `{python}` = project interpreter), allow-listed environment without any API key,
  deadline, pytest `-v` line parsing to case observations; zero tests collected is
  `not_run`, failing assertions are `assertion_failed` (a valid observation).
- Scheduler: `scoped_repository` -> coding dispatch (workspace, events, capture,
  partial snapshot + checkpoint with unknown side effect on interruption);
  `isolated_execution` -> check dispatch (environment artifact, execution records,
  CHECK_REPORT.md, `checks_failed` warning); strict mode refuses both with zero
  launches and names the `sdlc isolation` command.
- `sdlc.teams.implementation`: plan_changes, code, developer_tests, check,
  review_changes enabled; prompts added. Model-authored execution records refused.
- Everyday commands: `sdlc isolation`, `sdlc takeover NAME TASK --to DIR`,
  `sdlc return-work NAME TASK --from DIR` (participant snapshot with provenance);
  `sdlc new` now binds a coder (Codex by default) and a local runner. Fixture script
  gained a `coding` section; the library-reservation example scripts the whole
  implementation stage (plan -> code -> check -> handoff).

### Fixes forced by the live run (Windows, Codex as coder)

- `sdlc connection add --type local-process` crashed on a display map that lacked the
  new type (the connection was already saved). Added the label.
- The coding client received the workspace as a *relative* path for both `cwd` and
  `-C`, so Codex resolved it twice and died with "path not found (os error 3)".
  `WorkspaceManager.path_for` is absolute now; both CLI coding adapters resolve too.
- Rebuilding a workspace for attempt 2 failed on read-only git object files
  (`WinError 5`). `remove_tree` chmods and retries; used for every workspace delete.
- The crash above happened after the workspace lease was acquired, so attempt 3 was
  refused with "lease is held by another owner" until the 15-minute TTL. `prepare`
  now releases the lease on any failure. Three regression tests added.
- With those fixed, the stage ran through (plan, code, developer tests, check, review,
  handoff all `completed`) but the content was wrong, which exposed four real defects:
  1. The Codex *coder* found five genuine contradictions between the design documents
     and stopped with blocking findings and a question, exactly as its prompt says; the
     runtime still marked the task completed and the check ran on a workspace holding
     only a work log. Now any proposal with a `blocking` finding settles as
     `needs_input`: documents are kept, the questions are recorded, `sdlc show` names
     the cause, and the cycle stops for a person (`_blocked_by_worker`).
  2. The contradictions existed because the design bundle carried both the original
     and the reworked architecture (different artifact ids, same path). A reworked
     document now shadows the older one at the same kind+path: `compose_manifest`
     keeps the newest and retires the other as `superseded`, journaled with a reason.
  3. The Codex *leader* ran in a scratch directory under the project, i.e. inside this
     host repository, so it read the orchestrator's own `AGENTS.md`/`WORK_LOG.md` and
     planned changes to `src/sdlc`. Brain scratch directories now live under the
     system temp directory (outside any git tree) and are removed after each call.
  4. The check task received requirement ids in `scope_ids` and treated them as recipe
     names (all "skipped"). Scope ids select recipes only when every one is a
     registered recipe; otherwise all recipes run and the report records the scope.
- New everyday flag `sdlc run NAME --stage X --again`: cancels the stage's unfinished
  tasks, keeps finished ones, tells the leader which task ids are taken and asks for a
  fresh plan on the current inputs. Needed whenever an upstream package changes.
- Observed, not ours to fix: Codex's shell tool failed twice inside the workspace-write
  sandbox on Windows ("setup refresh had errors"); Codex fell back to Node file reads.
- Second live run (cleaned bundle b-design-006, `--again`): the leader chose fresh `-r2`
  ids as asked; `plan_changes-r2` first returned a proposal with empty `sha256` strings
  in `related_refs` (schema rejection, one repair), then stopped with three *blocking*
  findings: the requirements' acceptance criteria want a bare list where the API
  contract specifies an `items` envelope, plus two provenance/gate conflicts. The stage
  ended `needs_input` with the question recorded: the new behaviour, working live on a
  genuine upstream defect. Two more defects surfaced from it:
  5. The leader still received the *old* design delivery (the inbox keeps history), so
     superseded documents reached it through the older grant. Planning now uses the
     newest accepted delivery per upstream team plus every participant memo, and drops
     superseded refs; brain dispatch omits superseded documents and says so in a
     `runtime/omitted-superseded.txt` note; coding inputs skip them too.
  6. A worker named `implementation-plan-changes` matched the `<team>-plan-` id prefix
     and was mistaken for the leader's plan task. Plan tasks are now recognised by
     operation (`<team>.plan_work`).
- Everyday `sdlc answer NAME memo.md --stage X`: delivers a participant decision memo to
  that team (markdown/PDF/DOCX through the normal import path) and names the follow-up
  (`--again`, or `task resume` when one task was waiting). `sdlc show` now prints a
  blocked task's `questions` and `preserved_documents`.
- A killed `sdlc run` (participant Ctrl-C) leaves its plan task `running`; `task cancel`
  handles it, and `recover` reclaims it once the lease expires. Not changed.
- Third live run (participant decision memo via `sdlc answer`, then `--again`): the
  leader planned five `-r3` tasks; **Codex wrote a real application** (13 files: store,
  service, HTTP layer, identity, 60 developer tests) and the collector ran pytest to
  exit 0. Three more defects, all offline-reproducible now (`test_m5_live_fixes.py`):
  7. The check recorded "passed, cases none parsed": the workspace lives under this
     repository, so pytest walked up, adopted the host `pyproject.toml` (quiet mode)
     and printed dots. The runner now pins `--rootdir` to the workspace and, when the
     candidate ships no config, a neutral `pytest.ini` under the system temp. The
     result-line regex also lost 9 of 60 parametrised ids; it now accepts any text.
  8. The runner was cached per connection at the preflight probe (no writer), so the
     attempt-specific stdout/stderr writer was never used and no logs were stored. A
     runner built with a writer is no longer cached.
  9. `review_changes` stopped with "snapshot and check report unavailable": brain tasks
     never received their same-team dependency outputs (only coding workspaces did).
     Brain dispatch now binds them (text inlined, binaries listed by reference, journaled
     `task.inputs_bound`), and `dependency_outputs` follows `task rerun` successor
     chains so a corrected check feeds the review.

### Checks actually run

```text
pytest tests/integration/test_m5_implementation.py  -> 17 passed
pytest tests/integration/test_m5_live_fixes.py      -> 4 passed
  (strict refuses with zero launches; capture; interruption keeps a partial snapshot
   with reconciliation_required; check passes / records a failing assertion; cycle run
   through implementation; take-over + return-work with participant provenance)
live (demo project, Codex brain + Codex coder, development isolation), final state:
  plan -r3 (5 tasks) -> code-r3 completed (13 files) -> developer-tests-r3 completed
  -> check-r3-r2 (rerun with the fixed runner): assertion_failed, 60 cases parsed,
     59 passed / 1 failed (a timing-dependent HTTP test in the candidate), stdout kept
  -> review-r3: needs_input, blocking "CHECK_FAILURE_UNRESOLVED" (refuses to approve a
     candidate with a failing check; a person decides: fix cycle or accept)
  -> prepare-handoff-r3: pending until the review is settled.
  Every live finding above became an offline regression test. Codex Windows note:
  its shell tool fails under the workspace-write sandbox; it still edits files.
full offline suite after all fixes: passed (exit 0), including the new live-fix tests
```

## 2026-10-09 — M4b: everyday command layer and stage driver (done)

**Why:** the participant found the step-by-step surface (ids to copy, publish/deliver/
review per stage) too close to the plumbing. The runtime is unchanged; a thin
user-facing layer sits on top of it.

### Completed

- `sdlc.application.cycle.CycleRunner`: drives stages end to end in either decision
  mode. `continuous` project mode -> policy actor (`cycle-run-v1`) decides at gates;
  `collaborative` -> a `gate` callback asks the participant and decisions are recorded
  as the participant. Restartable (skips finished stages, hands on a package left by an
  earlier `--until` run), stops with a readable reason on `needs_input`/`paused`, a
  rejected leader graph, a stale delivery, a missing input, a declined gate, or an
  unimplemented stage (`IMPLEMENTED_STAGES`). `WorkflowService.preview_plan` shows the
  proposed graph before approval.
- `sdlc.cli.friendly`: `setup`, `new NAME --brief FILE [--brain claude|codex|fixture]
  [--mode auto|ask]`, `run NAME [--until S] [--step] [--stage S] [--ask|--auto]
  [--objective S=text]`, `continue NAME [--quota-restored]`, `show NAME [--stage S]`,
  `export NAME --to DIR [--stage S]`, `brain NAME claude|codex|fixture [--stage S]`.
  Users never see or type delivery/bundle ids; the runtime generates and tracks them.
  Human output for `run`/`show` is plain lines ending with "what to do".
- Advanced commands (`cycle run`, `project mode`, and all earlier ones) remain.
- FixtureBrain substitutes `@INBOX@` in recorded leader graphs from the runtime's
  leader-context document, so fixtures no longer need test-time patching.
- README rewritten around the everyday flow: install, write a brief, `new`, `run`
  (with the automation choices), `show`/`continue`, `export`, `brain`, change your mind,
  cost and privacy; advanced commands and developer notes at the end.

### Checks actually run

```text
pytest -q --timeout 120   -> all passing incl. test_everyday_commands_new_run_show_export,
                             test_cycle_run_* (fixture brain, offline)
live: `sdlc cycle run` on a sample brief with Claude: Planning completed automatically
      (6 tasks); Requirements paused on a real session limit ("resets 8pm Asia/Seoul"),
      the connection was paused and nothing else was called, as designed.
```

## 2026-10-09 — M4: live backends (done, native CLIs verified live)

**Revision:** working tree, not yet committed.
**Assistant:** Claude Code (Fable 5.1).

### Completed

- `sdlc.adapters.cli_common`: argv subprocess runner (shell=False), explicit child
  environment that strips inherited `*_API_KEY` in native mode, timeout -> `unknown`
  with `completion_certainty: unknown`, error-text classification, secret redaction.
- `sdlc.adapters.brains.claude_cli.ClaudeCliBrain`: `claude -p --output-format json
  --json-schema ... --tools "" --append-system-prompt ... [--model] [--resume]`, prompt
  on stdin, throwaway cwd; parses `structured_output`, `session_id`, `usage`,
  `total_cost_usd` (measured usage); `is_error`/429 -> quota or rate limit;
  `probe(live=True)` reads `claude auth status` and compares the email with
  `expected_account_id` (`verified`/`mismatch`/`observed`/`identity_unverified`).
- `sdlc.adapters.brains.codex_cli.CodexCliBrain`: `codex exec --json
  --skip-git-repo-check --sandbox read-only --output-schema FILE -o FILE [-m] -`;
  parses JSONL (`thread.started`, `item.completed`, `turn.completed`, `turn.failed`);
  identity stays `identity_unverified` (the client exposes no account id).
- `sdlc.verification.schemas.schema_subset` / `transport_projection`: per-definition
  schema with only the referenced `$defs`, and a provider-safe projection without
  `pattern`/`format`/min-max keywords; the runtime re-validates against the full
  contract (docs/05 s6).
- `sdlc.adapters.brains.api`: OpenAI Responses (`text.format` json_schema strict),
  Anthropic Messages (`output_config.format`), OpenAI-compatible Chat Completions;
  lazy SDK import, injectable clients, refusal/incomplete/truncation/429 handling.
- `sdlc.application.connections.ConnectionService`: `add` (credential references only),
  `bind` (turns fixture_mode off), `record_probe` (`connection.probed` events),
  `dispatchable` (fixture always; others only after a successful *live* probe and no
  identity mismatch). Scheduler preflight uses it; repository operations stay blocked
  because the CLI brains declare `scoped_repository: unknown`.
- `sdlc.teams.design`: five design operations enabled with output obligations
  (DesignSet pinned to the delivered requirements ref; must-requirement coverage or
  disposition). Design prompts added.
- Leader context: `TEAM.plan_work` receives a runtime-built document with inbox
  delivery ids, available operations, a Task template and rules. `handoff publish
  --auto --target TEAM` composes the manifest (team outputs + dependency closure +
  forwarded inputs).
- CLI: `connection add|bind|list`, `doctor --live`, `handoff publish --auto`.
- `scripts/install-backends.ps1|.sh`: Node LTS, both CLIs, login check, optional
  `connection add/bind` + `doctor --live` for a project. Verified on Windows 11.

### Checks actually run

```text
pytest -q --timeout 120           -> 214 passed, 2 skipped (live tests need SDLC_LIVE=1); ~4 min
scripts\install-backends.ps1 -Project demo -SkipInstall  -> both connections dispatchable after live probe
live (Windows 11, Claude Code 2.1.295, codex-cli 0.162.0, both logged in):
  ClaudeCliBrain.generate -> validated WorkerProposal, measured usage (cost reported)
  CodexCliBrain.generate  -> validated WorkerProposal after transport projection
  walkthrough.py (planning stage, live Claude) -> see next entry / README manual
```

### Live walkthrough evidence (demo project, Claude Code as every team's brain)

`examples/library-reservation/brief.md` -> Planning (leader graph of 6 tasks approved;
all completed) -> `handoff publish --auto` forwarded brief + extraction + 7 planning
artifacts -> Requirements baseline accepted -> Requirements (leader graph of 5 tasks;
all completed; RequirementsSet with 12 requirements, 5/5 claims with brief locators)
-> Design baseline accepted -> Design (leader graph of 5 tasks; all completed).
19 completed tasks, all with measured usage (about 193k output tokens; the CLI's
list-price equivalent was about USD 20, drawn from the subscription, not billed).

Fixes the live run forced, each covered by offline tests afterwards:

1. Workers are told the exact artifact `kind`s their operation requires, with media
   type, and receive the record's JSON Schema as a document (`runtime/schema-*.json`).
2. The single repair attempt now carries the runtime's rejection reason (same inputs).
3. A worker reply that makes a runtime check raise (e.g. Markdown where JSON was
   expected) becomes `invalid_output` instead of stranding the task in `running`.
4. ```json fences around JSON candidates are tolerated.
5. The design gate now resolves the *pinned* RequirementsSet through the team view:
   `requirements_ref` must be that artifact, element requirement ids must exist in it,
   and every must-requirement needs an element or an open-question disposition. The
   first live design (requirements_ref = the brief, ids REQ-F-01 vs REQ-F-1) is
   rejected by this rule; `design-architecture` was rerun as a successor task.
6. `stage run --task` promotes a task whose retry wait has elapsed.
7. Policies have `context_notes`: Design and Requirements handoff workers are told the
   valid requirement ids and the exact pinned requirements references up front.
8. Policies have `normalize`: inside a DesignSet, any {artifact_id, revision, sha256}
   whose id+revision name a granted artifact gets the exact sha256 (models transcribe
   64-hex digests unreliably); a `ref_normalized` warning is recorded.
9. Requirement-id consistency gates: readable documents of Requirements and Design
   may cite only ids present in the canonical RequirementsSet (the live Requirements
   handoff had renumbered REQ-F-1 to REQ-F-01 and invented REQ-Q-*; this is what
   misled Design). `define_acceptance` may not renumber. The handoff no longer re-emits
   the JSON record; the auto-manifest forwards the existing artifact.
10. Workers are told the required Markdown headings of their document kinds.
11. CLI forces UTF-8 stdout/stderr (a Korean code page crashed `--json` output).

Rework loop executed live on the demo project after the gates landed: successor task
`requirements-handoff-r2` passed first time; the defective document was superseded and
the old design artifacts marked `stale` (manual for now; automatic stale propagation
is M7); bundle `b-requirements-004` delivered with `--expected-target-baseline
b-requirements-003` and accepted as Design's baseline; `design-architecture-v2`,
pinned to the new delivery, completed first time with `requirements_ref` =
`requirements-extract-requirements@1` and all twelve real ids cited.

### Design notes / deviations

- Native brains run with tools disabled in a throwaway directory. That is not an OS
  boundary, so coding operations (`inspect_system`, `probe_feasibility`,
  implementation.*) remain disabled until the isolation-launcher spike (M5).
- Codex exposes no account identity; `expected_account_id` cannot be verified for it.
- The suite now takes ~4 minutes (on-disk SQLite with synchronous=FULL in CLI tests).

### Next bounded task (M5)

1. Isolation launcher spike (WSL2 or Docker profile): canary reads, login inside the
   profile, secret isolation; then `CodingBackend` for Codex (`workspace-write`) and
   Claude Code (`--add-dir`, allowed tools), session resume bound to the ownership key.
2. Implementation team operations, task workspaces from immutable snapshots, single
   integrator, participant take-over/return-work.

## 2026-10-09 — M3: importers, first two teams, diagrams (done)

**Revision:** working tree, not yet committed.
**Assistant:** Claude Code (Fable 5.1).

### Completed

- `sdlc.artifacts.imports`: Markdown (paragraph/heading locators), PDF via `pypdf`
  (page locators, scanned-page warnings, encryption, page cap) and DOCX via
  `python-docx` (headings, paragraphs, table cells with row/col). Completeness is
  `complete` / `partial` / `unreadable` / `blocked` (library missing).
- `sdlc.application.ingress`: original + `import_extraction` JSON artifact delivered in
  one `manual_input` bundle; `needs_input` carries the exact gap (e.g. "2 of 3 units
  yielded no text").
- `sdlc.adapters.research`: `FixtureResearch` (recorded evidence, labeled) and
  `ImportedEvidenceResearch`. The scheduler retrieves evidence for operations needing
  `research_or_imported_evidence`, registers each item as a `research_evidence`
  artifact (origin fixture/tool, created_by_task) and feeds it to the Brain.
- `sdlc.teams.policies`: `OperationPolicy` = prerequisites + output obligations;
  `sdlc.verification.outputs`: kind-schema validation (RequirementsSet, DesignSet ...),
  Markdown section checks, diagram syntax checks, refusal of model-authored
  `execution_record`. `sdlc.teams.planning` and `sdlc.teams.requirements` enable their
  eleven specific operations with required kinds and quality rules (weak acceptance,
  unsourced retrieved claims, synthetic-only basis for a must, mislabeled synthetic
  evidence).
- `prompts/`: twenty editable prompt files with versions; `sdlc.teams.prompts` records
  version + SHA-256 per operation and falls back common -> generic.
- `sdlc.artifacts.diagrams`: selective rule-based recommendations with the question
  each view answers, notation per type (PlantUML for use case/deployment), starter
  sources, syntax validation, `unrendered` render status.
- `sdlc.application.workflow`: `propose_stage_plan` (leader plan_work task),
  `approve_plan` (participant decision, graph admitted, empty input_refs frozen to
  exact grants), `request_diagram`, `diagram_status`, `revise_artifact` (participant
  edit = new revision with participant origin; original superseded, never overwritten).
- Scheduler: output obligations enforced before registration (violations ->
  invalid_output -> one repair -> needs_input); accepted vs provisional mode (outputs
  `reviewable` only when every input delivery is the team's current baseline).
- CLI: `stage propose|approve`, `diagram recommend|request|status`, `artifact revise`;
  `import` reports extraction, needs_input and warnings.
- `examples/library-reservation/`: brief + recorded script covering planning
  (plan_work, frame_problem, research_context, prepare_handoff) and requirements
  (plan_work, extract, validate, prepare_handoff, request_diagram).

### Checks actually run

```text
.venv/Scripts/python -m pytest -q      -> 193 passed (offline; pypdf + python-docx installed)
```

End-to-end evidence (tests/integration/test_m3_teams.py): Markdown brief ->
planning leader proposal -> participant approval -> three planning tasks complete
(research evidence registered as fixture-origin artifacts; summary labels the
synthetic persona) -> handoff with the brief forwarded (publish without the closure is
refused) -> requirements baseline accepted -> requirements tasks run in accepted mode
-> RequirementsSet with REQ-001..003 traced to brief paragraphs, status `reviewable`;
Requirements is denied planning's private problem frame. PDF partial extraction yields
needs_input with the missing page count; DOCX round-trips Korean headings and table
cells; a participant diagram edit becomes revision 2 with participant provenance.

### Design notes / deviations

- Recorded task graphs use the placeholder `@INBOX@` for the delivery id because the
  id is generated at import time; tests and the CLI walkthrough substitute it. A live
  leader sees the real id in its documents.
- Rendering is not executed yet: diagram sources are syntax-checked and labeled
  `unrendered`; the renderer runs in the constrained ProcessRunner from M6.
- The four remaining teams expose only the five shared lifecycle operations.

### Next bounded task (M4)

1. `adapters/brains/openai.py`, `anthropic.py`, `openai_compatible.py`: Responses /
   Messages structured output with `max_retries=0`, local schema validation, refusal
   and truncation as distinct errors, usage recorded as measured when returned.
2. `adapters/coding/codex_cli.py` and `claude_cli.py`: argv subprocess with JSONL
   events, read-only vs workspace-write sandbox, session id persistence, explicit child
   environment (no inherited API keys in native mode), identity preflight
   (`expected_account_id` -> needs_input on mismatch, `identity_unverified` otherwise).
3. `sdlc doctor --live`: small explicit probe per connection, version pinning,
   capability record. Live smoke tests under `tests/live/`, opt-in only.
4. Isolation launcher spike for the first supported profile; until it passes,
   repository operations stay `capability_missing`.

## 2026-10-09 — M2: durable runtime (done)

**Revision:** working tree, not yet committed.
**Assistant:** Claude Code (Fable 5.1).

### Completed

- `sdlc.runtime.graphs.TaskGraphService`: admits a leader's proposal only if every task is
  same-team, runs a registered *and enabled* operation with valid parameters, depends only
  on same-team tasks, declares inputs its accepted deliveries grant, uses a configured
  backend profile; graph acyclic and within the fan-out bound. Task intent and the input
  manifest digest are frozen on admission.
- `sdlc.runtime.leases.LeaseManager`: coordinator / task_attempt / workspace /
  connection_permit scopes, CAS heartbeat, monotonically increasing fencing generation.
- `sdlc.runtime.quotas.QuotaRegistry`: durable per-scope pause (`quota.paused` /
  `quota.restored` events); never infers that availability returned.
- `sdlc.runtime.scheduler.Scheduler`: preflight (quota scope, connection readiness,
  adapter capabilities, prerequisite policy) with zero calls when blocked; fenced
  attempt with frozen resolved-connection snapshot; dispatch outside transactions via
  the team view; BackendEvent journal; completion validates the WorkerProposal, registers
  team-owned artifacts with provenance, and commits TaskResult + state in one fenced
  transaction. Error routing per docs/06 s6: rate_limited/provider_unavailable ->
  retry_wait (persisted backoff, retry-after honoured, attempt/elapsed budget ->
  paused); quota_exhausted/auth_required -> paused + scope pause; invalid_output ->
  one repair then needs_input (rejected output preserved as an artifact);
  context_limit/permission_required/capability_missing -> needs_input; cancelled;
  unknown -> paused. Checkpoints written on pause/needs_input. `recover()` marks
  expired attempts interrupted and reconciles (brain-only -> pending; workspace ->
  needs_input). `resume` (dry-run, `--quota-restored`), `cancel`, `rerun` (successor),
  `describe` (cause, preserved checkpoint, next command).
- `sdlc.teams.common`: policy registry; the five shared lifecycle operations of every
  team are enabled with generic prerequisites. Team-specific operations stay disabled.
- `sdlc.adapters.registry.AdapterRegistry`: builds adapters per connection; non-fixture
  types raise `capability_missing` until M4. Fixture connections may name a `script`.
- CLI: `stage plan|run`, `task list|resume|cancel|rerun`; `status` shows tasks, quota
  pauses, ready tasks and whether the next run would be billable.

### Checks actually run

```text
.venv/Scripts/python -m pytest -q      -> 182 passed (offline)
```

Recovery evidence (tests/integration/test_runtime.py): quota pause survives a
coordinator restart with completed siblings untouched and exactly two model calls;
resume dry-run changes nothing; resume without a restore declaration is refused;
rate limit honours retry-after under a virtual clock; three transient failures end in
paused; invalid output gets one repair; a lease taken over mid-generation discards the
late result (no TaskResult, no artifacts, `attempt.fenced` event); an attempt whose
lease expired is interrupted, reconciled to pending and replayed with a new attempt;
a second coordinator cannot start a running task.

### Design notes / deviations

- Concurrency is sequential inside one coordinator for now; `max_parallel_workers`
  and connection permits are enforced in a later milestone.
- A blocked preflight (capability/policy) records `task.blocked` and `needs_input`
  without creating an attempt, since no backend or process is launched.
- Fixture scripts keep their step cursor in memory, so across CLI processes a scripted
  failure replays; tests that need restart semantics share the adapter instance.

### Next bounded task (M3)

1. Importers: deterministic PDF (`pypdf`) and DOCX (`python-docx`) extraction with
   page/paragraph locators, warnings and a completeness assessment; `needs_input` on
   unreadable content (OCR optional capability).
2. Research port fixture + imported-evidence adapter with provenance records.
3. Planning and Requirements team modules: prompts under `prompts/<team>/`,
   prerequisite policies for their specific operations, output checks
   (RequirementsSet schema, trace links), provisional vs accepted mode.
4. Diagram service: Mermaid source generation, syntax check, unrendered labelling,
   participant edits as new revisions.

## 2026-10-09 — M1: ownership and delivery (done)

**Revision:** working tree, not yet committed (repository has no commits yet).
**Assistant:** Claude Code (Fable 5.1).

### Completed

- `sdlc.artifacts.store.ContentStore`: temp-file write + fsync, digest from written bytes,
  atomic no-overwrite publish to `objects/<aa>/<sha256>`, corruption detected on read and
  on republish, `verify_all()` for doctor. `safe_relative_path` rejects absolute, drive,
  backslash, traversal, control characters and empty segments.
- `sdlc.storage.database.Database`: foreign keys, WAL, `synchronous=FULL`, busy timeout;
  checksummed migrations in `storage/migrations/0001_initial.sql` (derived verbatim from
  `contracts/state.sql`); refuses a newer database or a changed checksum; explicit
  `BEGIN IMMEDIATE` transactions, nesting forbidden.
- `sdlc.storage.repositories`: parameterised SQL for projects, team baselines (CAS),
  events (project-wide sequence), artifacts + status projection (CAS), bundles,
  deliveries + grants, receipt ledger (events) + projection (CAS), outbox, decisions.
- `sdlc.application.projects`: `<home>/<name>/{project.yaml,state.sqlite3,artifacts/,workspaces/}`;
  in-memory variant for tests.
- `sdlc.application.artifacts.ArtifactService`: durable bytes first, then one transaction
  for metadata + `draft` status + journal event; participant ownership only via ingress.
- `sdlc.application.handoffs.HandoffService`: `publish` (ownership-or-accepted-grant,
  dependency closure, registered route, idempotent by digest, conflict on different
  bytes), `deliver` (grants = exact manifest, pending receipt + outbox in one
  transaction), `receive` (digest/route/grant/availability checks, baseline CAS ->
  `accepted`/`stale`/`rejected`, idempotent), `accept_baseline` (participant/policy
  actor decision, CAS on revision), `process_outbox`.
- `sdlc.application.views.TeamView`: own-team or task-declared accepted grants only;
  denials journaled as `view.denied`; `materialize` copies granted files only.
- `sdlc.application.ingress.import_material`: size limit, content-based type detection
  (Markdown/text now; PDF/DOCX stored with `extraction: pending` until M3), original
  bytes retained, `manual_input` bundle delivered to exactly one team.
- `sdlc.application.fixtures.load_fixture`: loads `minimal-cycle.json` through the real
  services, idempotent, remaps project id when loaded into a differently named project.
- CLI: `status` (baselines, inboxes), `import`, `fixture load`, `artifacts list`,
  `handoff publish|deliver|receive|inbox`, `review`; `doctor --project` now verifies the
  store and database integrity. `--json`/`--home` accepted after the sub-command.

### Checks actually run

```text
.venv/Scripts/python -m pytest -q      -> 166 passed (unit + contract + integration, offline)
scripts\cleanup.ps1 ; scripts\setup.ps1 ; pytest   -> clean tree round trip verified (M0)
```

Canary evidence (tests/integration/test_team_isolation.py): with six private canary
artifacts, every team can read its own and is denied the other five (30 journaled
denials, no canary text in any denial payload); provenance links, undeclared
deliveries, newer revisions and guessed ids are all denied before bytes are read.

### Design notes / deviations

- Delivery `receive` evaluates the target-baseline precondition at receipt time and
  records `stale`; `accept_baseline` is a separate decision with CAS on the baseline
  revision. Transport acceptance never changes a baseline.
- Receipt history is the `delivery.receipt` event ledger; `delivery_receipts` holds only
  the latest projection, as the DDL comments require.
- The outbox worker is a local in-process step (`process_outbox`); a separated transport
  with leases/redelivery is left for M2's lease machinery.
- The migration runner keeps its own `migrations` table (version, filename, checksum)
  beside the reference `schema_versions` table.

### Blocked / not done

- Tasks, attempts, leases, checkpoints, retry/quota state machine (M2).
- PDF/DOCX extraction and the interpretation task (M3); imports of those types are
  stored and flagged `pending`.
- Garbage collection of unreferenced store objects (deferred; harmless leftovers).

### Next bounded task (M2)

1. `sdlc.runtime.graphs`: validate a leader-proposed task graph (same-team deps, acyclic,
   registered + enabled operations, input refs within accepted grants, fan-out bound).
2. `sdlc.runtime.attempts` + `leases`: fenced attempts with heartbeat CAS; coordinator,
   task-attempt, workspace and connection-permit scopes.
3. `sdlc.runtime.scheduler`: `pending -> running -> ...` using the state machine; dispatch
   outside transactions; complete only with validated `WorkerProposal` -> artifacts ->
   `TaskResult`.
4. Normalized-error routing (`retry_wait` with persisted backoff, `paused` on quota,
   `needs_input` on permission/capability), checkpoints, `task resume --dry-run`.
5. Tests: quota pause survives process restart, stale writer cannot commit, duplicate
   dispatch prevented, interrupted attempt reconciles.

## 2026-10-09 — M0: contracts and skeleton (done)

**Revision:** working tree, not yet committed.
**Assistant:** Claude Code (Fable 5.1), from the GPT/Codex design handoff in `../handoff/`.

### Completed

- Repository scaffold: `pyproject.toml` (setuptools, editable install, extras for
  `openai`, `anthropic`, `pdf`, `docx`, `dev`), `.gitignore`, `.env.example`,
  `scripts/setup.{ps1,sh}`, `scripts/cleanup.{ps1,sh}`, `README.md`, `AGENTS.md`.
- Contracts shipped as package data in `src/sdlc/contracts/` (schema, operation
  registry seed, reference DDL), copied byte-for-byte from the handoff.
- `sdlc.domain.models`: 33 pydantic records mirroring every `$defs` entry; closed,
  immutable, string timestamps, semantic validators. Parity test against the schema.
- `sdlc.domain.states`, `teams`, `errors`, `identities`.
- `sdlc.verification`: canonical JSON + SHA-256, Draft 2020-12 validation with version
  gate, `check_fixture` cross-record rules.
- `sdlc.operations`: registry of 69 operations, all `enabled=False` until their
  prerequisite policy is implemented.
- `sdlc.config`: strict YAML config; placeholders detected; secret values rejected.
- `sdlc.ports` + `sdlc.adapters.brains.fixture`: port protocols and a deterministic
  scripted Brain with an invocation counter.
- CLI: `init`, `doctor` (local only), `validate`, `operations`, `status`, `contracts`.

### Checks actually run

```text
.venv/Scripts/python -m pytest        -> 126 passed
python -I ../handoff/tools/validate_package.py ../handoff  -> status pass (8/8 groups)
```

Environment: Windows 11, Python 3.14.5, pydantic 2.14.0, jsonschema 4.26.0, PyYAML 6.0.3,
pytest 9.1.1. No provider was contacted. No live backend is claimed to work.

### Design notes / deviations

- Models use pydantic's `python-re` regex engine (look-ahead in the path pattern).
- Models are lax about enum coercion; type strictness comes from the JSON Schema layer.
- Capability name `research_or_imported_evidence` added to the known set.
- Contracts live in `src/sdlc/contracts/` so the installed package can locate them.
