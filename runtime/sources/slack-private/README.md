# Private Slack input

This source connects BeeLoop to one configured Slack user through Socket Mode.
It accepts only private `message.im` events from that user and creates one
persistent orchestrator per Slack thread. Message bodies and file bytes are
fetched only after intake authentication.

`reply` and `upload` answer inside a thread this input authenticated. The
`dm-thread` helper is the one outbound path with no inbound event behind it: it
opens a new thread in the owner's DM for scheduled work the owner configured,
such as the `research-digest` input. Its destination is always
`SLACK_ALLOWED_USER_ID`.

## Slack app

Create or reuse an app with Socket Mode enabled, an app token with
`connections:write`, and these bot scopes:

- `im:history`
- `chat:write`
- `im:write`
- `files:read`
- `files:write`

Subscribe only to the `message.im` bot event, enable App Home messages, and
install the app in the target workspace.

## Setup

Install the optional dependency and run setup:

```sh
python3 -m pip install -e '.[slack]'
runtime/sources/slack-private/slack-private setup
```

Setup reads only `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, and
`SLACK_ALLOWED_USER_ID`. It writes them to the ignored `secrets.env` with mode
`0600` and verifies the workspace and owner DM.

Enable intake only after setup succeeds:

```sh
chmod +x inputs.d/slack-private
gateway/loop
```
