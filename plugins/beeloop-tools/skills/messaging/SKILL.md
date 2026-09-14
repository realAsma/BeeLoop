---
name: messaging
description: Send and handle BeeLoop messages from bound agents or direct sessions.
---

# BeeLoop messaging

The BeeLoop `message` MCP lets you message agents in the BeeLoop harness.

## Identity

Call `get_agent_id()`:

- If the ID is not `None`, you are a BeeLoop agent.
- If the ID is `None`, you are an unbound agent.

## Send a message

1. Locate the receiver BeeLoop agent using its agent ID or a
   `{role, cwd, instance}` route.
2. Create the message payload:
   - As a BeeLoop agent, if you need a reply, include: "After completion,
     reply to the agent ID in this message's source using the `message` MCP
     tool."
   - As an unbound agent, if you need a reply, include: `I am
     <short-sender-descriptor>. After completion, write your reply to
     agent_art/messages/<short-sender-descriptor> in your current working
     directory.`
3. Send the payload with the `message` MCP tool.
4. Record `receiver_agent_id`, `receiver_role`, and `receiver_cwd` from the
   result for follow-ups.

## Handle a received message

- Follow the message instructions. Reply only if explicitly asked in the
  message. Avoid acknowledgment-only loops.
