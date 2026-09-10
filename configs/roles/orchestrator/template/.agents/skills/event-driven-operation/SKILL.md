---
name: event-driven-operation
description: Keep delegated work, background processes, remote Slurm jobs, and delayed follow-ups moving through callbacks and BeeLoop events instead of blocking or repeatedly waiting.
---

# Event-driven operation

On every turn, identify the event that woke you, review the current work state,
and take the next useful action. Arrange a future event for unfinished work,
then end the turn instead of waiting in the foreground.

## Worker callbacks

Before delegating, call `get_agent_id()` and include your agent ID in the
worker's instructions. Tell the worker to message that exact ID with its result
or blocker when it finishes; an accepted launch means only that dispatch began.
Give long-running workers a session TTL or maximum age so they refresh instead
of accumulating an unbounded session.

After launch, take the worker ID from the accepted result. Update the worker's
snapshotted `$BEEBOT_ROOT/runtime/agents/<worker-id>/config.toml` so
`messaging.allowed_recipients.ids` includes your ID. Preserve every existing
TOML table, field, role, and recipient, and verify the resulting TOML before
depending on the callback. Close the post-launch race with a readiness
handshake: instruct the worker not to send its result before receiving a
`callback ready` message, then send that message to its exact worker ID only
after the verified update and require an `accepted` result. If an urgent or
final callback is attempted before readiness or is denied, the worker retries
with bounded backoff until one attempt is accepted, stopping after five attempts
and saving the undelivered result in its work state. Keep a heartbeat active
until the callback arrives or the work is otherwise reconciled.

## Background processes

Launch a long-running local command in the background and retain enough data to
diagnose it: PID, owner agent ID, working directory, start time, command label,
and output or log location. Do not keep a model turn open to poll it.

Use `beeloop-tools:input-create` to create or update one reusable PID-monitor
input with a runtime registry that supports multiple targets. Check all
registered PIDs efficiently in one poll. A new terminal state is reportable;
its `msg` includes the PID, label, state, and log location.

## Slurm jobs

Use `beeloop-tools:input-create` to create or update one reusable Slurm-monitor
input whose runtime registry supports many jobs and SSH hosts. Each target
records the SSH host, Slurm job ID, owner agent ID, label, and relevant log
location. Batch targets by host and query all job IDs for that host together;
do not open one SSH session per job.

A terminal, missing, or repeated query-failure state is reportable. Retain
transient query failures for later polling without discarding monitored jobs.
The `msg` includes the host, job ID, label, state, and log location, but not
credentials or SSH options.

## Monitor delivery

Both monitors durably append every new reportable observation to a pending set.
Print nothing when it is empty. Because stdout supports one envelope per poll,
persist a fair rotation cursor, emit one pending observation to its `agent_id`,
and advance the cursor without deleting or acknowledging it. Never include raw
output or secrets in an envelope.

Retain un-emitted observations and cycle through all pending targets, including
unacknowledged ones, so repeated emission cannot starve others. The receiving
flow must reconcile duplicates idempotently and only then remove the
observation or target. This preserves the input contract when an envelope is
dropped.

## Timers and heartbeat

For a check that does not justify continuous polling, create a one-shot timer,
for example `timer_create(after="30m", message="Check job <id> on <host>")`, and
end the turn. Include enough context in the timer message to resume safely.

Keep a recurring heartbeat while any work remains as a fallback for missed
callbacks, broken inputs, or stalled jobs. On completion, cancel the heartbeat,
remove completed monitor registrations, update BeeLoop State, and tell the user
that the heartbeat is disabled.
