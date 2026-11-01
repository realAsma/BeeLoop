# BeeLoop

BeeLoop runs directory-backed agents with memory that receive events and message one another.

## Setup

```sh
pip install -e .
```

### Codex Setup

```sh
codex plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
codex plugin add beeloop-state@beeloop-state
codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
```

### Claude Setup

```sh
claude plugin marketplace add git@github.com:realAsma/BeeLoop-State.git
claude plugin install beeloop-state@beeloop-state
claude plugin marketplace add ./
claude plugin install beeloop-tools@beeloop-tools
```

<details>
<summary>Root for BeeLoop and Memory Files (Optional)</summary>

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

</details>

## Getting Started

Launch the gateway in a detached tmux session using the Python environment
where BeeLoop and its source dependencies are installed. Input adapters inherit
that environment's executables:

```sh
tmux new-session -d -s beeloop beeloop
```

## Routing

One persistent router in `router/` connects new external conversations to an
existing orchestrator for related intake or creates one. Connected conversations
go directly to their orchestrator. Configure the model in
`configs/roles/router/role.toml`; the router uses a read-only filesystem sandbox
and explicitly approved routing and state-reading MCP tools. Its Codex policy
overrides use the setup's plugin IDs and bundled `state` server name; adjust
those paths if your deployment names them differently.

## Orchestrator

Orchestrators in `orchestrator/` act as event-driven chiefs of staff: they
coordinate related intakes as their scope evolves, delegate work, share
progress, and maintain one overall BeeLoop State record each.

For design details, see the [orchestrator instructions](orchestrator/AGENTS.md)
and [skills](orchestrator/.agents/skills/).

Talk through connected inputs and add skills for recurring workflows. Agent
operations require a harness-bound caller.

`delegate_worker` dispatches workers with runtime-supplied standard assignments
and exact caller callback permission. Optional `save_state` requests a durable
worker record for future use; reuse is limited to the caller's own delegated
workers.

After updates, refresh BeeLoop Tools' copied server and skills to match this
checkout (`codex plugin add beeloop-tools@beeloop-tools`
or `claude plugin update beeloop-tools@beeloop-tools`).

For existing deployments, copy effective `orchestrator` intake mappings to the
router intake key (router role, workspace, backend, and `default` instance),
preserving recipient IDs. Set retained router and orchestrator records to type
`PersistentAgent` for same-ID recovery after backend session loss. Keep old
mappings for rollback. New deployments need no migration.

To customize its personality:

```sh
cp orchestrator/SOUL_template.md orchestrator/SOUL.md
```

### Connect a Slack App

For the best experience, use Slack as the main communication channel. Follow the
[private Slack input setup](runtime/sources/slack-private/README.md).

Then record it as the primary communication channel:

```sh
echo 'The primary communication channel with the user is the configured Slack app in runtime/sources/slack-private.' >> orchestrator/MEMORY.md
```

### Example Skill: Feature to Pull Request

Request a feature in Slack: the orchestrator delegates, opens a draft pull
request, notifies you in Slack, and handles GitHub review comments until ready.

Ask your orchestrator to set up this flow first; GitHub setup varies by
organization.
