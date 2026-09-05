# BeeBot

Long-running agents that live in a directory and are addressed by letter. An
adapter writes an envelope, the gateway loop pipes it to the dispatcher, and
the dispatcher resolves it to an agent -- creating one if none exists -- and
delivers it.

Architecture notes to follow. For the envelope format, see `inputs.d/README.md`.

## Running it

```sh
pip install -e .          # src/beebot/ -- the installed code
export BEEBOT_ROOT="$PWD" # runtime/, logs/, inputs.d/, configs/ -- the tree
gateway/loop &            # poll adapters, dispatch what they produce
$EDITOR letterbox         # any edit posts it once; `touch letterbox` re-sends
tail -f logs/dispatch.log
```

Two roots, and they are not the same thing. The **code** is installed and found
by import, wherever pip put it. The **deployment tree** is a directory that is
written to every tick, and `BEEBOT_ROOT` is how anything finds it. It is
required and has no default: guessing it from the installed package's location
would answer `site-packages`, which is a perfectly real directory and the wrong
one. `gateway/loop` will derive it from its own location and export it, so in a
single checkout the export above is optional.

Tests: `pytest_pwd tests/ -q` (after the install).

## The messaging plugin

Agents talk to each other through `plugins/beeloop-tools`, a plugin installed
once into the tool:

```sh
/plugin marketplace add .
/plugin install beeloop-tools@beeloop-tools

codex plugin marketplace add .
codex plugin add beeloop-tools@beeloop-tools
```

Installed rather than passed per invocation. The backend used to hand every
`claude` call a `--plugin-dir` and an `--mcp-config` blob naming the server; an
installed plugin supplies that path itself, so the adapter's command line is
back to the three things a directory cannot know -- the session, the permission
profile and the model.

What the adapter still has to say is *which agent this process is*, and it says
it as `BEEBOT_AGENT_DIR` in the child's environment. Claude Code passes its
environment down to the stdio MCP servers it starts (verified against 2.1.257),
so the server reads its binding -- and the inherited `BEEBOT_ROOT` -- from
there. Out-of-band on purpose: the server derives the sender's identity and
grants from that directory, so if it were a tool argument, anything the model
read could make it send under someone else's name.

Codex uses the inline MCP definition in `.codex-plugin/plugin.json`. Unlike
Claude, it forwards only named variables to stdio servers, so that definition
allowlists `BEEBOT_AGENT_DIR` and `BEEBOT_ROOT`. Its relative `cwd` is resolved
against the installed plugin root; no backend-generated MCP configuration is
needed when a Codex backend is added.

One requirement, and it is easy to miss because nothing fails until an agent
tries to send: both plugin definitions name `python3`, and `server.py` imports
`beebot`. Whichever `python3` is first on the tool's PATH must be one that
`pip install -e .` above installed into. If it is not, point both
definitions at an interpreter that is.

## Wake timers

Every agent receives bound `timer_create`, `timer_list`, and `timer_cancel` MCP
tools. A one-shot timer uses `after="30m"`; a recurring timer uses `every="1h"`
and may include `duration="7d"`. Durations are positive integers ending in
`s`, `m`, `h`, or `d`.

Timers live in the owning agent's `record.json`. The first created timer installs
one shared executable adapter at `inputs.d/beebot-timers`. Each poll emits at
most the globally earliest due wake, addressed directly to its owner. The
schedule is removed or advanced before dispatch, so delivery is at-most-once.

### Session TTL

A role may give newly created backend sessions an idle TTL, a maximum age, or
both. The earlier deadline wins:

```toml
[session_ttl]
idle = "4h"
max_age = "24h"
```

TTL uses an ordinary visible, cancellable one-shot timer. When it fires, the old
session receives one final turn asking it to save to BeeLoop State and return a
continuation handoff. BeeBot caches that response in the agent directory and
detaches the backend session. The stable agent ID and its routes remain intact.

The next input opens a fresh session and is delivered in one ordered batch with
the cached handoff first. Cancelling the TTL timer disables expiry for that
backend session. TTL configuration is copied into the agent's `config.toml` at
creation, so later role edits do not alter an existing agent's policy.

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
/plugin install beeloop-state@beeloop-state
```

`state_store/` is git-ignored here -- it is its own repository.
