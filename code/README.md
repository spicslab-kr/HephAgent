# HEPHAGENT (sdlc-agent-teams)

> ChatKHU enabled: Kyung Hee University's AI gateway can serve as the Brain for any stage (`sdlc brain NAME chatkhu`, section 11).

Six AI teams run a software project the way a real engineering organisation does:
**Planning → Requirements → Design → Implementation → Testing → Maintenance.** Each team has
its own leader and workers, its own memory and files, and talks to the next team only
through a versioned package (명세서 plus diagrams, records and evidence) that you can read,
edit or reject. The teams think with your own **Claude Code** or **Codex** login; nothing
here needs an API key.

It is built for a software-engineering course: you bring a one-page project idea, watch
each stage do its job, step in wherever you want, and end up with documents whose every
requirement, design element and test is traceable back to your brief.

**All six stages work end to end** (Planning, Requirements, Design, Implementation,
Testing, Maintenance), including releases, change requests that reopen the owning team,
and a verifiable export. Test runs can be isolated in a Docker container once you have
Docker (section 10); coding clients still run in their own sandboxes, so their results stay
labelled *nonconforming* and the tool says so rather than pretending. Implementation writes real code through Codex or Claude Code inside a private
workspace and runs your check recipes, but only after you switch the project to
*development isolation* (see section 7), because no OS-level sandbox exists yet.

---

## 1. Install (once per computer)

You need Python 3.11 or newer and Git. Then, in this folder:

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.venv\Scripts\Activate.ps1
sdlc setup
```

```sh
# Ubuntu / macOS
sh scripts/setup.sh
. .venv/bin/activate
sdlc setup
```

`sdlc` is a command that exists only inside this folder's virtual environment. Every time
you open a new terminal: `cd` into this `code` folder and activate it
(`.venv\Scripts\Activate.ps1` on Windows, `. .venv/bin/activate` elsewhere); the prompt
then starts with `(.venv)`. Run all `sdlc` commands from this folder; projects are stored
under `code/.sdlc/<name>/`.

`sdlc setup` installs Node.js and the two official clients (Claude Code CLI, Codex CLI)
and tells you whether each is logged in. If not:

```text
claude auth login     # your Claude subscription (browser sign-in)
codex login           # your ChatGPT subscription (browser sign-in)
```

The tool never sees your password or tokens; the official clients keep them. Usage is
drawn from those subscriptions. No pay-per-token account is involved unless you add one
yourself (see *Advanced*).

## 2. Write your project idea

One Markdown file, in your own words. Say what you want, who uses it, the rules that must
never break, constraints, and what you deliberately leave undecided. Example:
[examples/library-reservation/brief.md](examples/library-reservation/brief.md).

PDF and DOCX work too (text and tables are extracted; scanned pages are reported, never
silently skipped).

## 3. Create the project

```sh
sdlc new demo --brief examples/library-reservation/brief.md
```

This creates the project `demo`, connects your Claude and Codex logins, checks them with
one small request each, imports the brief, and makes it the Planning team's starting
point. Options:

| Option | Meaning |
|---|---|
| `--brain claude` (default) / `--brain codex` | which model all teams think with |
| `--mode auto` (default) | the tool decides at the gates, you review afterwards |
| `--mode ask` | you approve every plan and every package yourself |

## 4. Run it

```sh
sdlc run demo                      # all available stages, automatically
sdlc run demo --until requirements # stop after Requirements, leave Design for later
sdlc run demo --step               # just the next stage
sdlc run demo --stage design       # exactly this stage
sdlc run demo --ask                # you approve each plan and package (yes/no prompts)
```

What one stage does, every time:

1. the **leader** reads the package it received and proposes a small task graph;
2. the graph is checked (only registered operations, no cross-team shortcuts) and
   approved, by the policy or by you;
3. the **workers** run, each seeing only the files granted to that team;
4. every result is checked against hard rules: required documents and headings, valid
   records, requirement ids that really exist, no invented test results;
5. the stage's package is composed and handed to the next team, and accepted as that
   team's starting point.

The run prints what happened and ends with one line, **what to do**. It stops, instead of
guessing, whenever a person is needed:

- a worker's result failed a rule twice (you will see the exact finding);
- your Claude or Codex limit was reached (the connection is paused, nothing else is
  called);
- a stage that is not built yet comes next.

Then:

```sh
sdlc show demo            # dashboard: each stage finished / blocked / waiting, and why
sdlc continue demo        # pick up after you fixed the cause
sdlc continue demo --quota-restored   # after your subscription limit reset
```

## 5. Read the results

```sh
sdlc export demo --to out
```

`out/planning/PROJECT_PLAN.md`, `out/requirements/REQUIREMENTS.md` and
`requirements.json`, `out/design/DESIGN.md` and `design.json`, research notes, risk
registers, scenarios, diagram sources. `sdlc show demo --stage design` lists one stage's
documents with their status (`draft`, `reviewable`, `superseded`, `stale`).

Reading the records is part of the course: `requirements.json` is the canonical list of
REQ ids with acceptance conditions and the brief paragraphs they came from;
`design.json` lists design elements and the REQ ids each one realises. The tool refuses a
design that cites a requirement id that does not exist.

## 6. Choose the model per stage

```sh
sdlc brain demo codex                 # Codex for every stage
sdlc brain demo claude --stage design # Claude for Design only
```

Switching never carries a conversation across teams; each task starts from its pinned
inputs. `sdlc show demo` lists which brain each stage uses and whether it is ready.

## 7. Implementation: code, checks, and taking over

Implementation needs a place to edit files. On a machine without a container or WSL2
sandbox, allow it explicitly:

```sh
sdlc isolation demo development
sdlc run demo --stage implementation
```

What happens: the Implementation leader plans bounded changes; a coding worker (Codex by
default, `sdlc new ... --coder claude` to change) edits a private workspace built from the
design; the runtime snapshots the result as a candidate with a patch; a check worker runs
your registered recipes (default: `pytest -v` with the project interpreter) and the
*collector*, never the model, records the outcome; the leader writes `IMPLEMENTATION.md`.
Every artifact from this mode is marked `nonconforming_isolation`: it is real code and a
real test run, but not one produced under the security boundary the design requires.

Want to write part of it yourself?

```sh
sdlc takeover demo impl-code --to C:\work\mine     # copies the task's workspace
# edit, then
sdlc return-work demo impl-code --from C:\work\mine   # becomes a candidate with your name on it
```

Recipes live in `.sdlc/demo/project.yaml` under `execution.recipes`; `{python}` means the
project's interpreter.

If a worker reports a *blocking* finding (for example, two design documents contradict
each other), the task stops as `needs_input` with its questions; `sdlc show` prints
them. Answer with a short memo (your decision, in markdown, PDF or DOCX), then redo the
stage on the new inputs:

```sh
sdlc answer demo decision.md --stage implementation    # delivers your memo to that team
sdlc run demo --stage implementation --again           # cancels unfinished tasks, keeps finished ones, plans afresh
```

The leader plans from the newest package of each upstream team plus your memos; documents
replaced by a rework are never shown to workers again.

## 8. Testing: an independent bench, real runs, defects that travel as documents

After Implementation hands over, `sdlc run demo` (or `--stage testing`) lets the Testing
team plan from the *requirements*, derive numbered cases (`TC-001`...), design and code a
bench in its own workspace on the exact delivered candidate, collect the tests, run them
through the collector, analyse failures and recommend RELEASE, HOLD or REJECT.

What the runtime enforces, not the model:

- a case nobody executed is `not_executed`, never a pass; skipped is not a pass either;
- a bench may add tests and doubles but may not touch the candidate's files;
- a defect must cite the execution record it comes from; a RELEASE verdict is refused
  while any case failed or was not executed; the raw counts are appended to every report.

If Testing files a critical or major defect, the runtime delivers a *defect package* to
Implementation, has it repaired under a fresh plan, hands the corrected candidate back and
re-tests it once (`--defect-loops N` changes the budget; `0` stops and asks you).

## 9. Release, maintenance, change requests

When Testing recommends RELEASE and the records agree (every declared case executed and
passed), the runtime records a release: `RELEASE_MANIFEST.json` pins the exact candidate,
requirements, design, benches, execution records and decision. Under development
isolation the release is marked **nonconforming**; the compliant gate is never claimed.

```sh
sdlc release demo                                 # record it yourself, or see exactly why the gate refuses
sdlc export demo --release --to out/release-001   # the release's exact closure + EXPORT_MANIFEST.json
sdlc verify-export out/release-001                # hashes recomputed; tampering or loss is reported
```

A bug report or a changed requirement is a memo you hand to Maintenance:

```sh
sdlc request demo change-request.md
sdlc run demo --stage maintenance
```

Maintenance triages, attempts a reproduction from the records, analyses impact and names
the earliest team that must change (an implementation bug goes to Implementation, a changed
rule to Requirements). The runtime delivers the change package there, marks the dependent
outputs of that team and everything downstream *stale* (kept, never deleted), re-runs those
stages on fresh plans, re-tests, records the next release and lets Maintenance verify
closure in `MAINTENANCE_REPORT.md`. `--change-loops 0` stops after routing and asks you.
Since no real operators feed this course project, memos stand in for them and say so.

## 10. Isolation: development mode or a container launcher

Two modes, switched per project:

- `sdlc isolation demo development`: coding and test runs use the client's own sandbox
  inside the task workspace. Everything works, every result is labelled nonconforming.
- `sdlc isolation demo strict --launcher docker --image python:3.12`, then
  `sdlc isolation demo probe`: test runs (checks, benches) execute inside a container with
  only the workspace mounted, no network and no host environment. The probe is a canary
  test; strict mode refuses executions until it passed on your machine. Coding clients
  are not launched inside a container yet (they would need an image with the client and
  its login), so strict mode refuses coding and tells you to switch to development mode
  for that step. The release manifest records which evidence was conforming.

Docker Desktop (Windows, macOS) or Docker Engine (Linux) is the only engine supported.

## 11. Extras: ChatKHU, local models, more inputs, diagrams, an overview page

- **ChatKHU** (Kyung Hee University generative-AI platform): its API Gateway is
  OpenAI-compatible, so it can be the Brain for any stage, billed in ChatKHU credits
  instead of your personal plans. Create a key in ChatKHU (API Gateway, "+ API 키 생성",
  shown once), keep it only in an environment variable, then:

  ```sh
  pip install -e .[local]
  export CHATKHU_API_KEY=...            # Windows PowerShell: $env:CHATKHU_API_KEY = "..."
  sdlc brain demo chatkhu                # all stages; --stage design for one; --model gpt-5 to choose
  sdlc brain demo claude                 # switch back any time; finished work is untouched
  ```

  The project records only the reference `env:CHATKHU_API_KEY`, never the key. The
  connection is probed live on binding; a task is refused, not failed, when the variable
  is missing. It is a Brain only: code is still written by the coder you chose.

- **Local model** (Ollama, LM Studio, vLLM, anything OpenAI-compatible):
  `pip install -e .[local]`, then `sdlc brain demo local --base-url http://localhost:11434/v1 --model llama3.1`.
  It is a Brain only: it writes documents, never code.
- **Inputs**: Markdown, plain text, PDF, DOCX, HTML, JSON and CSV (tables become Markdown
  tables with cell locators). Scanned PDFs without text are reported, not guessed.
- **Diagrams**: teams write Mermaid or PlantUML source; if `mmdc` or `plantuml` is
  installed the export renders SVG next to it, otherwise the source is the deliverable.
- **Overview page**: `sdlc export demo --to out --html` writes `out/index.html`, one
  self-contained page with every stage's documents, task states, releases and open
  questions, linking to the exported files.

Not shipped: an MCP facade (optional in the plan); the same services are reachable through
the Python API in `src/sdlc/application/`.

## 12. Change your mind

- **Edit a document.** Export it, edit, then import the edited copy as a new revision:
  `sdlc artifact revise --project demo --artifact REF --file my-edit.md` (the REF is shown
  by `sdlc show demo --stage ...`). The original stays; nothing is overwritten.
- **Ask for a diagram.** `sdlc diagram recommend` suggests which views answer which
  question; `sdlc diagram request` makes the team draw one as editable Mermaid or
  PlantUML source.
- **Redo a stage.** `sdlc run demo --stage requirements` plans and runs it again; the
  earlier package stays in history.

## 13. Cost and privacy, plainly

- Usage comes from your Claude and ChatGPT subscriptions; `sdlc show` says when the next
  run will call a paid provider.
- When a limit is hit the tool pauses that connection and never switches accounts or
  invents a reset time; you say when it is back.
- Nothing is sent anywhere except through the official clients you logged into.

---

## Advanced: the fine-grained commands

Everything `sdlc run` does is also available step by step, which is useful for teaching
what a handoff really is:

```text
sdlc import | review | stage propose | stage run | stage approve
sdlc handoff publish --auto | handoff deliver | handoff inbox
sdlc task list | task resume | task rerun | task cancel   sdlc isolation | answer | request | release | verify-export | takeover | return-work
sdlc diagram recommend | request | status      sdlc artifact revise
sdlc connection add | bind | list              sdlc doctor --live
sdlc cycle run                                 sdlc project mode
```

Add `--json` before any command for a machine-readable envelope. Advanced setup (API-key
connections to OpenAI or Anthropic, a local model server, fixture mode for development
without any account) is described in the work log and `config/project.example.yaml`.

### Status against the design

| Milestone | State |
|---|---|
| M0 contracts, M1 ownership and delivery, M2 durable runtime | done |
| M3 importers, Planning and Requirements teams, diagrams | done |
| M4 live backends (Claude Code, Codex, API brains), everyday commands | done |
| M5 coding backends, workspaces, Implementation team, take-over (development isolation) | done |
| M6 Testing team, benches, result truth, defect loop | done |
| M7 Release gate and manifest, Maintenance team, change loop with stale propagation, export round trip | done |
| M8 local model profile, HTML/JSON/CSV inputs, diagram rendering, overview page | done |
| Isolation launcher (Docker profile, canary probe, strict executions) | done, unverified on this machine (no Docker) |
| Coding clients inside the container boundary, MCP facade | not shipped |

Still open from M5: an OS/container isolation launcher (WSL2 or Docker profile) so that
coding and test runs can be *conforming*; this machine had neither, so strict mode keeps
refusing repository work.

Repository work in strict mode is deliberately blocked until the isolation launcher
exists: a client's own sandbox in a scratch folder is not a security boundary.

## For developers

- `pytest` runs the offline suite (no account, no network). `SDLC_LIVE=1 pytest tests/live`
  runs the opt-in live smokes.
- Design: the sibling [`handoff/`](../handoff/) package. Working notes and the next task:
  [WORK_LOG.md](WORK_LOG.md). Rules for contributors: [AGENTS.md](AGENTS.md).
- Layout: `src/sdlc/domain` (records, states), `verification` (schemas, gates),
  `application` (projects, handoffs, workflow, cycle), `runtime` (scheduler, leases),
  `teams` (policies per team), `prompts/` (editable prompt text), `adapters` (Claude,
  Codex, API, fixture), `cli` (`friendly.py` everyday commands, `main.py` advanced).
- Before committing: `scripts\cleanup.ps1` or `sh scripts/cleanup.sh` removes the
  virtual environment, caches and local project data; `.gitignore` covers them too.

License: MIT.
