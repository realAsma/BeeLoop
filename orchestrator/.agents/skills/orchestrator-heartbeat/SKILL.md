---
name: orchestrator-heartbeat
description: Check an orchestrator's work, keep it moving, and stop its heartbeat when the work is done.
---

# Orchestrator heartbeat

Review the current work and act as needed to keep it moving. Check whether it is
on track, what has changed since the user was last briefed, and whether any
blocker or missing user input needs attention.

Notify relevant sources when there is a useful result, meaningful change, or
needed input. Keep quiet when nothing needs the user's attention.

Evaluate whether the work is done:

- If done, cancel the recurring heartbeat timer that triggered this turn and
  tell the user that the heartbeat is disabled.
- If blocked, notify the user through their preferred communication channel.
  Keep the heartbeat active.
- If unsure whether the work is done, ask before cancelling. Keep the heartbeat
  active until the user confirms completion.
- Otherwise, leave the heartbeat active.

For new work after a cancelled heartbeat, use `timer_create` with the heartbeat
message and interval from the [orchestrator role](../../../../configs/roles/orchestrator/role.toml).
Re-enable it before saving State and ending the turn.

At the end of the heartbeat, update your overall BeeLoop State record with its
status, including progress, blockers, needed input, actions taken, or
completion.
