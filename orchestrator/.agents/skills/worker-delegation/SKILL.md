---
name: worker-delegation
description: Choose workers and send assignments using BeeLoop agent creation and messaging.
---

# Worker delegation

1. Prefer continuing with an existing worker for related work. Give it a broad
   assignment; it can launch subagents through its native harness as needed.
   Create another worker when a separate context would help. Use parallel
   workers for independent work.
2. For cross-turn work, create a persistent BeeLoop worker with
   `create_agent(role="worker", cwd=...)`. For per-agent settings, use its
   optional `config` object, for example
   `create_agent(role="worker", cwd=..., config={"backend": "codex", "backend_options": {"codex": {"model": "gpt-6-astra"}}})`.
   Native subagents may be used inside workers, but do not rely on them to
   persist across CLI turns.
   Assign each worker ownership of its inputs, timers, job monitoring, and
   cleanup; use the orchestrator heartbeat as a backstop rather than duplicating
   worker polling.
3. Send the assignment with `message` to its agent ID using the prompt below.
   Include the optional line only when the work may be useful later.

   ```text
   Assignment: <scope, context, deliverables and checks>
   Own persistent BeeLoop inputs, timers, job monitoring, and cleanup for this work. Arrange a BeeLoop wakeup and end your turn while waiting.
   Use the BeeLoop message MCP tool to promptly report meaningful results, new learnings, interesting behavior, completion, or blockers needing my decision. Skip routine acknowledgments and status chatter.
   [Optional: use beeloop-state:save and return work_name and cwd.]
   ```

4. Retain the worker ID. Do not wait for worker completion. End your turn; the
   worker will message you in a later turn. Share meaningful results, new
   learnings, interesting behavior, completion, or needed user input promptly
   through relevant intakes. Summarize routine progress at the heartbeat.

## Worker state

- Keep the returned `work_name` and `cwd` as an artifact reference in your
  overall BeeLoop State record for future sessions.
