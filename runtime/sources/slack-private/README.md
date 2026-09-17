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

## Setup

1. Install the optional dependency:

```sh
python3 -m pip install -e '.[slack]'
```

2. [Create a Slack app](https://docs.slack.dev/app-management/quickstart-app-settings/)
   in the workspace BeeLoop will use, then configure it:

   - Enable [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
     and create an app-level token with the `connections:write` scope.
   - Add the `im:history`, `chat:write`, `im:write`, `files:read`, and
     `files:write` bot token scopes.
   - Subscribe to the `message.im` bot event.
   - Enable the Messages Tab under App Home.
   - Install the app into the workspace.

3. Create `runtime/sources/slack-private/secrets.env`:

```dotenv
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_ALLOWED_USER_ID=U...
```

Use the app-level token, the installed app's `xoxb-` bot token, and your
[Slack member ID](https://slack.com/help/articles/360003827751-Create-a-link-to-a-members-profile).
Then protect the file so only its owner can read or write it:

```sh
chmod 600 runtime/sources/slack-private/secrets.env
```

4. Verify the configuration and enable Slack intake:

```sh
runtime/sources/slack-private/slack-private setup
```

Slack-private reads credentials only from this ignored `secrets.env` file.
Values exported in the process environment are not used.
Setup enables the input only after all checks succeed. The running BeeLoop
gateway discovers it on its next poll.

If any step fails, tell your agent what happened and ask it to fix the setup. :)
