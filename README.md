# BeeLoop

BeeLoop runs long-lived, directory-backed agents that receive events and message one another.

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
claude plugin marketplace add .
claude plugin install beeloop-tools@beeloop-tools
```

<details>
<summary>Root for BeeLoop and Memory Files (Optional)</summary>

By default, this checkout is the BeeLoop root and includes the orchestrator
workspace. To use a different prepared BeeLoop root containing `configs/` and
`orchestrator/`, create `~/.config/beeloop/loop.toml` before starting BeeLoop:

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

## Orchestrator

The orchestrator lives in the `orchestrator/` workspace. It is a special,
stateful, event-driven agent designed to be your chief of staff. It delegates
work, shares progress, and asks when it needs input.

For design details, see the [orchestrator instructions](orchestrator/AGENTS.md)
and [skills](orchestrator/.agents/skills/).

Grow the orchestrator by talking to it and adding skills for workflows you
repeat. To talk to it from the Codex or Claude Code CLI:

```sh
cd orchestrator && codex
# or
cd orchestrator && claude
```

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
