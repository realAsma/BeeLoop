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
input. Its runtime registry must support multiple targets. In one poll, check
all registered PIDs efficiently and durably append every new terminal
observation to a pending set. Print nothing when that set is empty. Because
stdout supports one envelope per poll, persist a fair rotation cursor, emit one
pending target to its `agent_id`, and advance the cursor without deleting or
acknowledging the observation. Include the PID, label, terminal state, and log
location in `msg`; never include raw output or secrets.

Retain un-emitted observations and cycle through all pending targets, including
unacknowledged ones, so repeated emission of one terminal event cannot starve
others. The receiving flow must treat duplicates as idempotent and remove a
target only after reconciling its result. This preserves the input contract when
an emitted envelope is dropped.

## Slurm jobs

Use `beeloop-tools:input-create` to create or update one reusable Slurm-monitor
input whose runtime registry supports many jobs and SSH hosts. Each target
records the SSH host, Slurm job ID, owner agent ID, label, and relevant log
location. Batch targets by host and query all job IDs for that host together;
do not open one SSH session per job.

Durably retain every reportable terminal, missing, or repeated query-failure
observation. Print nothing when none are pending. Use the same persisted fair
rotation as the PID monitor to emit one envelope per poll to the owning
`agent_id`; retain un-emitted and unacknowledged observations so no host or job
can starve. Include the host, job ID, label, state, and log location, but keep
credentials, SSH options, and raw scheduler output out of envelopes. Remove an
observation only after the recipient reconciles it. Retain transient query
failures for later polling without discarding the monitored jobs.

## Timers and heartbeat

For a check that does not justify continuous polling, create a one-shot timer,
for example `timer_create(after="30m", message="Check job <id> on <host>")`, and
end the turn. Include enough context in the timer message to resume safely.

Keep a recurring heartbeat while any work remains as a fallback for missed
callbacks, broken inputs, or stalled jobs. On completion, cancel the heartbeat,
remove completed monitor registrations, update BeeLoop State, and tell the user
that the heartbeat is disabled.
