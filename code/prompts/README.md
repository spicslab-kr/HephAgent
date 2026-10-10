# Prompts

Editable prompt bodies, one file per operation: `prompts/<team>/<operation>.md`.
Files may start with a `version:` line. The runtime records the version and SHA-256 of
the body used by each task. Missing team files fall back to `common/<operation>.md`,
then `common/generic.md`. `common/preamble.md` is prepended to every prompt.

Change the words here to change agent behaviour; Python logic stays in `src/sdlc/teams/`.
