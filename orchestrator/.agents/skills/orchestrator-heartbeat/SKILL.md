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

When the work is done, cancel the recurring heartbeat timer that triggered this
turn and tell the user that the heartbeat is disabled. Otherwise, leave the
heartbeat active.

At the end of the heartbeat, update your overall BeeLoop State record with its
status, including progress, blockers, needed input, actions taken, or
completion.
