# BeeBot

BeeBot runs long-lived, directory-backed agents that receive events and message one another.

## Quick Start

```sh
git clone git@github.com:realAsma/BeeBot-State.git state_store
codex plugin marketplace add ./state_store
codex plugin add beeloop-state@beeloop-state
codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
python3 -m pip install -e .
mkdir -p workspaces/orchestrator
cp -a templates/orchestrator/. workspaces/orchestrator/
```

<details>
<summary>Claude setup</summary>

```sh
claude plugin marketplace add ./state_store
claude plugin install beeloop-state@beeloop-state
claude plugin marketplace add .
claude plugin install beeloop-tools@beeloop-tools
```

</details>
