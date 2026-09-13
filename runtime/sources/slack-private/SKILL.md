---
name: slack-private-input
description: Fetch and reply to authenticated owner DMs delivered through the slack-private BeeLoop source.
---

# Handle private Slack input

The complete `slack-private:` source is the authenticated reply handle. Use the
runtime helper for every operation; do not send responses through a generic
Slack connector or the owner's Slack identity.

For an inbound event:

1. Copy the complete input source and the Slack permalink from the message.
2. Fetch authenticated context with `$BEEBOT_ROOT/runtime/sources/slack-private/slack-private
   fetch --source <source> --permalink <url>`. Add `--download-files` only when
   the task needs attached files.
3. Reply with `$BEEBOT_ROOT/runtime/sources/slack-private/slack-private reply
   --source <source> --text -`. Use `upload --source <source> --file <path>` for
   files.

Put the result first and keep replies comfortable to read in Slack. Prefer
short paragraphs, `*bold*` emphasis, `•` bullets, and `<url|label>` links.
Briefly state blockers and the next useful action. Use `dm` or `dm-thread` only
for proactive messages to the configured owner.
