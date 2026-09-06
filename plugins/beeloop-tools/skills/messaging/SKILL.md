---
name: messaging
description: Send an asynchronous message to another BeeLoop agent or reply to one.
---

# BeeLoop messaging

Use `get_agent_id()` when you need your own stable agent ID. Send work with
`message(receiver, msg)`. The result contains `status`, `receiver_agent_id`, and
`receiver_role`. A status of `accepted` means the message was handed to the
background dispatcher, not that the other agent has completed a turn. Use the
returned agent ID to continue messaging the exact agent resolved from a route.

Incoming agent messages have a source such as `agent:<id>`. Reply explicitly by
calling `message(receiver="<id>", msg="...")`. A reply is a new outbound
message, so your own `messaging.allowed_recipients` policy must allow the
original sender. Do not reply merely to acknowledge receipt, and avoid reply
loops unless another turn is needed to complete the work.

Messaging is denied unless the sender's snapshotted `config.toml` allows the
recipient by role or exact agent ID:

```toml
[messaging.allowed_recipients]
roles = ["worker", "logger"]
ids = ["0198ff2a-0000-7000-8000-000000000000"]
```

The lists are unioned, and `"*"` in either one permits any existing recipient.
Only an explicit role or `"*"` in `roles` permits creating a new role route.
Recipients do not maintain an inbound allowlist.

A receiver route contains `role` and optional `cwd` and `instance`. Omit
`instance`, pass it empty, or use the exact lowercase value `fresh` to create a
new agent. Use any other non-empty value, including `default`, to reuse the
latest restorable agent for that route.
