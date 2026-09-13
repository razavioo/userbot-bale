<p align="center">
  <img src="docs/assets/icon.png" alt="userbot-bale" width="160" height="160" />
</p>

# userbot-bale

`userbot-bale` is a comprehensive Bale integration framework and userbot runtime. It provides a durable single-account messaging userbot, an allowlist-gated Model Context Protocol (MCP) server for local AI integrations, and a call-backed VPN and proxy transport. The userbot and MCP features use Bale's authenticated WebSocket API; the VPN/proxy path uses a Bale LiveKit call.

---

## Features

- **Durable Userbot:** Long-lived messaging automation with SQLite persistence, dialog synchronization, and inbound/outbound rate limiting.
- **Model Context Protocol (MCP):** Allowlist-controlled MCP server (`account_status`, `list_messages`, `list_dialogs`, `send_text`) for Claude, Cursor, and other AI agents.
- **Call-Backed VPN & Proxy:** SOCKS5 proxy and system VPN tunneling encapsulated over Bale voice/video calls.
- **Cross-Platform:** Runs on Linux, macOS, and Windows.

---

## Quick Start (Userbot & MCP)

### 1. Authenticate

Authenticate and save your Bale session into the platform secret store:

```bash
userbot-bale auth bale-login --phone +98912xxxxxxx --save --no-print-jwt
userbot-bale auth status
```

### 2. Allow Peers

Allow numeric Bale user IDs to interact with automation:

```bash
userbot-bale userbot allow-peer 123456789
userbot-bale userbot peers
```

### 3. Run Userbot

```bash
# Run durable receiver & message logger
userbot-bale userbot run

# Run with auto-echo plugin (replies only to allowed peers)
userbot-bale userbot run --echo
```

### 4. Serve MCP for AI Clients

```bash
pip install -e ".[mcp]"
userbot-bale mcp serve
```

---

## Quick Connect (macOS Proxy)

> **Prerequisite:** The relay service on the VPS must be running (instructions below).

### Connect

```bash
bash scripts/run-proxy-client.sh
```

Or if the LaunchAgent is installed (it starts automatically after login):

```bash
launchctl start ai.userbot_bale.proxy-client
```

When the proxy is ready, you will see these messages in the log:

```
call_established
transport_selected=dc
proxy_listening=127.0.0.1:1080
system_proxy_active=127.0.0.1:1080
```

The system's Wi-Fi SOCKS proxy will be enabled automatically.

### Test

```bash
# Should return the VPS server IP (68.183.118.171)
curl --socks5-hostname 127.0.0.1:1080 https://ifconfig.me
```

### Disconnect

```bash
launchctl stop ai.userbot_bale.proxy-client
```

Or if running in the foreground: `Ctrl-C`

Both methods immediately restore the system's Wi-Fi proxy settings (SIGTERM → Python cleanup).

### Status

```bash
bash scripts/install-launchagent-client.sh --status
bash scripts/install-launchagent-client.sh --logs
```

---

## Architecture

```
Mac (Account B)                    VPS / Ubuntu (Account A)
────────────────────               ────────────────────────
bale-proxy system                  bale-proxy relay
  │  --peer-id 1519372475            │  --answer
  │  --transport dc                  │  --transport dc
  │                                  │
  └──── Bale LiveKit call ───────────┘
         (DataChannel / WebRTC)

  SOCKS5 :1080  <──── tunnel ────>  TCP to internet
  Wi-Fi proxy ON                    exit node = VPS IP
```

- **Account A** (relay): Runs on the VPS, answers incoming calls, and forwards TCP traffic to the internet.
- **Account B** (client): Runs on the Mac, calls Account A, opens a SOCKS5 server on `127.0.0.1:1080`, and enables the macOS Wi-Fi proxy.
- **Transport**: WebRTC DataChannel (`--transport dc`) with PSK encryption (`--proxy-secret-file`).

---

## Installation and Setup

### Prerequisites

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,bale,desktop]"
```

### Install LaunchAgent (Auto-start on login)

```bash
USERBOT_BALE_JWT_FILE=~/.bale_jwt_b \
USERBOT_BALE_PSK_FILE=~/.userbot-bale/userbot-bale-vpn.psk \
USERBOT_BALE_RELAY_PEER_ID=1519372475 \
bash scripts/install-launchagent-client.sh --install
```

Important Variables:

| Variable | Default | Description |
|---|---|---|
| `USERBOT_BALE_JWT_FILE` | `~/.bale_jwt_b` | Client account (Mac) JWT |
| `USERBOT_BALE_PSK_FILE` | `~/.userbot-bale/userbot-bale-vpn.psk` | Encryption pre-shared key |
| `USERBOT_BALE_RELAY_PEER_ID` | `1519372475` | Relay account (VPS) user_id |
| `USERBOT_BALE_LISTEN_PORT` | `1080` | Local SOCKS5 port |
| `USERBOT_BALE_SERVICE` | `Wi-Fi` | macOS network service name |

### Setup Relay on VPS (Ubuntu)

```bash
# Run once to install the systemd service
ssh root@<VPS-IP> bash << 'EOF'
cat > /etc/systemd/system/userbot-bale-relay.service << 'SVC'
[Unit]
Description=userbot-bale relay proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/userbot-bale-run/project
ExecStart=/root/userbot-bale-run/project/.venv/bin/python -u -m userbot_bale.cli \
    bale-proxy relay \
    --transport dc \
    --bale-jwt-file /root/userbot-bale-run/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file /root/userbot-bale-run/userbot-bale-vpn.psk \
    --ws-ssl-no-verify
Restart=always
RestartSec=10
SVC
systemctl daemon-reload
systemctl enable --now userbot-bale-relay.service
EOF
```

```bash
# Check relay status
ssh root@<VPS-IP> systemctl status userbot-bale-relay.service
```

---

## Common Commands

```bash
# Authentication
userbot-bale auth bale-login --phone +98912xxxxxxx --method browser --headful --save --no-print-jwt
userbot-bale auth status

# Userbot and MCP
userbot-bale userbot allow-peer 123456789
userbot-bale userbot run
pip install -e ".[mcp]"
userbot-bale mcp serve

# Health check
userbot-bale doctor

# Manual proxy (without LaunchAgent)
bash scripts/run-proxy-client.sh

# LaunchAgent management
bash scripts/install-launchagent-client.sh --status
bash scripts/install-launchagent-client.sh --logs
bash scripts/install-launchagent-client.sh --start
bash scripts/install-launchagent-client.sh --stop
bash scripts/install-launchagent-client.sh --uninstall

# Two-account smoke test
userbot-bale vpn live-smoke \
    --caller-jwt-file ~/.bale_jwt_b \
    --callee-jwt-file ~/.bale_jwt_a \
    --callee-peer-id 1519372475
```

---

## Advanced Commands

```bash
# Server-side relay (manual)
userbot-bale bale-proxy relay \
    --bale-jwt-file ~/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file ~/.userbot-bale/userbot-bale-vpn.psk \
    --ws-ssl-no-verify

# Mac client (manual, with system proxy)
userbot-bale bale-proxy system \
    --bale-jwt-file ~/.bale_jwt_b \
    --peer-id 1519372475 \
    --proxy-secret-file ~/.userbot-bale/userbot-bale-vpn.psk \
    --service Wi-Fi \
    --ws-ssl-no-verify

# Loopback tests
userbot-bale loopback "hello" "world"
userbot-bale tunnel-loopback
```

---

## Troubleshooting

| Symptom | Probable Cause | Solution |
|---|---|---|
| `No module named 'cryptography'` | Missing dependency | `pip install cryptography>=42` |
| `WS did not connect within 15s` | Network or Bale WS down | Use `--ws-ssl-no-verify` or check connectivity |
| `OSError: EINVAL` on TCP_NODELAY | macOS daemon context | Automatically patched in `ws_client.py` |
| `BrokenPipeError` in SSL handshake | SIGPIPE without handler | Handled by the wrapper script |
| `proxy_listening` does not appear | Relay did not answer | Ensure VPS relay is running |
| Wi-Fi proxy remains ON after stop | SIGKILL instead of SIGTERM | Use `launchctl stop` (not `kill -9`) |

---

## Documentation

- [Getting Started](docs/SETUP.md)
- [Install and Bootstrap](docs/INSTALL.md)
- [Windows Development](docs/WINDOWS_DEVELOPMENT.md)
- [Bale Headless Notes](docs/BALE_HEADLESS.md)
- [Native macOS Scaffold](native/macos/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Userbot and MCP](docs/USERBOT.md)
- [VPN Operations](docs/OPERATIONS.md)
- [VPN Transport](docs/VPN.md)
- [macOS client + Linux VPS production-test](docs/MACOS_VPS_PRODUCTION_TEST.md)

---

## Glossary

| Term | VPN Equivalence | Meaning |
|---|---|---|
| `ControlService` / `ProvisioningService` | Control plane | Auth, pairing, and credential management |
| `CarrierSession` | Carrier / call session | Active live media session |
| `Transport` | Bearer transport | Byte transport layer (DataChannel, audio, QR) |
| `TransportPool` / `TransportChain` | Transport selector | Switches between available transports |
| `Tunnel` | Tunnel engine | Handles framing, ARQ, reassembly |
| `VpnRunner` | Tunnel runner | Connects TUN to tunnel engine |
| `TunnelBridge` / `TunnelService` | Tunnel service boundary | IPC interface |
| `VpnBackend` | VPN backend adapter | Platform-specific integration layer |
| `CredentialEpoch` | Credential lease | Short-lived secret window |
| `TUN` | Kernel tunnel interface | OS device for IP packets |
