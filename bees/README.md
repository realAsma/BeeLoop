# Bees

Each bee is an executable script under `bees/`.

Input adapters dispatch to a bee by emitting a text body on stdout that starts
with the target bee name. For example, a short Slack notification for BeeBot:

```text
bee=beebot

Slack mention from the owner; see thread https://example.com/thread
```

Bee scripts read a request body from stdin. A request body starts with
`key=value` headers, followed by a blank line and prompt text:

```text
key=value
other_key=other_value

prompt text
```

Each bee supports `-h`/`--help` and documents its own arguments and output
behavior in `usage()`. Malformed or unknown `key=value` arguments fail clearly.

`args.sh` provides the shared minimal parser. Each bee still defines its own
`usage()` and `handle_arg()`.
