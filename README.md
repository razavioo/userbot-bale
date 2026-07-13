<p align="center">
  <img src="docs/assets/icon.png" alt="baleobala" width="160" height="160" />
</p>

# baleobala

`baleobala` is a Bale integration framework. It provides a call-backed VPN and proxy transport, a durable single-account messaging userbot runtime, and an allowlist-gated MCP server for local AI integrations. The VPN path uses a Bale LiveKit call; the userbot and MCP path use Bale's authenticated WebSocket API.

---

## Quick Connect (macOS)

> **Prerequisite:** The relay service on the VPS must be running (instructions below).

### Connect

```bash
bash scripts/run-proxy-client.sh
```

Or if the LaunchAgent is installed (it starts automatically after login):

```bash
launchctl start ai.baleobala.proxy-client
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
launchctl stop ai.baleobala.proxy-client
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
BALEOBALA_JWT_FILE=~/.bale_jwt_b \
BALEOBALA_PSK_FILE=~/.baleobala/baleobala-vpn.psk \
BALEOBALA_RELAY_PEER_ID=1519372475 \
bash scripts/install-launchagent-client.sh --install
```

Important Variables:

| Variable | Default | Description |
|---|---|---|
| `BALEOBALA_JWT_FILE` | `~/.bale_jwt_b` | Client account (Mac) JWT |
| `BALEOBALA_PSK_FILE` | `~/.baleobala/baleobala-vpn.psk` | Encryption pre-shared key |
| `BALEOBALA_RELAY_PEER_ID` | `1519372475` | Relay account (VPS) user_id |
| `BALEOBALA_LISTEN_PORT` | `1080` | Local SOCKS5 port |
| `BALEOBALA_SERVICE` | `Wi-Fi` | macOS network service name |

### Setup Relay on VPS (Ubuntu)

```bash
# Run once to install the systemd service
ssh root@<VPS-IP> bash << 'EOF'
cat > /etc/systemd/system/baleobala-relay.service << 'SVC'
[Unit]
Description=baleobala relay proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/baleobala-run/project
ExecStart=/root/baleobala-run/project/.venv/bin/python -u -m baleobala.cli \
    bale-proxy relay \
    --transport dc \
    --bale-jwt-file /root/baleobala-run/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file /root/baleobala-run/baleobala-vpn.psk \
    --ws-ssl-no-verify
Restart=always
RestartSec=10
SVC
systemctl daemon-reload
systemctl enable --now baleobala-relay.service
EOF
```

```bash
# Check relay status
ssh root@<VPS-IP> systemctl status baleobala-relay.service
```

---

## Common Commands

```bash
# Authentication
baleobala auth bale-login --phone +98912xxxxxxx --method browser --headful --save --no-print-jwt
baleobala auth status

# Userbot and MCP
baleobala userbot allow-peer 123456789
baleobala userbot run
pip install -e ".[mcp]"
baleobala mcp serve

# Health check
baleobala doctor

# Manual proxy (without LaunchAgent)
bash scripts/run-proxy-client.sh

# LaunchAgent management
bash scripts/install-launchagent-client.sh --status
bash scripts/install-launchagent-client.sh --logs
bash scripts/install-launchagent-client.sh --start
bash scripts/install-launchagent-client.sh --stop
bash scripts/install-launchagent-client.sh --uninstall

# Two-account smoke test
baleobala vpn live-smoke \
    --caller-jwt-file ~/.bale_jwt_b \
    --callee-jwt-file ~/.bale_jwt_a \
    --callee-peer-id 1519372475
```

---

## Advanced Commands

```bash
# Server-side relay (manual)
baleobala bale-proxy relay \
    --bale-jwt-file ~/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file ~/.baleobala/baleobala-vpn.psk \
    --ws-ssl-no-verify

# Mac client (manual, with system proxy)
baleobala bale-proxy system \
    --bale-jwt-file ~/.bale_jwt_b \
    --peer-id 1519372475 \
    --proxy-secret-file ~/.baleobala/baleobala-vpn.psk \
    --service Wi-Fi \
    --ws-ssl-no-verify

# Loopback tests
baleobala loopback "hello" "world"
baleobala tunnel-loopback
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
