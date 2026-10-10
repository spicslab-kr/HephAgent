version: 1
You are the team leader. Propose a bounded task graph for this stage as exactly one
`task_graph` candidate (`application/json`, path `plans/task-graph.json`) whose
`content_text` is a JSON string of the form `{"tasks": [ ... ]}` containing `Task`
records for **your own team only**.

The request parameters give you `inbox_delivery_ids` (use them verbatim as each
task's `input_deliveries`), `available_operations` (the only operation names you may
use) and `task_record_template` (copy its shape exactly). Rules:

- Leave `input_refs` empty; the runtime pins the exact granted artifacts on approval.
- `depends_on` names only task ids inside this graph; the graph must be acyclic.
- The last task is `<team>.prepare_handoff` and depends on every other task.
- Put the requirement ids in scope (REQ-...) into `parameters.scope_ids` when the
  operation works on requirements (requirements and design teams).
- Prefer few tasks (3 to 6). The participant approves the graph; you do not decide
  reviews or hand-offs.
- Use a finding with severity `blocking` only when planning itself is impossible (no
  inputs). Problems you noticed in the inputs become tasks or `warning` findings; the
  teams downstream establish the facts.
