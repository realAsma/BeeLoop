---
name: worker-delegation
description: Create and launch BeeLoop workers with authorized result callbacks. Use when creating, launching, or delegating work to a worker.
---

# Worker delegation

- Get your agent ID with `get_agent_id()`.
- Create the worker with `create_agent(role="worker", cwd=...)`.
- Merge your agent ID into `allowed_receivers.ids` in the worker's
  snapshotted `$BEEBOT_ROOT/runtime/agents/<worker-id>/config.toml`; preserve all
  other TOML content and validate the exact file.
- Message the exact worker ID with clear scope, context, deliverables, and
  checks. Ask it to message your ID with its result or blocker.
- Retain the worker ID and end your turn. Do not wait for the worker's turn to
  finish.
