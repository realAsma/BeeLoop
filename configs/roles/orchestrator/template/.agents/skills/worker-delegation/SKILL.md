---
name: worker-delegation
description: Create and launch BeeLoop workers with authorized result callbacks. Use when creating, launching, or delegating work to a worker; use event-driven-operation for background process and job monitoring.
---

# Worker delegation

- Get your agent ID with `get_agent_id()`.
- Create the worker with `create_agent(role="worker", cwd=...)`.
- Merge your agent ID into `allowed_receivers.ids` in the worker's
  snapshotted `$BEEBOT_ROOT/runtime/agents/<worker-id>/config.toml`; preserve all
  other TOML content and validate the exact file.
- Message that exact worker ID with the task and ask it to report its result or
  blocker to your ID.
- Retain the worker ID and end the turn.
