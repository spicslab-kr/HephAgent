# Student guide: running one project through the six SDLC teams

You know how to program. You have never seen this repository. This guide tells you exactly
what to type, where every file goes, and what you should see. Follow it in order; do not
skip the preparation.

The tool is a command-line program called `sdlc`. Six "teams" of AI agents (Planning,
Requirements, Design, Implementation, Testing, Maintenance) hand documents to each other.
You are the participant: you write the project description, approve plans, answer
questions, and read the results. The AI never talks to another team directly; everything
goes through files you can open.

## 0. What you need before anything else

| Need | Why | Check |
|---|---|---|
| Python 3.12 or newer | the tool is Python | `python --version` |
| Git | to get the repository | `git --version` |
| Node.js 20 or newer | the Claude Code and Codex clients are npm packages | `node --version` |
| A Claude account (claude.ai, Pro or Team) | the "Brain" that writes documents | you can log in at claude.ai |
| A ChatGPT account with Codex access | the client that writes code | you can log in at chatgpt.com |

No API keys are needed. The tool uses your logged-in clients. It never reads or copies
your login tokens.

Install Node.js if `node --version` fails: Windows `winget install OpenJS.NodeJS.LTS`,
Ubuntu `sudo apt install nodejs npm` (or the NodeSource package for a newer version).

## 1. Get the repository and set it up (5 minutes)

Open a terminal (PowerShell on Windows, a shell on Ubuntu or macOS).

```powershell
git clone https://github.com/spicslab-kr/HephAgent.git hephagent
cd hephagent\code                 # Ubuntu/macOS: cd hephagent/code
```

**Windows**

```powershell
.\scripts\setup.ps1               # creates .venv, installs the tool and test tools
.\.venv\Scripts\Activate.ps1       # your prompt now starts with (.venv)
sdlc --version
```

If PowerShell refuses to run the script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

**Ubuntu / macOS**

```sh
sh scripts/setup.sh
source .venv/bin/activate
sdlc --version
```

From now on **every command in this guide is typed inside the `code` folder with the
virtual environment active**. You can tell: the prompt starts with `(.venv)`. If you open
a new terminal, repeat the `cd` and the activate line.

If PowerShell says `'sdlc' is not recognized` (in Korean: `'sdlc' 용어가 ... 인식되지
않습니다`), the environment is not active in that window. Run the activate line again, or
call the program by its path, which works without activation:

```powershell
.\.venv\Scripts\sdlc.exe --version
```

## 2. Install and log in to the two AI clients (10 minutes)

```powershell
.\scripts\install-backends.ps1          # Ubuntu/macOS: sh scripts/install-backends.sh
```

This installs the `claude` and `codex` commands with npm and reports whether each is
logged in. For the ones that are not:

```powershell
claude          # opens a browser login; pick your claude.ai account; then type /exit
codex login     # opens a browser login for ChatGPT
```

Check again:

```powershell
sdlc setup --skip-install
```

Both lines must say `ready`. If one says `NOT ready`, log in again and re-check.

Check **which account** each client uses before spending anything on it:

```powershell
claude auth status      # JSON: "email", "orgName", "subscriptionType"; authMethod "claude.ai" = your subscription
codex login status      # "Logged in using ChatGPT" (Codex does not print the email; run `codex` once to see it)
```

Later, once a project exists, `sdlc doctor --project myproject --live` shows the same from
the tool's side: for Claude the identity is `observed` and compared with the email recorded
at setup; for Codex it is `identity_unverified`, which only means the client gives no email
to compare. To switch accounts: `claude logout` then `claude`; `codex logout` then `codex login`.

## 3. Make your work folder and write the project description (15 minutes)

All files **you** write live in one folder. Create it inside `code`:

```powershell
mkdir work
```

Write `work\brief.md` (any editor). It is the only input the AI gets at the start, so make
it concrete. Template:

```markdown
# <Project name>

## Problem
Two or three sentences: who has which problem today.

## Users
Who uses the system (two or three roles).

## Must have
- Behaviour 1 (one sentence, observable)
- Behaviour 2
- Behaviour 3
- Behaviour 4

## Constraints
- Language or platform if it matters (e.g. "Python, command line, no database server")
- One non-functional expectation (e.g. "answers within one second for 1,000 records")

## Out of scope
What the first version does NOT do.
```

Do not describe the solution or the architecture. That is the teams' job.

## 4. Create the project (1 minute)

```powershell
sdlc new myproject --brief work\brief.md --brain claude --coder codex --mode ask
sdlc isolation myproject development
```

What happened:

- A project folder `.sdlc\myproject` was created inside `code`. It holds the database and
  every document the teams produce. You never edit it by hand.
- Your brief was imported and accepted as the Planning team's starting point.
- `--brain claude`: Claude writes the documents. `--coder codex`: Codex writes the code.
- `--mode ask`: the tool stops at every plan and every hand-over for your yes/no.
- `development` isolation: code and tests run in the client's own sandbox on your
  machine. Every result is labelled "nonconforming", which is the honest label for
  "no container boundary". Keep your project home away from anything valuable.

Look at it:

```powershell
sdlc show myproject
```

## 5. Run the stages one at a time

The pattern for every stage is the same:

```powershell
sdlc run myproject --step --ask        # run exactly the next stage, asking at each gate
sdlc show myproject --stage <stage>    # read what it produced and whether anything is blocked
sdlc export myproject --to work\out --html   # copies every document to work\out, plus index.html
```

Answer the yes/no prompts with `y` unless the plan is obviously wrong. Open
`work\out\index.html` in a browser whenever you want the whole picture.

### 5.1 Planning (10 to 15 minutes, Claude)

```powershell
sdlc run myproject --step --ask
```

Produces `PROJECT_PLAN.md` (problem, scope, feasibility, risks) and research notes.
Expected last lines: `[completed] planning` and the package delivered to Requirements.

### 5.2 Requirements (15 to 25 minutes, Claude)

```powershell
sdlc run myproject --step --ask
```

Produces `REQUIREMENTS.md` and `requirements.json` with ids like `REQ-001`, plus
acceptance criteria. These ids are used by every later team; look at them once.

### 5.3 Design (15 to 30 minutes, Claude)

```powershell
sdlc run myproject --step --ask
```

Produces the architecture, interfaces, behaviour, an implementation task list and
diagram sources (Mermaid text you can paste into any Mermaid viewer).

### 5.4 Implementation (20 to 40 minutes, Codex writes code)

```powershell
sdlc run myproject --step --ask
```

What happens: the Implementation leader plans bounded changes; Codex edits a private
workspace; the runtime snapshots the result; pytest runs through the tool's own
collector; a reviewer reads the patch and the check report; `IMPLEMENTATION.md` is
written. The code itself is in the export:

```powershell
sdlc export myproject --to work\out --html
```

Look under `work\out\implementation\snapshots\...\candidate.zip` (the code) and
`work\out\implementation\runs\...\CHECK_REPORT.md` (what pytest observed).

If the reviewer refuses because a test fails, the stage stops with `needs_input`. That is
intended. Either fix it yourself (section 7) or answer with a memo (section 6).

### 5.5 Testing (30 to 50 minutes, Codex builds an independent test bench)

```powershell
sdlc run myproject --step --ask
```

Produces test cases (`TC-001`...), a bench, a per-case verdict table, defects if any,
and a recommendation: RELEASE, HOLD or REJECT. If a defect is found, the tool sends it
back to Implementation once and re-tests the corrected code.

Read the verdict table in `work\out\testing\runs\...\EXECUTION_REPORT.md`:
`not_executed` means the bench did not cover that case. It is never counted as a pass.

### 5.6 Release (1 minute)

```powershell
sdlc release myproject
```

Either a release id, or the exact reasons the gate refuses (for example "HOLD" or
"declared cases never executed"). A refusal is information, not an error. Export the
release and verify it:

```powershell
sdlc export myproject --release --to work\release-001
sdlc verify-export work\release-001
```

### 5.7 Maintenance (30 to 60 minutes)

Pretend you are the operator: write `work\change.md` describing one bug you found or
one rule that changed, two paragraphs. Say it is a simulated report. Then:

```powershell
sdlc request myproject work\change.md
sdlc run myproject --stage maintenance --ask --change-loops 0
```

Produces a triage note, an impact analysis naming the team that must change, a
`CHANGE_SPEC.md`, and the change package delivered to that team. With
`--change-loops 1` the tool also re-runs that team through Testing and records the next
release; budget an hour.

## 6. When a stage stops

`sdlc show myproject --stage <stage>` tells you why. Three cases:

| It says | Meaning | What you do |
|---|---|---|
| `needs_input` with questions | an agent cannot continue without a decision | write `work\decision.md` answering the questions, then `sdlc answer myproject work\decision.md --stage <stage>` and `sdlc run myproject --stage <stage> --again --ask` |
| `paused`, mentions a quota or limit | your Claude or Codex limit is reached | wait for the reset, then `sdlc continue myproject --quota-restored` (it asks at gates by itself in ask mode) |
| `paused` with another error | a provider or runtime problem | read the cause; `sdlc continue myproject` retries without replaying blindly |

`--again` redoes a stage on the current inputs: unfinished tasks are cancelled, finished
documents are kept, the leader plans afresh.

## 7. Doing part of the work yourself

```powershell
sdlc takeover myproject <task-id> --to work\mine      # copies the task's workspace
# edit files in work\mine
sdlc return-work myproject <task-id> --from work\mine  # becomes a candidate with your name on it
```

Task ids are shown by `sdlc show myproject --stage implementation`.

## 8. Where everything is

| What | Where |
|---|---|
| Your inputs (brief, memos) | `code\work\` (you created it) |
| The project (database, documents, workspaces) | `code\.sdlc\myproject\` (never edit) |
| Readable copies of every document | `code\work\out\<stage>\...` after `sdlc export` |
| The overview page | `code\work\out\index.html` |
| A release, verifiable | `code\work\release-001\` with `EXPORT_MANIFEST.json` |

Git ignores `.sdlc\`. Your `work\` folder is yours; add it to `.gitignore` if you do not
want to commit your briefs.

## 9. Commands you will actually use

```text
sdlc new NAME --brief FILE --brain claude|codex --coder codex|claude --mode ask|auto
sdlc isolation NAME development
sdlc run NAME --step --ask            next stage
sdlc run NAME --stage STAGE --ask     exactly this stage
sdlc run NAME --until STAGE --ask     everything up to this stage
sdlc run NAME --stage STAGE --again   redo a stage on new inputs
sdlc show NAME [--stage STAGE]
sdlc export NAME --to DIR [--html] [--release]
sdlc answer NAME FILE --stage STAGE   answer a blocked team
sdlc request NAME FILE                hand Maintenance a change request
sdlc release NAME                     record a release or see why not
sdlc continue NAME [--quota-restored] resume after a pause
sdlc takeover / return-work           do a task by hand
sdlc brain NAME claude|codex|local [--stage STAGE]   change who writes documents
```

`--mode auto` at creation lets everything run without prompts (`sdlc run NAME`); use it
once you know what the stages do.

## 10. Rules of the road

- Costs: your subscriptions only. A full project is a few dozen model calls.
- Privacy: every document goes to the chosen model. Do not put secrets in the brief.
- Honesty: the tool labels what it cannot prove (nonconforming, not_executed, fixture).
  If a report says a test passed, a real `pytest` run observed it.
- When in doubt: `sdlc show myproject`, read the `what to do:` line, do that.
