---
name: agent-creation
description: Create BeeLoop agents with create_agent, or configure reusable roles, templates, and workspaces.
---

# Create a BeeLoop agent

1. Reuse `$BEEBOT_ROOT/configs/roles/<role>/` if it exists; otherwise create it.
   Preserve existing role files unless the request requires changes.
2. Add `role.toml` only for needed defaults or policies. Use TOML. Its `cwd`
   is a default; caller input overrides it.
3. Put optional seed files in `$BEEBOT_ROOT/templates/<role>/`. Creation copies
   only missing content into the workspace; it never copies `role.toml` or
   overwrites files.
4. Call `create_agent`. Pass `cwd` when the role has no default; it may be
   absolute or relative to `$BEEBOT_ROOT`.
