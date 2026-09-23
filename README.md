<p align="center">
  <img src="docs/assets/icon.png" alt="userbot-bale logo" width="140" height="140" />
</p>

<h1 align="center">userbot-bale</h1>

<p align="center">
  <strong>Comprehensive Bale Messenger Framework: Automation Userbot, Stdio MCP Server, and Tunnel Core</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg" alt="Platforms" />
  <img src="https://img.shields.io/badge/mcp-compatible-purple.svg" alt="MCP Compatible" />
</p>

---

## ⚠️ Important Legal & Ethical Disclaimer

> **PLEASE READ CAREFULLY BEFORE USING OR CONTRIBUTING TO THIS SOFTWARE.**

- **Educational & Personal Use Only:** `userbot-bale` is developed strictly for educational, security research, and personal automation experiments. It is designed to explore distributed communication paradigms, protocol implementations, and AI tool integrations.
- **No Affiliation:** This repository and its contributors are neither affiliated with, endorsed by, nor associated in any official manner with **Bale Messenger** (`bale.ai`), its parent entities, or any of its subsidiaries.
- **User Responsibility & Compliance:** Any automated interaction with communication platforms is subject to their respective Terms of Service, Acceptable Use Policies, and applicable local/international laws. **The end-user assumes total, sole responsibility and liability** for their actions, usage, compliance, and any consequences arising therefrom—including, but not limited to, account termination, rate limits, service suspension, or legal proceedings.
- **No Warranty & Limitation of Liability:** This software is distributed on an **"AS IS"** and **"AS AVAILABLE"** basis without warranties of any kind, express or implied. In no event shall the authors, maintainers, or copyright holders be liable for any direct, indirect, incidental, special, consequential, or punitive damages resulting from the use or inability to use this software.
- **Responsible Usage:** Never use this software for unauthorized data collection, spamming, harassment, denial of service, traffic manipulation, or any malicious purposes.

---

## 🌟 Key Capabilities

### 1. Robust Userbot Engine
- **Durable Local Storage:** SQLite-backed persistence (`state/userbot.sqlite3`) ensuring message deduplication, inbound/outbound event history, and audit logging.
- **Command & Regex Routing (`CommandDispatcher`):** Declarative decorators (`@dispatcher.command`, `@dispatcher.regex`, `@dispatcher.default`) for effortless creation of responsive chat bots and automated handlers.
- **Allowlist & Rate-Limiting Policy:** Hardened outbound controls enforcing an allowlist of approved numeric peer IDs and strict rate-limiting (maximum 20 outbound messages per minute per peer) to prevent spam or runaway loops.
- **Full Dialogue Synchronization:** Automatic retrieval and parsing of grouped conversations, server-side read status marking (`mark_read`), and access-hash caching for groups and channels.

### 2. Model Context Protocol (MCP) Server
- **AI Agent Integration:** Exposes standard Model Context Protocol tools over `stdio`, ready for immediate connection with **Claude Desktop**, **Cursor**, or custom AI agents.
- **Scoped Tool Boundary:** Every tool returns a single JSON object (`structuredContent` type `object`):
  - `account_status`: Inspect connection state, authenticated user ID, token expiration, and allowlisted peers.
  - `list_dialogs`: Retrieve recent server conversations filtered against the allowed peer set → `{dialogs, count}`.
  - `list_messages`: Read verified server/local chat history with an approved peer → `{messages, count}`.
  - `search_messages`: Search local database history by keyword across allowed chats → `{messages, count}`.
  - `search_contacts` & `resolve_phone`: Resolve names or phone numbers to numeric IDs → `{contacts, count}` / `{phone, user_id, is_allowed}`.
  - `list_rpc_paths`: Discover known Bale `/bale.*/*` gRPC paths from the offline APK inventory → `{paths, count, total, service, query}`.
  - `send_text`: Two-phase outbound send to an allowlisted peer. First call returns `{needs_confirm, confirm_token, preview, expires_in}` without sending; second call with the same `peer_id`/`text` and `confirm_token` delivers `{ok, peer_id, confirmed}` (token TTL 5 minutes).
  - `mark_read`: Acknowledge and mark messages as read up to a specific timestamp → `{ok, peer_id, date}`.

### 3. Comprehensive CLI Interface
- Fully unified command line interface (`userbot-bale userbot ...`) for terminal-based usage, administrative management, manual messaging, contact lookup, and status inspection without background daemons.

### 4. Resilient Network & VPN Transport Core
- Encapsulated SOCKS5 proxy and full-system VPN tunneling routed over Bale VoIP calls.
- Pluggable bearer channels: WebRTC DataChannels, acoustic GGWave audio encoding, and RPC message fallback.
- Native system integrations across Linux (`tun`), macOS (`PacketTunnel` & LaunchAgent), and Windows.

---

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/razavioo/userbot-bale.git
cd userbot-bale

# Set up Python virtual environment (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate

# Install userbot, bale core, and MCP dependencies
pip install -e ".[bale,mcp,dev]"
```

### 1. Authenticate

Authenticate your account and securely save the session credentials:

```bash
# Option A: Browser-based interactive login (recommended)
userbot-bale auth bale-login --phone +98912xxxxxxx --save --no-print-jwt

# Option B: Import an existing JWT
userbot-bale auth login --jwt-file /path/to/server.jwt

# Check authentication health and expiry
userbot-bale auth status
```

### 2. Configure Peer Allowlist

To protect accounts against unintended automated sends, peers must be explicitly authorized by their numeric Bale user ID:

```bash
# Allow a specific peer
userbot-bale userbot allow-peer 123456789

# List all allowed peers
userbot-bale userbot peers

# Remove a peer from the allowlist
userbot-bale userbot disallow-peer 123456789
```

### 3. CLI Operations

Interact with Bale directly from your shell:

```bash
# Display authenticated identity
userbot-bale userbot whoami

# Check userbot engine & allowlist status
userbot-bale userbot status

# Send a message to an authorized peer
userbot-bale userbot send 123456789 "Hello from userbot CLI!"

# List recent active conversations
userbot-bale userbot dialogs --limit 10

# Read conversation history with a peer
userbot-bale userbot messages 123456789 --limit 20

# Search message history locally
userbot-bale userbot search "order" --peer-id 123456789

# Mark conversation as read up to a timestamp
userbot-bale userbot mark-read 123456789 1789370000000

# Look up contacts or resolve phone numbers
userbot-bale userbot search-contacts "Support"
userbot-bale userbot resolve-phone "+98912xxxxxxx"
```

### 4. Running the Userbot Worker

Run a continuous receiver that persists messages and can optionally echo responses:

```bash
# Run listener & persistent message logger
userbot-bale userbot run

# Run with built-in Echo plugin (responds only to allowed peers)
userbot-bale userbot run --echo
```

### 5. Serving MCP for AI Clients

Configure your MCP-compliant client (e.g. Claude Desktop configuration `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "userbot-bale": {
      "command": "/path/to/userbot-bale/.venv/bin/userbot-bale",
      "args": ["mcp", "serve"]
    }
  }
}
```

Or run manually in stdio mode:

```bash
userbot-bale mcp serve
```

---

## 💻 Python Developer API

### 1. Modern Async API (Standard Telethon / Pyrogram Style)

The recommended standard way to build userbots and integrate Bale into other projects:

```python
from userbot_bale import BaleClient, events, filters

# Initialize client (uses saved credentials by default, or provide jwt="...")
client = BaleClient(jwt="YOUR_JWT_HERE")

@client.on(filters.command("start"))
async def handle_start(event):
    await event.reply("Hello from Bale userbot!")

@client.on(filters.command("ping"))
async def handle_ping(event):
    await event.reply("pong!")

@client.on(filters.regex(r"^order #?(\d+)"))
async def handle_order(event):
    order_id = event.pattern_match.group(1)
    await event.reply(f"Looking up order {order_id}...")

@client.on(filters.text & filters.private)
async def handle_private(event):
    await event.mark_read()

# Run the userbot until interrupted
client.run()
```

Or run inside an existing `asyncio` loop:

```python
async with client:
    await client.send_message(peer_id=123456789, text="System notification")
    messages = await client.get_messages(peer_id=123456789, limit=10)
```

### 2. Synchronous & Classic Plugin API

For lightweight scripts or existing synchronous workflows:

```python
import threading
from userbot_bale import BaleUserClient, filters

client = BaleUserClient(jwt="YOUR_JWT_HERE", enforce_allowlist=False)

@client.on_message(filters.command("ping"))
def ping(event):
    event.reply("pong!")

client.start()
try:
    threading.Event().wait()
finally:
    client.stop()
```

Also supports `CommandDispatcher` and `UserbotRuntime` for legacy code:

```python
from userbot_bale.userbot import BaleUserClient, CommandDispatcher, UserbotRuntime, UserbotStore

store = UserbotStore()
store.allow_peer(123456789)
client = BaleUserClient(jwt="YOUR_JWT_HERE", store=store)

dispatcher = CommandDispatcher(prefix="/")

@dispatcher.command("ping")
def handle_ping(event, bot, args):
    bot.send_text(event.peer_id, "pong!")

runtime = UserbotRuntime(client, plugins=[dispatcher])
runtime.start()
```

---

## 🔒 Security & Privacy Practices

1. **Zero Secret Persistence in Database:** Userbot SQLite state files (`userbot.sqlite3`) store message metadata and text for synchronization, but **never** store account JWTs or private cryptographic keys.
2. **Platform Keychains:** Authentication records are managed via platform-native secret stores (macOS Keychain, Linux SecretService, Windows Credential Manager) or user-controlled permission files (`600`).
3. **Outbound Gating:** All programmatic sends are restricted to the local allowlist. Unverified peers receive no automated replies unless explicitly added by the owner.
4. **Rate Limiting:** Embedded sliding-window counters protect accounts from accidental network saturation and threshold bans.

---

## 🧪 Testing

Run the automated test suite:

```bash
# Run all unit and mock tests
pytest tests/

# Run specific userbot & protocol tests
pytest tests/test_userbot.py tests/test_cli_surface.py tests/test_new_protos.py
```

---

## 📖 Documentation & Languages

- **Persian Documentation:** [docs/PERSIAN_GUIDE.md](docs/PERSIAN_GUIDE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Userbot and MCP](docs/USERBOT.md)
- [Setup Guide](docs/SETUP.md)
- [Operations & Deploy](docs/OPERATIONS.md)

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
