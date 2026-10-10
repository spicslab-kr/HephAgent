<p align="center">
  <img src="assets/hephagent-logo.png" alt="HEPHAGENT: a flame shaped like braces rising from an anvil" width="200">
</p>

<h1 align="center">HEPHAGENT</h1>

<p align="center"><b>Six isolated AI teams forge one software project end to end, and every hand-over is a file you can read.</b></p>

Planning → Requirements → Design → Implementation → Testing → Maintenance. Each team is a
small group of agents with its own workspace; teams never talk to each other, they deliver
immutable documents. A runtime owns the state, enforces the gates, runs the tests itself and
labels honestly what it could not prove. Built for a software-engineering course, usable by
anyone who wants to watch an SDLC happen rather than read about it.

**Author:** Kevin Nam (Kyung Hee University, SPICS lab) · **Questions:** <kvnam@khu.ac.kr> · **Issues:** [GitHub issues](https://github.com/spicslab-kr/software_engineering_tool/issues)

![python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![brains](https://img.shields.io/badge/brains-Claude%20Code%20%C2%B7%20Codex%20CLI%20%C2%B7%20local-8A2BE2)
![state](https://img.shields.io/badge/state-SQLite%20%C2%B7%20content--addressed%20store-003B57?logo=sqlite&logoColor=white)
![isolation](https://img.shields.io/badge/isolation-development%20%7C%20docker%20launcher-orange)
![milestones](https://img.shields.io/badge/milestones-M0%E2%80%93M8%20done-brightgreen)
![tests](https://img.shields.io/badge/offline%20suite-passing-brightgreen)
![status](https://img.shields.io/badge/status-course%20alpha-yellow)
![license](https://img.shields.io/badge/license-not%20chosen%20yet-lightgrey)

---

## Why this exists

Hephaestus forged for the gods; HEPHAGENT forges software with agents, on an anvil you can inspect.

Students know the SDLC as a diagram. Here they *run* it: write a one-page brief, approve
each team's plan, read the documents the teams produce, answer the questions they raise,
watch real tests run on real code, send a bug report back, and see the change travel to the
team that owns it. Everything is a plain file on disk, so a reader without the tool can still
follow the project.

Three rules make it honest rather than a demo:

- **Artifact-only hand-overs.** A team sees exactly the documents it was granted, nothing else.
- **The runtime decides, not the model.** Tests are run by the tool's own collector; a case
  nobody executed is never a pass; a release needs evidence, not a confident paragraph.
- **Labels for what is unproven.** Results produced without an OS-level sandbox are marked
  *nonconforming*; fixture evidence can never satisfy a release gate.

## Who it is for

| You are | Start here |
|---|---|
| A student who programs but has never seen this repository | [`code/STUDENT_GUIDE.md`](code/STUDENT_GUIDE.md) |
| An instructor running the whole process once by hand | [`code/WALKTHROUGH.md`](code/WALKTHROUGH.md) |
| An engineer who wants the commands, concepts and limits | [`code/README.md`](code/README.md) |
| A reader of the design (why it is built this way) | [`handoff/README.md`](handoff/README.md) and [`handoff/docs/`](handoff/docs/) |
| A contributor | [`code/AGENTS.md`](code/AGENTS.md), then [`code/WORK_LOG.md`](code/WORK_LOG.md) |

## Quick start

```sh
git clone https://github.com/spicslab-kr/HephAgent.git
cd HephAgent/code
./scripts/setup.sh                 # Windows: .\scripts\setup.ps1
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
./scripts/install-backends.sh      # installs Claude Code and Codex, checks both logins

sdlc new myproject --brief examples/brief-template/brief.md --brain claude --coder codex --mode ask
sdlc isolation myproject development
sdlc run myproject --step --ask    # one stage at a time; repeat
sdlc export myproject --to out --html && open out/index.html
```

You need Python 3.12+, Node.js 20+, a Claude account and a ChatGPT account with Codex.
No API keys: the tool uses your logged-in clients and never copies their tokens.

## How it works, in one picture

```text
 brief.md ──▶ Planning ──▶ Requirements ──▶ Design ──▶ Implementation ──▶ Testing ──▶ Maintenance
                                                            ▲                 │              │
                                                            └── defect package┘              │
                           ▲                                                                 │
                           └──────────── change request (to the earliest affected owner) ────┘
```

- Each arrow is one immutable delivery: a sealed bundle of exact document revisions.
- Implementation writes code in a private workspace; the runtime snapshots it and runs
  the checks. Testing builds its own bench on the delivered candidate and reports one verdict
  per declared case.
- A RELEASE recommendation backed by executed cases becomes a release manifest you can
  export and verify by hash.
- Maintenance takes a memo (a bug, a changed rule), routes it to the owning team and marks
  everything downstream stale while keeping the old evidence.

## Repository layout

```text
code/      the tool: Python package `hephagent` (CLI command `sdlc`), prompts, examples, tests, scripts
handoff/   the design package the tool was built from: requirements, architecture,
           team specifications, contracts, acceptance tests, implementation plan
```

## What it can and cannot do today

- Runs all six stages with Claude Code and Codex CLI as backends; a local OpenAI-compatible
  server can serve as a document-writing brain.
- Verified live on Windows with both clients; Ubuntu setup scripts are provided but not yet
  exercised on Ubuntu.
- Test runs can be isolated in a Docker container (canary-probed); coding clients still run
  in their own sandboxes, so their results stay labelled nonconforming.
- Not shipped: an MCP facade, coding inside the container boundary.

Details, evidence and every known limitation are in [`code/WORK_LOG.md`](code/WORK_LOG.md).

## Contributing

Read [`code/AGENTS.md`](code/AGENTS.md) for the rules the code follows (readable for
students, no silent fallbacks, no fabricated evidence), run the offline suite with
`python -m pytest -q`, and open an issue or a pull request. Live runs against the model
clients are never part of the default tests.

## Acknowledgements

I highly thank Codex and Claude Code to find un-debuggable issues from my code. Over half of the codes were modified by them during the debugging process.
As you can guess, this repo itself was used in generating this this repo itself.

## License

Not chosen yet. Until a license file is added, all rights are reserved by the author; ask
before reusing beyond the course.
