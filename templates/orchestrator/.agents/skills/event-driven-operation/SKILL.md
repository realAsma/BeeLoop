---
name: event-driven-operation
description: Monitor background jobs through BeeLoop inputs.
---

# Event-driven operation

For pollable jobs expected to take longer than about 5–10 minutes, configure an
input before ending the turn. Its envelope should wake the responsible agent
when the state changes.

## Operating procedure

- Read and follow the `beeloop-tools:input-create` skill to create or update a
  reusable input.
- A monitor may track multiple jobs, including worker-started ones. Keep owner agent
  IDs, identifiers, and log locations.
- Set cadence by duration. Long jobs may use aligned wall-clock buckets—for
  example, five-minute buckets at 12:05 and 12:10. Remember each job's last bucket;
  stay silent between due checks.
- Poll due jobs together; compare prior status and emit changes, missing jobs,
  or repeated errors.
- If several jobs change, keep their updates and emit one per poll in turn.
  Omit secrets and raw logs. Keep updates and finished targets until receiving
  agents handle them; then remove both.

## PID example

- Launch in background, record the PID, and poll due PIDs together.

## Slurm example

- Query due jobs by SSH host, using one session per host. Keep jobs after
  temporary failures.

## Timer

When PID or remote polling is impractical, schedule a one-shot timer for when
you expect progress; the timer wakes you to check.
