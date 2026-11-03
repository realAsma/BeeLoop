---
name: agent-creation
description: Create BeeLoop agents with create_agent, or configure reusable roles and workspaces.
---

# Create a BeeLoop agent

Agent creation requires a harness-bound caller.

Use existing role definitions when possible. Only orchestrators should create
new role definitions when needed; other agents should ask their orchestrator.
Any harness-bound caller may create agent instances from roles they are
authorized to create.

1. Call `get_beeloop_root()` to locate `<root>/configs/roles/<role>/`.
   Preserve existing role files unless the request requires changes.
2. Add `role.toml` only for needed defaults or policies. Use TOML. Its `cwd`
   is a default; caller input overrides it.
3. Call `create_agent`. Pass `cwd` when the role has no default; it may be
   absolute or relative to the configured root.

## Per-agent configuration

Any harness-bound caller may pass optional `config` settings when creating an
existing role they are authorized to create.

```text
create_agent(role="worker", cwd="workspaces/project", config={"backend": "codex", "backend_options": {"codex": {"model": "gpt-6-astra"}}})
```

`config` accepts only `backend` and `backend_options`. Each backend's option
table overlays its role defaults; an explicit nested value replaces the whole
value without recursive merging. Backend options retain their native shape.
Pass the existing `cwd` argument to override the role's default workspace.

`permissions` uses the same four profiles for every backend: `read`, `edit`,
`approve_for_me`, or `yolo`. The default permission profile is `yolo`.

The selected backend and explicit options persist across turns and session
expiry. Unspecified options still follow the role. Type, permissions, prompts,
TTL, heartbeat, and receiver policy come from the role and cannot be supplied
through creation config.
