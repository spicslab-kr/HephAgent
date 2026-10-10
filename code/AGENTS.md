# Instructions for coding assistants and engineers working in this repository

This is the implementation of the design in `../handoff/`. Read `../handoff/AGENTS.md`
first; its invariants apply here verbatim. Then read `WORK_LOG.md` for the current
milestone, what has been verified, and the next bounded task.

## Working rules

- Build in milestone order (`../handoff/docs/09_IMPLEMENTATION_PLAN.md`): recovery and
  isolation before rich generation features.
- `src/sdlc/domain/models.py` must stay in one-to-one parity with
  `src/sdlc/contracts/sdlc.schema.json`; `tests/contract/test_schema_parity.py` checks it.
  Change the schema and the model together, and record a design-change note in the log.
- No provider SDK imports outside `src/sdlc/adapters/`. No credentials in config, logs,
  artifacts or tests.
- Subprocesses use argument arrays, never shell strings. `shell=False` alone is not
  isolation.
- Fixture results are labeled fixture and never satisfy a release gate.
- Tests are offline by default. Live smoke tests go under `tests/live/` and are opt-in.
- Keep modules small and readable for students; prefer explicit service methods over
  framework magic.

## Before you stop

1. Run `pytest` and record the result in `WORK_LOG.md`.
2. Update the log: milestone, completed items, commands run, blocked items, next task.
3. Run `scripts/cleanup.*` before the tree is committed or pushed if you created a venv.
