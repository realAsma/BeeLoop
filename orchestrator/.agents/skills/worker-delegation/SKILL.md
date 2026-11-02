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
4. Retain the worker ID and end your turn. Acceptance confirms dispatch, not
   completion; communicate results or blockers through relevant intakes.

```text
Assignment: <scope, context, deliverables and checks>
Report results or blockers back to me using message.
[Optional: use beeloop-state:save and return work_name and cwd.]
```

## Worker state

- When a worker returns a saved record, keep a brief summary and its `work_name`
  and `cwd` in your overall record for future sessions.
