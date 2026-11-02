---
name: worker-delegation
description: Choose workers and send assignments using BeeLoop agent creation and messaging.
---

# Worker delegation

## Choosing workers

Give workers coherent scopes in the relevant project workspace. Reuse a worker
you created when its context helps; start fresh for independent work. Workers
can organize execution and use native subagents or parallel work as needed.

## Dispatch and results

Create a worker with `create_agent(role="worker", cwd=...)`, or reuse a worker
you created. New workers are authorized to reply to the orchestrator
that created them; no permission edits are needed.

Read the [assignment prompt](prompts/assignment.md), fill in the workspace,
scope, context, deliverables, and checks, and send it with `message` to the
worker's agent ID. Include the optional state prompt below when useful.

Retain the worker ID and end your turn to stay available for intake;
acceptance confirms dispatch, not completion. Workers return delivery-ready
results or blockers; communicate them through relevant sources.

## Worker state

When a worker's work may be useful later, append the
[worker-state prompt](prompts/worker-state.md) to explicitly request
`beeloop-state:save` and the record's `work_name` and `cwd`. Keep a brief summary
and that reference in your overall record for future sessions. Otherwise the
worker simply returns results.
