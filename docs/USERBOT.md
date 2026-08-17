# Userbot And MCP

`baleobala` can run a local, single-account Bale userbot and expose a narrow
MCP interface for an AI client. Both use the saved Bale session and the same
SQLite state database. They do not expose VPN, proxy, call, contact-import, or
raw RPC operations.

Run one long-lived messaging worker per Bale account. Do not run the userbot
and MCP server as independent long-lived workers for the same account when
reliable delivery matters.

## Setup

Authenticate and store the account session in the platform secret store:

```bash
baleobala auth bale-login --phone +98912xxxxxxx --save --no-print-jwt
```

Allow each peer that may receive automated messages. A peer is a Bale numeric
user ID, not a phone number.

```bash
baleobala userbot allow-peer 123456789
baleobala userbot peers
```

## Run A Userbot

The base runtime receives and persists messages. It does not automatically
reply. The optional echo plugin replies only to peers already on the allowlist.

```bash
baleobala userbot run
baleobala userbot run --echo
```

Local message and audit state is saved at `state/userbot.sqlite3` beneath the
normal Baleobala application directory. JWTs are not stored in this database.
`list_dialogs` reads Bale's current grouped dialog endpoint and falls back to
conversations that this userbot has actually observed and persisted only when
the remote endpoint is unavailable. For an approved peer, `list_messages`
reads recent remote history while the worker is running and otherwise returns
the durable local history it has observed.

## MCP

Install the optional SDK, then serve MCP over standard input/output:

```bash
pip install -e ".[mcp]"
baleobala mcp serve
```

The server provides `account_status`, `list_messages`, `list_dialogs`, and
`send_text`. Messages and dialogs are limited to the local allowlist, and
`send_text` refuses any peer outside it. Automated sends are also capped at 20
per peer per minute. Configure your MCP host to launch `baleobala mcp serve`; do
not use a network transport until an authentication boundary is added.

## Framework API

```python
from baleobala.userbot import BaleUserClient, UserbotRuntime, UserbotStore
import threading

store = UserbotStore()
store.allow_peer(123456789)
client = BaleUserClient(jwt="loaded-from-your-secret-store", store=store)

def handle(event):
    if event.text == "/status":
        client.send_text(event.peer_id, "online")

client.on_message(handle)
runtime = UserbotRuntime(client)
runtime.start()
try:
    threading.Event().wait()
finally:
    runtime.stop()
```

Custom handlers should use `MessageEvent` and `BaleUserClient`; they should not
reach into the WebSocket or protobuf modules. Keep automation confined to
accounts and peers for which the account owner has permission.
