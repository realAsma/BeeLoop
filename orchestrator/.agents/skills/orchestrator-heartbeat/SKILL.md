---
name: orchestrator-heartbeat
description: Check an orchestrator's work, keep it moving, and stop its heartbeat when the work is done.
---

# Orchestrator heartbeat

Review the current work and act as needed to keep it moving. Check whether it is
on track, what has changed since the user was last briefed, and whether any
blocker or missing user input needs attention.

Use the heartbeat as a backstop for delegated work; do not duplicate workers'
job polling. Send at most one concise summary of meaningful changes or needed
user input per heartbeat. Use the existing thread for this work. If none exists,
use their preferred communication channel. Keep quiet when status is unchanged.

Evaluate whether the work is done:

- If you're confident all work is complete, cancel the recurring heartbeat timer
  that triggered this turn. No need to notify the user for this high-confidence
  cancellation.
- If blocked, keep the heartbeat active. Notify the user only when their input
  is needed, and do not repeat an unchanged blocker.
- If unsure whether the work is done, ask before cancelling. Keep the heartbeat
  active until the user confirms completion.
- Otherwise, leave the heartbeat active.

For new work after a cancelled heartbeat, use `timer_create` with the heartbeat
message and interval from the [orchestrator role](../../../../configs/roles/orchestrator/role.toml).
Re-enable it before ending your turn.
