---
name: worker-delegation
description: Create and launch BeeLoop workers with authorized result callbacks. Use when creating, launching, or delegating work to a worker.
---

# Worker delegation

- Get your agent ID with `get_agent_id()`.
- Create a worker with `create_agent(role="worker", cwd=...)`, or reuse one
  whose context is useful for the assignment.
- Get `<root>` with `get_beeloop_root()`.
- Add your agent ID into `allowed_receivers.ids` in the worker's
  snapshotted `<root>/runtime/agents/<worker-id>/config.toml`; preserve all
  other TOML content and validate the exact file.
- Message the exact worker ID with clear scope, context, deliverables, and
  checks. Give it room to organize execution and use native subagents as needed.
  Ask it to message your ID with its result or blocker. When its work may be
  useful later, explicitly request `beeloop-state:save` and its record's
  `work_name` and `cwd`; retain a summary and reference in your own state.
- Retain the worker ID and end your turn. Do not wait for the worker's turn to
  finish.
