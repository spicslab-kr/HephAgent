"""Team modules: operation prerequisite policies, prompts and semantic checks.

Each of the six teams gets its own package (M3+). ``common`` holds the lifecycle
operations every team shares (intake, plan_work, review, prepare_handoff,
request_diagram) and the policy registry that turns a registered operation into a
dispatchable one. Team modules never touch the database or other teams' files.
"""
