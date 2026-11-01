# Advanced deployment and configuration

## Root and memory files

This checkout is the default BeeLoop root, including the orchestrator workspace.
For another prepared root containing `configs/`, `router/`, and `orchestrator/`,
create `~/.config/beeloop/loop.toml` before starting BeeLoop:

```bash
mkdir -p "$HOME/.config/beeloop"
cat > "$HOME/.config/beeloop/loop.toml" <<EOF
root = "$HOME/my-beeloop"
EOF
```

BeeLoop State stores memory files in `~/.beeloop_states` by default. See the
[BeeLoop State configuration](https://github.com/realAsma/BeeLoop-State#configuring-the-root-folder-for-memory-files-optional)
to change this location.

## Routing and workers

One persistent router in `router/` connects new external conversations to an
existing orchestrator for related intake or creates one. Connected conversations
go directly to their orchestrator. Configure the model in
[`roles/router/role.toml`](roles/router/role.toml); the router uses a read-only
filesystem sandbox and explicitly approved routing and state-reading MCP tools.
Its Codex policy overrides use the [setup's](../README.md#setup) plugin IDs and
bundled `state` server name; adjust those paths if your deployment names them
differently.

The router has no heartbeat or idle expiry. Its backend session rotates after
three weeks (`session_ttl.max_age = "21d"` in its role configuration). Rotation
keeps the logical router ID: expiry returns a read-only routing handoff, and the
next intake starts a session that rediscovers orchestrators and source mappings.
TTL policy is snapshotted when an agent is created; changing it for an existing
deployment also requires updating that agent's runtime configuration and timer.

Orchestrators in `orchestrator/` act as event-driven chiefs of staff: they
coordinate related intakes as their scope evolves, delegate work, share
progress, and maintain one overall BeeLoop State record each. Agent operations
require a harness-bound caller. See the
[orchestrator instructions](../orchestrator/AGENTS.md) and
[skills](../orchestrator/.agents/skills/).

`delegate_worker` dispatches workers with runtime-supplied standard assignments
and exact caller callback permission. Optional `save_state` requests a durable
worker record for future use; reuse is limited to the caller's own delegated
workers.

## Upgrades

After updates, refresh BeeLoop Tools' copied server and skills to match this
checkout (`codex plugin add beeloop-tools@beeloop-tools`
or `claude plugin update beeloop-tools@beeloop-tools`).

For existing deployments, copy effective `orchestrator` intake mappings to the
router intake key (router role, workspace, backend, and `default` instance),
preserving recipient IDs. Keep old mappings for rollback. New deployments need
no migration.

Start the gateway with the Python environment where BeeLoop and its source
dependencies are installed; input adapters inherit that environment's executables.
