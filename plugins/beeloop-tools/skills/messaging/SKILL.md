---
name: messaging
description: Send and handle messages among harness-bound BeeLoop agents.
---

# BeeLoop messaging

The BeeLoop `message` MCP lets you message agents in the BeeLoop harness.

## Identity

Call `get_agent_id()`; `None` means agent operations are unavailable because
they require a harness-bound caller.

## Send a message

1. Locate the receiver BeeLoop agent using its agent ID or a
   `{role, cwd, instance}` route.
2. Create the message payload. If you need a reply, include: "After completion,
   reply to the agent ID in this message's source using the `message` MCP
   tool." Worker assignments use `delegate_worker`, which supplies the callback
   instructions automatically.
3. Send the payload with the `message` MCP tool.
4. Record `receiver_agent_id`, `receiver_role`, and `receiver_cwd` from the
   result for follow-ups.

## Handle a received message

- Follow the message instructions. Reply only if explicitly asked in the
  message. Avoid acknowledgment-only loops.
