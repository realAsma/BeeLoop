---
name: agent-creation
description: Create BeeLoop agents with create_agent, or configure reusable roles and workspaces.
---

# Create a BeeLoop agent

Agent creation requires a harness-bound caller.

1. Call `get_beeloop_root()` and reuse `<root>/configs/roles/<role>/` if it
   exists; otherwise create it.
   Preserve existing role files unless the request requires changes.
2. Add `role.toml` only for needed defaults or policies. Use TOML. Its `cwd`
   is a default; caller input overrides it.
3. Call `create_agent`. Pass `cwd` when the role has no default; it may be
   absolute or relative to the configured root.

## Per-agent configuration

Only a bound orchestrator may pass the optional `config` object to override
role defaults for one agent. Other callers may create permitted roles using
their defaults. Role creation permissions still apply.

```text
create_agent(role="worker", cwd="workspaces/project", config={"backend": "codex", "backend_options": {"codex": {"model": "gpt-6-astra"}}})
```

`config` accepts the role fields `type`, `backend`, `permissions`, `cwd`,
`backend_options`, `session_ttl`, `heartbeat`, `prompts`, and `allowed_receivers`.
Tables merge recursively; lists and scalars replace defaults. Set `cwd`,
`session_ttl`, or `heartbeat` to null to disable that default; `prompts: null`
clears default prompts. A blank prompt disables that hook. Caller `cwd` wins
over both config and role defaults. Backend options retain their native shape.

`permissions` uses the same three profiles for every backend: `read_only`,
`approve_for_me`, or `full_access`. The default permission profile is `full_access`.

Overrides persist across turns and session expiry. Type, backend, and cwd are
fixed at creation. Unoverridden runtime settings still follow the role; TTL and
receiver policies use the agent's role snapshot with its overrides applied.
Optional `instance` records existing creation metadata; it does not create a
source route. Internal IDs, status, counters, and timers are not config fields.
