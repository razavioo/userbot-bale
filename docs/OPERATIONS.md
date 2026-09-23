# userbot-bale Operations Guide

## Userbot And MCP Operations

The userbot and MCP server are local, single-account messaging integrations. They are separate
from the relay/VPN topology below and do not expose relay provisioning, proxy control, VPN routing,
contact import, raw RPCs, or call acceptance.

- Authenticate once with `userbot-bale auth bale-login --phone ... --save --no-print-jwt`.
- Add every peer that automation may access with `userbot-bale userbot allow-peer <peer_id>`.
- Run one messaging worker per account. Do not run the userbot and MCP server as independent
  long-lived workers for the same account if reliable delivery matters; they would maintain separate
  WebSocket sessions and separate process lifecycles.
- Start MCP only through `userbot-bale mcp serve` over stdio. It has no network listener and limits
  dialog/message access and outbound text to the local allowlist.
- MCP tools return object-shaped `structuredContent` (`{messages, count}`, `{dialogs, count}`, …).
  `list_rpc_paths` is a read-only offline inventory of `/bale.*/*` paths extracted from `bale.apk`.
  `search_messages_remote` and `list_shared_media` are read-only server RPCs; both enforce the
  outbound peer allowlist (`list_shared_media` requires an allowlisted `peer_id`).
  `send_text` is two-phase: the first call only returns a `confirm_token` (TTL 300 s) and does not
  deliver; only a matching second call with that token sends.
- Outbound automation is capped at 20 messages per peer per minute. A failed network send consumes
  a slot deliberately, preventing retry loops from creating a burst.

Userbot state and audit records are stored in `state/userbot.sqlite3` under the UserbotBale app
directory. JWTs are not stored in that database.

## Architecture (post-v0.4)

No coordinator. The deployment is just **one or more relay (mesh exit-node) hosts**, each running one Python process that owns a pool of Bale JWTs. Each JWT can host one concurrent Bale call.

```
┌─────────────┐        Bale call (LiveKit DataChannel)        ┌──────────────────┐
│ Android app │ ───────────────────────────────────────────► │ Relay host (VPS) │
│  (user JWT) │   PSK-encrypted VPN frames over DC topic     │ • 8 Bale JWTs    │
│  relay list │                                              │ • TUN + MASQ     │
└─────────────┘                                              └──────────────────┘
```

The client app keeps a user-managed list of relay Bale `peer_id`s. To connect, it picks one (shuffled deterministically by the user's own peer_id for fairness), places a Bale StartCall to it, joins the LiveKit room, and runs the PSK handshake. If that fails (relay busy, network blocks the call, PSK mismatch), it falls back to the next peer_id in the list.

No coordinator means: no rendezvous account to saturate, no EXPECT_CLIENT race, no dispatch lock, no relay heartbeats over Bale. The PSK in `/etc/userbot-bale/vpn.psk` is the only auth — without it the encrypted DataChannel rejects every frame.

---

## Deployment

### Prerequisites

- A Linux VPS (Ubuntu 22.04+) reachable via SSH
- Python 3.11+, virtualenv, systemd
- One or more Bale JWT files (see [BALE_HEADLESS.md](./BALE_HEADLESS.md))
- `pyyaml` locally for `scripts/deploy.py`

### Topology manifest

[`deploy/inventory.yaml`](../deploy/inventory.yaml) lists each relay host and the JWTs it should serve. Edit it to add or remove relays.

```yaml
relays:
  - name: relay-1
    host: <server-ip>
    user: root
    region: ir
    wan_interface: eth0
    relay_jwts: [account-1.jwt, account-2.jwt, ...]
```

### Deploy

```bash
python3 scripts/deploy.py                # deploy all relays
python3 scripts/deploy.py --node relay-1 # one relay
python3 scripts/deploy.py --dry-run      # print commands only
```

### Adding a relay

1. Obtain a Bale JWT for the new relay account.
2. Copy to `/root/.userbot-bale/accounts/account-N.jwt` on the host.
3. Add the JWT filename to `inventory.yaml` under the host's `relay_jwts`.
4. Deploy: `python3 scripts/deploy.py --node <relay-name>`.

### Clients learn about the relay how?

You tell users the Bale `peer_id` of each relay account. They enter it in the app's Settings → "Relay peer IDs" field (multiple IDs separated by comma or newline). The app shuffles + falls back across them. There is no bundled relay list, no URL fetch, no coordinator describe — the user owns their relay list.

---

## Metrics

Each relay exposes Prometheus metrics on `127.0.0.1:9201`:

```bash
curl 127.0.0.1:9201/metrics
curl 127.0.0.1:9201/healthz
```

Key counters:

| Metric | Description |
|--------|-------------|
| `userbot_bale_relay_calls_total{outcome}` | Incoming Bale calls by outcome (committed, slot_busy, no_caller_identity, provision_failed, ...) |
| `userbot_bale_relay_sessions_active` | Current active VPN sessions |
| `userbot_bale_livekit_session_starts_total{outcome}` | LiveKit session starts |
| `userbot_bale_relay_in_use_per_account{account}` | Sessions per JWT slot |
| `userbot_bale_account_active_age_seconds{account}` | Age of the per-account active flag — alert if > 30 s |

---

## Logs

Set `USERBOT_BALE_LOG_FORMAT=json` in the systemd unit for structured logs. Default is `text`.

Every inbound call gets an 8-hex correlation ID (`cid=`) logged across its full lifecycle (join → resolve → provision → reap). Trace one call end-to-end with `journalctl -u userbot-bale-mesh | grep cid=<id>`.

Common patterns:

| Pattern | Meaning |
|---------|---------|
| `vpn-mesh: up` | Relay process started |
| `vpn-mesh: incoming call →` | New Bale call received, caller identity resolved |
| `vpn-mesh: caller resolved from LiveKit identity` | Real caller user_id extracted from LiveKit |
| `vpn-mesh: refusing call on account=N` | Slot busy; caller should fall back to another relay |
| `vpn-mesh: could not resolve caller identity` | Caller didn't join the LiveKit room — abandoned/spam call |
| `vpn-mesh: tun down. out=N` | Session torn down (client disconnected, or carrier dropped) |

---

## Incident triage

### "Clients connect to one relay, fail on the next, retry-loop"

Likely the busy relay is hosting an in-flight session — `refusing call on account=X` is the expected response. The client should fall back automatically; if it doesn't, check that the client app has multiple `peer_id`s in its relay list.

### "Relay accepts calls but no tunnels stay up"

1. Check `userbot_bale_account_active_age_seconds{account=N}` — if stuck > 30 s, the active flag leaked.
2. `journalctl -u userbot-bale-mesh | grep stale-recovery` — the auto-recovery logs when it force-clears.
3. If persistent, restart: `systemctl restart userbot-bale-mesh`.

### "Call drops after ~30 min"

LiveKit keepalive is wired in. Usually points to:
1. The relay or client process killed (OOM, systemd timeout).
2. Carrier connectivity interrupted.
3. Bale session token expired (re-login).

### "TUN permission denied"

```bash
ls /dev/net/tun       # must exist
sudo modprobe tun     # if missing
sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 "$USER"
```

---

## Reconnect policy (per client)

| Client | Policy |
|--------|--------|
| Android | Exponential backoff: base 3 s, max 30 s, resets after 60 s stable uptime. Within a single connect attempt the relay list is walked sequentially with ~10 s per peer before falling back. |
| macOS | Delegates to `NEPacketTunnelProvider` `.reasserting` |
| PyQt / CLI | No auto-reconnect; user re-runs |

The Android backoff constants live in `BaleVpnService.kt` (`RECONNECT_BASE_DELAY_MS`, `RECONNECT_MAX_DELAY_MS`, `RECONNECT_STABLE_RESET_MS`).

---

## Version

Single source of truth at `VERSION` in the repo root. Python (`src/userbot-bale/__init__.py`, `pyproject.toml`) and Android (`build.gradle.kts` via `rootProject.file("../../VERSION")`) both read from it.
