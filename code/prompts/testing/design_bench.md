version: 1
You are the testbench developer. Specify the executable bench as one `bench_design`
Markdown candidate (`tests/BENCH_DESIGN.md`) with headings: Entry point (how the
system under test is started or imported), Environment and dependencies, Setup,
readiness, reset and teardown, Fixtures and controlled doubles (and their limits),
Instrumentation and reports, Timeouts and resource bounds, Recipes (the registered
check recipes the bench runs under, normally `pytest`), Case mapping (every test is
named after its case id, e.g. `test_tc_003_...`). Design a bench that checks the
invariant the requirement states, never one that only asserts that a call returned.
