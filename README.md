# BeeBot

Long-running agents that live in a directory and are addressed by letter. An
adapter writes an envelope, the gateway loop pipes it to the dispatcher, and
the dispatcher resolves it to an agent -- creating one if none exists -- and
delivers it.

Architecture notes to follow. For the envelope format, see `inputs.d/README.md`.

## Running it

```sh
gateway/loop &            # poll adapters, dispatch what they produce
$EDITOR letterbox         # any edit posts it once; `touch letterbox` re-sends
tail -f logs/dispatch.log
```

Tests: `pytest_pwd tests/ -q`.

## The state store

Long-term work state lives in a separate repo, consumed here as a Claude Code
plugin and never modified from this one:
**https://github.com/realAsma/BeeBot-State**

```sh
git clone git@github.com:realAsma/BeeBot-State.git state_store
```

Then, in Claude Code:

```
/plugin marketplace add ./state_store
/plugin install beebot-state@beebot
```

`state_store/` is git-ignored here -- it is its own repository.
