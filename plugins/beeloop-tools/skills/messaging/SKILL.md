---
name: messaging
description: Send and handle messages between BeeLoop agents.
---

# BeeLoop messaging

Use the `message` MCP tool with an agent ID or receiver route.

Messaging results contain `status`, `receiver_agent_id`, and `receiver_role`.
Use the returned agent ID to continue messaging the exact agent resolved from a
route.

Incoming agent messages have a source such as `agent:<id>`. Reply explicitly by
calling `message(receiver="<id>", msg="...")`.
Do not send acknowledgment-only replies; avoid reply loops unless another turn is needed to complete the work.
