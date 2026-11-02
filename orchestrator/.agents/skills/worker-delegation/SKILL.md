---
name: worker-delegation
description: Choose workers and send assignments using BeeLoop agent creation and messaging.
---

# Worker delegation

1. Choose a fresh worker or reuse one when its context helps. Give it a coherent
   scope; it can use native subagents as needed.
2. For a new worker, call `create_agent(role="worker", cwd=...)`.
3. Send the assignment with `message` to its agent ID using the prompt below.
   Include the optional line only when the work may be useful later.

   ```text
   Assignment: <scope, context, deliverables and checks>
   Report results or blockers back to me using the BeeLoop message MCP tool.
   [Optional: use beeloop-state:save and return work_name and cwd.]
   ```

4. Retain the worker ID. Do not wait for worker completion. End your turn; the
   worker will message you in a later turn. Communicate results or blockers
   through relevant intakes.

## Worker state

- Keep the returned `work_name` and `cwd` as an artifact reference in your
  overall BeeLoop State record for future sessions.
