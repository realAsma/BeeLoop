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

By default, this checkout is the BeeLoop root and includes the orchestrator
workspace. To use a different prepared BeeLoop root containing `configs/`,
`router/`, and `orchestrator/`, create `~/.config/beeloop/loop.toml` before
starting BeeLoop:

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

Launch the BeeLoop gateway in a detached tmux session:

```sh
tmux new-session -d -s beeloop beeloop
```

This runs the gateway in the background, where it waits for inputs.
Start it with the Python environment where BeeLoop and its source dependencies
are installed. Input adapters inherit that environment's executables.

## Routing

New external conversations enter one persistent router in `router/`. It connects
related intake to an existing orchestrator or creates one. Once connected,
later events go directly to that orchestrator. Configure the router's model in
`configs/roles/router/role.toml`.
The router uses a read-only filesystem sandbox and explicitly approved routing
and state-reading MCP tools. Its Codex tool-policy overrides assume the plugin
IDs and bundled `state` server names shown in setup; update those paths if your
deployment names them differently.

## Orchestrator

The orchestrator lives in the `orchestrator/` workspace. It is a special,
stateful, event-driven agent designed to be your chief of staff. Each
orchestrator coordinates related intakes as its scope evolves, delegates work,
shares progress, and maintains one overall BeeLoop State record.

For design details, see the [orchestrator instructions](orchestrator/AGENTS.md)
and [skills](orchestrator/.agents/skills/).

Grow the orchestrator by talking to it through connected inputs and adding
skills for workflows you repeat. BeeLoop agent operations require a
harness-bound caller.

Orchestrators dispatch workers with `delegate_worker`. The runtime supplies the
standard assignment instructions and exact caller callback permission. An
optional `save_state` flag requests a durable worker record for future use;
reuse through this tool is limited to its caller's own delegated workers.

After updating BeeLoop Tools, refresh the installed plugin so its copied server
and skills match this checkout (`codex plugin add beeloop-tools@beeloop-tools`
or `claude plugin update beeloop-tools@beeloop-tools`).

When upgrading an existing deployment, retain its source ownership by copying
effective `orchestrator` intake mappings to the router intake key (router role,
workspace, backend, and `default` instance), keeping the existing recipient ID.
Set retained router and orchestrator records to type `PersistentAgent` so lost
backend sessions recover under the same ID. Leave the old route entries intact
for rollback. New deployments need no migration.

To customize its personality:

```sh
cp orchestrator/SOUL_template.md orchestrator/SOUL.md
```

### Connect a Slack App

For the best experience, use Slack as the main communication channel with the
orchestrator. Follow the
[private Slack input setup](runtime/sources/slack-private/README.md).

Then record it as the primary communication channel:

```sh
echo 'The primary communication channel with the user is the configured Slack app in runtime/sources/slack-private.' >> orchestrator/MEMORY.md
```

### Example Skill: Feature to Pull Request

Ask in Slack for a feature. The orchestrator delegates the work, opens a draft
pull request, notifies you in Slack, and addresses your GitHub review comments
until it is ready.

Work with your orchestrator to set up this flow. GitHub setup varies by
organization, so talk to your orchestrator first. :)
