# Hands-on walkthrough: one project through all six teams

For: the instructor trying the whole process by hand, with the assistant on standby.
Time: about two to three hours of wall clock, most of it waiting for Codex or Claude.
Cost: your Claude and Codex subscriptions only; no API keys are used.

Every step says what you type, what you should see, and what to do if it stops.
All commands run inside the `code` folder with the virtual environment active:

```powershell
cd code
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
sdlc --version
```

## 0. Preparation (10 minutes)

1. Make sure both clients are logged in:
   ```powershell
   claude auth status
   codex login status
   ```
2. Write your own brief, two to five paragraphs, in a new folder outside this repository,
   for example `C:\sdlc-try\brief.md`: the problem, who uses the system, three to six
   must-have behaviours, one constraint. Do not describe the solution.
3. Pick the project home: a folder that is *not* inside a git repository you care about,
   for example `C:\sdlc-try\home`. Everything the tool writes goes there.

## 1. Create the project (2 minutes)

```powershell
sdlc new tryout --home C:\sdlc-try\home --brief C:\sdlc-try\brief.md --brain claude --coder codex
sdlc isolation tryout development --home C:\sdlc-try\home
```

Expected: the brief is imported and accepted as Planning's starting point; both clients
report `ready`. If one reports `NOT ready`, log in (`claude` / `codex login`) and check
again with `sdlc setup --skip-install`.

Tip: `--home` is needed on every command unless you run from the folder that contains
`.sdlc`. Pick one way and stick with it.

## 2. Planning, Requirements, Design (30 to 50 minutes, Claude)

```powershell
sdlc run tryout --home C:\sdlc-try\home --until design --ask
```

`--ask` makes the tool stop at every plan and every package for your yes/no. Say yes
unless the plan is obviously wrong. Expected at the end: `[completed] planning`,
`[completed] requirements`, `[completed] design`, and the design package delivered to
Implementation.

While it runs, open a second terminal and watch:

```powershell
sdlc show tryout --home C:\sdlc-try\home
sdlc show tryout --home C:\sdlc-try\home --stage requirements
```

If a stage stops with `needs_input`: read the questions in `sdlc show ... --stage X`.
Answer with a short memo and redo the stage:

```powershell
sdlc answer tryout C:\sdlc-try\decision.md --home C:\sdlc-try\home --stage design
sdlc run tryout --home C:\sdlc-try\home --stage design --again
```

If it stops with `paused` and mentions a quota: wait for your limit to reset, then
`sdlc continue tryout --home C:\sdlc-try\home --quota-restored`.

## 3. Implementation (20 to 40 minutes, Codex writes code)

```powershell
sdlc run tryout --home C:\sdlc-try\home --stage implementation
```

Expected: plan, code, developer tests, check, review, handoff. Look at the candidate:

```powershell
sdlc export tryout --home C:\sdlc-try\home --to C:\sdlc-try\out --html
```

Open `C:\sdlc-try\out\index.html`. The check report shows what pytest observed.

If the review refuses (`needs_input`, a failing test): that is intended. Decide: fix it
yourself (`sdlc takeover tryout TASK --to C:\sdlc-try\mine`, edit, `sdlc return-work ...`),
or answer with a memo and `--again`.

## 4. Testing (30 to 50 minutes, Codex builds a bench)

```powershell
sdlc run tryout --home C:\sdlc-try\home --stage testing --defect-loops 1
```

Expected: cases derived from your requirements, a bench in `tests/`, a collect-only
preflight, an execution report with one verdict per case, an analysis, a RELEASE/HOLD/
REJECT recommendation. If a defect is found, the tool sends it back to Implementation
once and re-tests the corrected candidate.

Read the per-case table: `not_executed` means the bench did not realise that case.
That is reported, never counted as a pass.

## 5. Release (1 minute)

```powershell
sdlc release tryout --home C:\sdlc-try\home
```

Either a release id (labelled nonconforming, because no container boundary was used) or
the exact reasons the gate refuses. Export and verify it:

```powershell
sdlc export tryout --home C:\sdlc-try\home --release --to C:\sdlc-try\release-001
sdlc verify-export C:\sdlc-try\release-001
```

## 6. Maintenance: a bug report or a changed rule (30 to 60 minutes)

Write `C:\sdlc-try\change.md`: one observed problem or one changed requirement, two
paragraphs, and say it is a simulated report (nobody operates the system yet). Then:

```powershell
sdlc request tryout C:\sdlc-try\change.md --home C:\sdlc-try\home
sdlc run tryout --home C:\sdlc-try\home --stage maintenance --change-loops 0
```

Expected: triage, reproduction from the records, an impact analysis naming the earliest
team to change, a change spec, and the package delivered to that team with the dependent
outputs marked stale. With `--change-loops 1` instead of `0` the tool also re-runs that
team through Testing, records the next release and verifies closure; budget about an hour.

## 7. What to tell the assistant

Paste the last `what to do:` line and, if it stopped, the output of
`sdlc show tryout --home ... --stage <stage>`. The assistant can read the project home
directly. Do not paste tokens or login output.

## Known limits you will meet

- Every result is labelled nonconforming until a Docker launcher is probed (section 10
  of the README). Coding never runs inside a container yet.
- Models sometimes stop with a blocking finding where a warning would do; a memo plus
  `--again` is the answer. Each such case so far became a runtime fix.
- A Codex session limit pauses the run; `--quota-restored` resumes without replaying.
