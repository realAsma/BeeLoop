# Configuration

Role settings: [router](roles/router/role.toml),
[orchestrator](roles/orchestrator/role.toml), [worker](roles/worker/role.toml),
and [planner](roles/planner/role.toml). TTL changes for existing agents also
require updating their runtime configuration snapshot and timer.

This checkout is the default root. To use another prepared root containing
`configs/`, `router/`, and `orchestrator/`, set `~/.config/beeloop/loop.toml`:

```toml
root = "/absolute/path/to/beeloop-root"
```

Memory defaults to `~/.beeloop_states`; see
[BeeLoop State configuration](https://github.com/realAsma/BeeLoop-State#configuring-the-root-folder-for-memory-files-optional)
to change it.

## Updates

Refresh the installed BeeLoop Tools copy after updating this checkout:

```sh
codex plugin add beeloop-tools@beeloop-tools
# or
claude plugin update beeloop-tools@beeloop-tools
```
