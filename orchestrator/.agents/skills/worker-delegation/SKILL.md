---
name: worker-delegation
description: Choose and dispatch BeeLoop workers with runtime-supplied assignment instructions and authorized result callbacks.
---

# Worker delegation

## Choosing workers

Give workers coherent scopes in the relevant project workspace. Reuse a worker
when its context helps; start fresh for independent work. Workers can organize
execution and use native subagents or parallel work as needed.

## Dispatch and results

Call `delegate_worker(cwd=..., assignment=...)` with scope, context, deliverables,
and checks. The runtime creates the worker with your exact callback permission
and template instructions for execution, verification, and reporting; no
boilerplate rewrite or worker-permission edits are needed.

To reuse your worker from this tool, pass its `worker_id` and the same workspace.

Retain `receiver_agent_id` and end your turn to stay available for intake;
acceptance confirms dispatch, not completion. Workers return delivery-ready
results or blockers; communicate them through relevant sources.

## Worker state

When a worker's work may be useful later, set `save_state=true` on delegation.
The template then explicitly asks it to use `beeloop-state:save` and return its
record's `work_name` and `cwd`. Keep a brief summary and that reference in your
overall record for future sessions. Otherwise the worker simply returns results.
