# Userbot And MCP

`userbot-bale` can run a local, single-account Bale userbot and expose a narrow
MCP interface for an AI client. Both use the saved Bale session and the same
SQLite state database. They do not expose VPN, proxy, call, contact-import, or
raw RPC operations.

Run one long-lived messaging worker per Bale account. Do not run the userbot
and MCP server as independent long-lived workers for the same account when
reliable delivery matters.

## Setup

Authenticate and store the account session in the platform secret store:

```bash
userbot-bale auth bale-login --phone +98912xxxxxxx --save --no-print-jwt
```

Allow each peer that may receive automated messages. A peer is a Bale numeric
user ID, not a phone number.

```bash
userbot-bale userbot allow-peer 123456789
userbot-bale userbot peers
userbot-bale userbot status
```

You can also send messages or inspect conversations directly from the CLI:

```bash
# Send a message to an allowed peer
userbot-bale userbot send 123456789 "Hello from userbot"

# List recent conversations
userbot-bale userbot dialogs --limit 20

# View recent messages with a peer
userbot-bale userbot messages 123456789 --limit 20

# Server-side text search via bale.search.v1.Search
userbot-bale userbot search-remote "invoice" --limit 10

# Shared media for one allowlisted peer (SharedMedia LoadMedia)
userbot-bale userbot shared-media 123456789 --limit 10

# Known Bale /bale.* gRPC paths from the offline APK inventory
userbot-bale userbot rpc-paths --service messaging --query LoadHistory
```

## Run A Userbot

The base runtime receives and persists messages. It does not automatically
reply. The optional echo plugin replies only to peers already on the allowlist.

```bash
userbot-bale userbot run
userbot-bale userbot run --echo
```

Local message and audit state is saved at `state/userbot.sqlite3` beneath the
normal application directory. JWTs are not stored in this database.
`list_dialogs` reads Bale's current grouped dialog endpoint and falls back to
conversations that this userbot has actually observed and persisted only when
the remote endpoint is unavailable. For an approved peer, `list_messages`
reads recent remote history while the worker is running and otherwise returns
the durable local history it has observed.

## MCP

Install the optional SDK, then serve MCP over standard input/output:

```bash
pip install -e ".[mcp]"
userbot-bale mcp serve
```

The server exposes these tools. Every tool returns a JSON **object**
(`structuredContent` type `object`), never a bare list:

| Tool | Returns |
| --- | --- |
| `account_status` | `{state, user_id, expires_in, allowed_peers}` |
| `list_dialogs(limit)` | `{dialogs, count}` (allowlisted only; `limit` 1–100) |
| `list_messages(peer_id, limit)` | `{messages, count}` |
| `search_messages(query, peer_id?, limit)` | `{messages, count}` (local store; allowlisted only) |
| `search_messages_remote(query, peer_id?, limit)` | `{messages, count, query}` (server-side SearchMessages; allowlist applied) |
| `search_contacts(query)` | `{contacts, count}` |
| `list_shared_media(peer_id, limit?, content_type?)` | `{media, count, peer_id}` (SharedMedia LoadMedia; allowlisted peer only) |
| `resolve_phone(phone)` | `{phone, user_id, is_allowed}` |
| `list_rpc_paths(service?, query?, limit)` | `{paths, count, total, service, query}` (offline APK inventory; read-only) |
| `mark_read(peer_id, date)` | `{ok, peer_id, date}` |

### `send_text` two-phase confirm

Outbound text is **not** sent on the first call. Messages and dialogs are
limited to the local allowlist, and `send_text` refuses any peer outside it.
Automated sends are also capped at 20 per peer per minute.

1. Call `send_text(peer_id, text)` **without** `confirm_token`.
   Response (nothing is delivered):

   ```json
   {
     "ok": false,
     "needs_confirm": true,
     "confirm_token": "…",
     "peer_id": 123456789,
     "preview": {"peer_id": 123456789, "text": "…"},
     "expires_in": 300
   }
   ```

2. Call again with the **same** `peer_id` and `text` plus `confirm_token`.
   On success: `{"ok": true, "peer_id": …, "confirmed": true}`.
   An unknown/expired/mismatched token raises `PermissionError`; request a
   fresh token by calling without `confirm_token` again.

Configure your MCP host to launch `userbot-bale mcp serve`; do
not use a network transport until an authentication boundary is added.
Hosts that expect the older list-shaped payloads must reconnect after
upgrading — tool results are now normalized objects.

## Modern Standard API (Async & Event-Driven)

```python
from userbot_bale import BaleClient, events, filters

# Connect to Bale
client = BaleClient()  # Automatically loads saved session from keychain

@client.on(filters.command("start"))
async def start_handler(event):
    await event.reply("Hello! Bale userbot is active.")

@client.on(filters.regex(r"^price\s+(.*)"))
async def price_handler(event):
    item = event.pattern_match.group(1)
    await event.reply(f"Price lookup for: {item}")

# Run bot
client.run()
```

## Legacy Synchronous Framework API

```python
from userbot_bale.userbot import BaleUserClient, UserbotRuntime, UserbotStore
import threading

store = UserbotStore()
store.allow_peer(123456789)
client = BaleUserClient(jwt="loaded-from-your-secret-store", store=store)

def handle(event):
    if event.text == "/status":
        event.reply("online")

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
