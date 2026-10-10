version: 1
Write the executable testbench in the repository you are in. The candidate under test
is already present; the bench design and case list are under `.sdlc-inputs/`. Add test
files, fixtures and controlled doubles only, under `tests/` (or the project's test
folder). Name every test function after the case it realises: case `TC-003` becomes
`test_tc_003_<short name>`. One test may serve one case; a case may need several
tests. Each test must assert the observable outcome the case expects, with state reset
between tests. Never modify or delete a file of the candidate: the runtime compares the
bench against the candidate and refuses a bench that touched production source. If a
case cannot be automated, say so in `findings` instead of writing a test that passes
vacuously.
