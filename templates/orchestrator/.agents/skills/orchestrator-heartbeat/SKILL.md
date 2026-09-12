---
name: orchestrator-heartbeat
description: Check an orchestrator's work, keep it moving, and stop its heartbeat when the work is done.
---

# Orchestrator heartbeat

Review the current work and act as needed to keep it moving. Check whether it is
on track, what has changed since the user was last briefed, and whether any
blocker or missing user input needs attention.

Notify the user through the primary communication channel for this work with a
concise status update. Ask for input when it is needed.

When the work is done, cancel the recurring heartbeat timer that triggered this
turn and tell the user that the heartbeat is disabled. Otherwise, leave the
heartbeat active.

At the end of the heartbeat, update this work's BeeLoop State with its final
status, including progress, blockers, needed input, actions taken, or
completion.
