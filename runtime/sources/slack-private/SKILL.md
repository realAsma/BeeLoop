---
name: slack-private-input
description: Fetch and reply to authenticated owner DMs delivered through the slack-private BeeLoop source.
---

# Handle private Slack input

The complete `slack-private:` source is the authenticated reply handle. Use
this source's helper, not a generic Slack connector or the owner's identity.
Orchestrators invoke it through `run_script`; check the returned `exit_code`.

## Reply

```text
run_script(script="sources/slack-private/slack-private",
           args=["reply", "--source", "<complete source>", "--text", "-"],
           stdin="<reply>")
```

## Context and files

Use the included text, rich content, and file metadata directly. Fetch only
when earlier discussion or attachment bytes are needed:

```text
run_script(script="sources/slack-private/slack-private",
           args=["fetch", "--source", "<complete source>", "--permalink", "<url>"])
```

Add `--download-files` when needed. Downloads return absolute paths and MIME
types for native document and image tools; intake metadata is not image pixels.

```text
run_script(script="sources/slack-private/slack-private",
           args=["upload", "--source", "<complete source>", "--file", "<absolute path>"])
```

Workers can use the same helper from their shell after locating the deployment
with `beeloop root`.

A scheduled job that the owner configured, with no inbound event to reply to,
uses `dm-thread --header <short line> --text -` instead. It opens a thread only
in the configured owner's DM. Do not start conversations the owner did not ask for.

Put the result first and keep replies comfortable to read in Slack. Prefer
short paragraphs, `*bold*` emphasis, `•` bullets, and `<url|label>` links.
Briefly state blockers and the next useful action.
