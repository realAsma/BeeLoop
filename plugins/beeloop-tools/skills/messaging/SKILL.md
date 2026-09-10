---
name: messaging
description: Create a BeeLoop agent, send or reply to a message, or route an owned input source.
---

# BeeLoop messaging

Use `get_agent_id()` when you need your own stable agent ID. Create an agent
without starting a model turn with `create_agent(role, cwd=None)`; it returns
the new agent's `agent_id`, `role`, and resolved `cwd`.

Send work with `message(receiver, msg)`. The result contains `status`,
`receiver_agent_id`, and `receiver_role`. A status of `accepted` means the
message was handed to the background dispatcher, not that the other agent has
completed a turn. Use the returned agent ID to continue messaging the exact
agent resolved from a route.

Incoming agent messages have a source such as `agent:<id>`. Reply explicitly by
calling `message(receiver="<id>", msg="...")`. A reply is a new outbound
message, so your own `allowed_receivers` policy must allow the
original sender. Do not reply merely to acknowledge receipt, and avoid reply
loops unless another turn is needed to complete the work.

Messaging, agent creation, and source routing are denied unless the caller's
snapshotted `config.toml` allows the receiver by role or exact agent ID:

```toml
[allowed_receivers]
roles = ["worker", "logger"]
ids = ["0198ff2a-0000-7000-8000-000000000000"]
```

The lists are unioned, and `"*"` in either one permits any existing recipient.
Only an explicit role or `"*"` in `roles` permits `create_agent` or creation
through a message route. Recipients do not maintain an inbound allowlist.

A receiver route contains `role` and optional `cwd` and `instance`. Omit
`instance`, pass it empty, or use the exact lowercase value `fresh` to create a
new agent through the same authorized creation path as `create_agent`. Use any
other non-empty value, including `default`, to reuse the latest restorable agent
for that route.

Use `route_source(source, receiver_agent_id)` to move a persistent input source
owned by this agent to an allowed existing agent with the same role and cwd. A
successful result identifies the source and receiver. The tool cannot create
agents or take another agent's route.
