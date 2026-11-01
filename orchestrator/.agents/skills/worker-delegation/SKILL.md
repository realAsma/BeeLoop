---
name: worker-delegation
description: Choose and dispatch BeeLoop workers with runtime-supplied assignment instructions and authorized result callbacks.
---

# Worker delegation

## Choosing workers

Give workers coherent scopes and the relevant project workspace. Reuse one when
its context helps; start fresh for independent work. Workers can organize their
own execution and use native subagents or parallel work as needed.

## Dispatch and results

Call `delegate_worker(cwd=..., assignment=...)` with the specific scope, context,
deliverables, and checks. The runtime creates the worker, authorizes your exact
callback, and supplies standard execution, verification, and reporting
instructions from its assignment template. You do not need to rewrite that
boilerplate or edit worker permissions.

To reuse a worker previously created for you by this tool, pass its `worker_id`
and the same workspace.

Retain `receiver_agent_id` and end your turn; acceptance confirms dispatch, not
completion. Stay available for intake. Workers send delivery-ready results or
blockers back to you; communicate them through relevant sources.

## Worker state

When a worker's work may be useful later, set `save_state=true` on delegation.
The template then explicitly asks it to use `beeloop-state:save` and return its
record's `work_name` and `cwd`. Keep a brief summary and that reference in your
overall record for future sessions. Otherwise the worker simply returns results.
