---
name: messaging
description: Send an asynchronous message to another BeeLoop agent or reply to one.
---

# BeeLoop messaging

Use `get_agent_id()` when you need your own stable agent ID. Send work with
`message(receiver, msg)`. The call returning `accepted` means the message was
handed to the background dispatcher, not that the other agent has completed a
turn.

Incoming agent messages have a source such as `agent:<id>`. Reply explicitly by
calling `message(receiver="<id>", msg="...")`; replies require permissions in
both directions. Do not reply merely to acknowledge receipt, and avoid reply
loops unless another turn is needed to complete the work.
