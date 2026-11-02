# Private Slack input

This Socket Mode source accepts only the configured user's private `message.im`
events. Authenticated intake includes text, links, rich blocks, attachments, and
file/image metadata, including file-only and image-only messages. Ordinary
messages need no extra fetch; the helper retrieves fuller thread context and
file bytes as needed, returning absolute paths for native document and image
tools. Initial metadata does not inline images. BeeBot adds a best-effort
`:blob-bee:` reaction to accepted messages that start a thread, not follow-ups.
It marks receipt, not completion.

`reply` and `upload` answer threads authenticated by this input. Only `dm-thread`
sends without an inbound event: it opens an owner DM thread for owner-configured
scheduled work (such as `research-digest`), always targeting `SLACK_ALLOWED_USER_ID`.

## Setup

1. Install BeeLoop and its Slack dependency in the Python environment that
   runs your BeeLoop and BeeLoop State MCP servers:

```sh
python3 -m pip install -e '.[slack]'
```

2. [Create a Slack app](https://docs.slack.dev/app-management/quickstart-app-settings/)
   in the workspace BeeLoop will use, then configure it:

   - Enable [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
     and create an app-level token with the `connections:write` scope.
   - Add the `im:history`, `chat:write`, `im:write`, `files:read`, `files:write`,
     and `reactions:write` bot token scopes.
   - Subscribe to the `message.im` bot event.
   - Enable the Messages Tab under App Home.
   - Install the app into the workspace; reinstall after changing scopes.

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
