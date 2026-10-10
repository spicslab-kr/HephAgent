"""CodingBackend adapters: fixture (scripted edits), Codex CLI, Claude Code CLI.

All three write only inside the task workspace handed to them. Under the project's
``development`` isolation mode that is a convention enforced by the client's own
sandbox, not an OS boundary; every result produced this way is labeled nonconforming
(handoff docs/06 s9). Strict mode refuses to launch until an isolation launcher exists.
"""
