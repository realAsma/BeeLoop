# Slack Private Input And BeeBot Runtime Spec

BeeBot needs a secure way to receive messages from and send messages to its
configured user.

This requires an always-on private Slack listener that receives 1:1 DMs from the
configured user, authenticates them, and notifies BeeBot. BeeBot can then fetch
the authenticated message and any files/images, perform the task described in
the message, and reply in Slack with text or files/images. BeeBot may also
occasionally start a DM to the configured user, such as a weekly email summary
or another user-facing reminder.

The interface must:

- Listen through Slack Socket Mode, with no public HTTP endpoint or slash
  command.
- Authenticate that each accepted Slack event is a `message.im` DM from
  `SLACK_ALLOWED_USER_ID`.
- After authenticating the Slack DM, pass BeeBot a small notification containing
  the accepted Slack permalink, not raw message bodies or file bytes.
- Drop every unauthenticated or untrusted event, so BeeBot sees only
  authenticated messages.
- Let BeeBot fetch message context, attachments, and files only after the DM has
  been authenticated.
- Reply only to the original user DM or thread, and send proactive BeeBot DMs
  only to `SLACK_ALLOWED_USER_ID`.

User-only inbound routing and outbound target checks are mandatory because
BeeBot is a trusted full-access agent; unauthenticated prompts could attempt
prompt injection or other unsafe actions.

## Implementation Design

The implementation is split between a small Slack input under `inputs.d/`,
BeeBot-side helpers for deterministic Slack operations, and skills for dynamic
BeeBot workflows. Do not change `AGENTS.md` or `CLAUDE.md` for this feature;
extend BeeBot behavior through skills when workflow guidance is needed.

### Slack Input

The Slack input listens to Slack, authenticates Slack events as user DMs,
records accepted permalinks and file metadata, and wakes BeeBot.

- Listen with Slack Socket Mode.
- Accept only events where:
  - Socket Mode request type is `events_api`.
  - The callback has a stable `event_id`.
  - The inner event is `type == "message"` and `channel_type == "im"`.
  - `event.user == SLACK_ALLOWED_USER_ID`.
  - The event team matches the bot token's workspace from `auth.test`.
  - The sender is not the bot user from `auth.test`, to avoid self-reply loops.
  - The subtype is absent or is a file-sharing subtype carrying user content.
    This keeps normal user messages and user-sent files/images, while dropping
    Slack system message variants.
  - The event has text or supported files.
- Drop non-DMs, other users, bot/self messages, edits, deletes, joins/leaves,
  reactions, typing, already-queued Slack retries, and unsupported empty events.
- Queue accepted records with permalink, `channel`, `ts`, `thread_ts`,
  `event_ts`, `event_id`, and file metadata.
- Print one BeeBot notification when accepted work is ready; print nothing when
  idle or blocked.

Authentication is part of intake, not BeeBot task handling. If the Slack input
cannot prove the message came from `SLACK_ALLOWED_USER_ID`, it must fail closed
and emit no BeeBot notification.

The BeeBot notification stays small and includes a delivery reminder for owner-DM
responses:

```text
bee=beebot

Private Slack DM from your user.
Message: <Slack message permalink>

Delivery: Send any Slack reply, status update, completion note, or link-only
response to this DM through the authenticated private-DM delivery path.
```

The Slack input only authenticates, records metadata, and notifies BeeBot; it
does not fetch threads, download files, or run task logic.

### BeeBot-Side Slack Support

BeeBot uses Slack-side helpers for deterministic Slack work: retrieving inbound
messages, sending replies, uploading files/images, and starting proactive DMs.
Dynamic BeeBot workflows, such as reply style or multi-step Slack-specific task
handling, should be expressed as skills instead of edits to `AGENTS.md` or
`CLAUDE.md`. Helpers and skills may be created or reused during setup, during an
inbound BeeBot run, or during a BeeBot-initiated workflow.

- Helpers should be scripts or other deterministic local runtime support.
- Skills should carry dynamic workflow guidance and user-facing reply style.
- For inbound DMs, retrieve the message, thread, and attachments from the
  permalink in the BeeBot notification.
- Download inbound files only through BeeBot-side retrieval.
- Store downloaded files through the BeeBot retrieval helper in event-scoped
  directories with sanitized filenames.
- Return local paths plus Slack file metadata for downloaded files.
- Reply to the original DM or thread.
- Send BeeBot-initiated proactive DMs, including reminders, only to
  `SLACK_ALLOWED_USER_ID`.
- Upload outbound files from local paths only; validate the path exists first.
- Send local files/images to the user DM with a short caption.
- Include meaningful captions, alt text, or descriptive filenames for images.
- Use runtime private Slack response support for outbound text and captions.

### Reply Style

BeeBot replies should be concise and Slack-native. Use a Slack reply style skill
or helper to format user-facing messages consistently.

- Prefer short answers with the result first.
- Use Slack formatting where it improves readability: `*bold*` for emphasis,
  `•` bullets instead of plain `-` bullets, `<url|label>` hyperlinks, and
  emojis when they help convey status or tone.
- Keep captions for files/images brief and useful.
- Avoid long markdown-heavy responses that read like terminal output.

### Runtime Rules

- Missing credentials, invalid credentials, or missing `slack_sdk` fail closed
  at runtime: emit no BeeBot notification and direct the user to first-run
  setup.
- Use short file locks for queue/state reads and writes; never hold locks while
  calling Slack, downloading files, invoking BeeBot, or running tests.

## Setup Instructions

Before first-run setup, ask the user: "Have you set up the Slack app for the
target workspace?"

If not, guide the user through Slack app creation before continuing.

### Slack App Setup

1. Create a Slack app for the target workspace.
2. Enable Socket Mode.
3. Create an app-level token with `connections:write`; this becomes
   `SLACK_APP_TOKEN`.
4. Add only these bot scopes: `im:history`, `chat:write`, `im:write`,
   `files:read`, and `files:write`.
5. Enable Event Subscriptions and subscribe only to the `message.im` bot event.
6. Enable App Home messages so the user can DM the app.
7. Install the app to the workspace; reinstall after any scope change.
8. Copy the bot `xoxb-` token; this becomes `SLACK_BOT_TOKEN`.
9. Identify the user's Slack user ID; this becomes `SLACK_ALLOWED_USER_ID`.

Do not configure `SLACK_SIGNING_SECRET`, user tokens, `chat:write.customize`,
custom sender identity, public/private channel scopes, app mention scopes,
multi-person DM scopes, or reaction scopes unless the runtime actually needs
them.

### Required Inputs

- `SLACK_APP_TOKEN`: app-level `xapp-` token with `connections:write`.
- `SLACK_BOT_TOKEN`: bot `xoxb-` token for Web API calls.
- `SLACK_ALLOWED_USER_ID`: the only user BeeBot may read from or DM.

If setup or verification cannot proceed because the Slack app or any of the
three required values are missing, prompt the user and wait for them to finish
setup and provide the missing values.

### First-Run Setup

Run first-run setup after the Slack app exists and before enabling Slack intake.
Do not stage, commit, or otherwise change git state after implementing Slack
runtime files; treat the Slack runtime as untracked for now.
Setup must:

- If required environment values are missing in non-interactive setup, report
  the exact missing values and stop.
- Write user-provided values to `secrets.sh`, preserve existing non-empty
  values, and keep `secrets.sh` readable only by the local account when
  possible. Never log Slack tokens.
- Install `slack_sdk` into the runtime Python environment when missing; for
  example, `python -m pip install slack_sdk`.
- Verify setup by loading secrets, importing `slack_sdk`, calling `auth.test`,
  confirming bot/team identity, and confirming the allowed user DM can be
  opened or addressed.
- At the end, send a short proactive hi DM to the allowed user. The message
  should ask the user to send a Slack DM back to test the path end to end, and
  should state that `beebot_loop` must be running for that test.

## Testing And Verification

Verify the interface in this order:

1. Confirm required environment values load from the runtime environment or
   `secrets.sh`.
2. Confirm `slack_sdk` imports successfully.
3. Call `auth.test` with `SLACK_BOT_TOKEN` and confirm the expected bot, team,
   and bot user identity.
4. Confirm BeeBot can open or address the allowed user DM.
5. Confirm the first-run proactive hi DM is delivered only to
   `SLACK_ALLOWED_USER_ID`.
6. With `beebot_loop` running, send a DM from the allowed user and confirm the
   Slack input emits exactly one small `bee=beebot` notification containing the
   permalink.
7. Confirm BeeBot retrieves the message/thread context after the authenticated
   notification and replies in the original DM or thread.
8. Send a DM from another Slack user and confirm it is ignored with no queue
   record, no BeeBot notification, and no reply.
9. Send unsupported Slack events such as edits, deletes, reactions, typing,
   joins/leaves, or empty events and confirm they are dropped.
10. Test file sharing from the user DM and confirm the input preserves file
    metadata only, while BeeBot downloads file bytes later into an event-scoped
    directory with sanitized filenames.
