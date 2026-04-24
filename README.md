# baleobala

`baleobala` is a CLI and GUI project for Bale sign-in, relay pairing, and secure connection flows.

Production-facing commands are `doctor`, `auth`, `pair`, `relay`, `vpn`, and `gui`.
Low-level commands such as `loopback`, `tunnel-loopback`, `bale-call`, `bale-tunnel`, and
`bale-proxy` remain available for engineering and debugging, not as parallel first-run paths.

The main product path is: `doctor -> auth -> pair -> connect`.

## Architecture

```mermaid
flowchart LR
    User[User] --> ControlPlane[Control Plane]
    ControlPlane --> Carrier[Carrier]
    Carrier --> Transport[Transport]
    Transport --> Tunnel[Tunnel]
    Tunnel --> TUN[TUN]
```

At a high level, the product flow is:

- User actions go through the control plane for auth, pairing, provisioning, and backend selection.
- The carrier is the live session layer that keeps a call open and moves bytes for the tunnel.
- The transport is the byte-bearing link inside the carrier, such as DataChannel, audio, QR, or RPC.
- The tunnel turns opaque transport frames into IP packets and keeps ARQ, reassembly, and swap logic alive.
- The TUN device is the kernel edge where IP packets enter and leave the host.

## Install

Create a virtual environment and install the Bale and GUI dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,bale,desktop]"
```

If `python` is not on `PATH` in your environment, use `./.venv/bin/python` as a fallback
entrypoint. The packaged and documented entrypoint is `baleobala`.

## Quick Start

```bash
baleobala doctor
baleobala auth bale-login --phone +98912xxxxxxx --method browser --headful --save
baleobala gui
```

- `doctor`: checks local readiness and required dependencies.
- `auth bale-login`: runs the real Bale phone/SMS login flow and can save or export the JWT.
- `gui`: launches the guided desktop app for sign-in, pairing, and connection.

## Login Modes

Headless terminal login:

```bash
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save
```

Visible browser login flow:

```bash
./.venv/bin/python -m baleobala.cli auth bale-login --phone +98912xxxxxxx --method browser --headful --save
```

- `--method browser`: forces the same web login path we verified against the GUI flow.
- `--headful`: opens Chromium visibly so you can watch the Bale login flow.
- `--save`: stores the JWT in the local auth store for later `vpn` commands.
- `./.venv/bin/python -m baleobala.cli ...` is a fallback/debug entrypoint; production docs should prefer `baleobala ...`.
- `bale-auth` still exists for backwards compatibility; it now emits a deprecation warning and `auth bale-login` is the preferred path.

## Common Commands

```bash
baleobala auth status
baleobala vpn status
baleobala vpn live-smoke --caller-jwt-file ~/.bale_jwt_a --callee-jwt-file ~/.bale_jwt_b --callee-peer-id 123456789
baleobala vpn up
baleobala vpn down
baleobala pair enroll --name home-relay --role client
baleobala pair request-access --profile-id "<profile-id>"
baleobala pair approve --profile-id "<profile-id>"
baleobala relay status
```

- `auth status`: shows the stored auth/session state.
- `vpn status`: shows the current control-plane and backend state.
- `vpn live-smoke`: runs the real two-account Bale call + LiveKit DataChannel smoke test.
- On intercepted/MITM networks, prefer `--ws-ca-file /path/to/ca.pem` or `--ws-ca-path /path/to/ca-dir`.
- `--ws-ssl-no-verify` remains available only as a debug-only workaround and should not be used as a production path.
- `vpn up`: starts the active VPN/proxy backend.
- `vpn down`: stops the active backend and cleans up state.
- `pair enroll`: creates a server-backed relay pairing record.
- `pair request-access`: asks the control plane to authorize this device.
- `pair approve`: completes owner approval and provisions credentials.
- `relay status`: shows saved relay settings.

## Advanced And Debug Commands

```bash
baleobala loopback "hello" "world"
baleobala tunnel-loopback
baleobala send --text "hello"
baleobala recv
```

- `loopback`: runs an in-process codec/framing self-test without real audio devices.
- `tunnel-loopback`: runs an in-process byte-tunnel self-test.
- `send`: encodes text and writes it to the current audio sink.
- `recv`: reads audio input and prints decoded messages.

These commands are useful for engineering and troubleshooting, but the production user path
should stay centered on `doctor`, `auth`, `pair`, `vpn`, and `gui`.

## When To Use What

- Use `auth bale-login` when you want a real Bale JWT through the verified browser flow.
- Use `vpn live-smoke` when you want to verify real signaling plus real bidirectional DataChannel bytes.
- Use `doctor` before debugging environment issues.
- Use `vpn up` when you want to bring up the current tunnel/proxy path.

## Docs

- [Getting Started](docs/SETUP.md)
- [Install and Bootstrap](docs/INSTALL.md)
- [Bale Headless Notes](docs/BALE_HEADLESS.md)
- [Native macOS Scaffold](native/macos/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [macOS client + Linux VPS production-test](docs/MACOS_VPS_PRODUCTION_TEST.md)

## Glossary

| Code term | VPN term | Meaning |
| --- | --- | --- |
| `ControlService` / `ProvisioningService` | control plane | Owns auth, pairing, relay enrollment, and credential issuance. |
| `CarrierSession` | carrier / call session | The live media session that carries the tunnel payload. |
| `Transport` | bearer transport | The opaque byte pipe used inside the carrier. |
| `TransportPool` / `TransportChain` | transport selector | Chooses and swaps between available bearer transports. |
| `Tunnel` | tunnel engine | Handles framing, fragmentation, ARQ, and reassembly. |
| `VpnRunner` | tunnel runner | Connects TUN to the tunnel and drains the packet queue. |
| `TunnelBridge` / `TunnelService` | tunnel service boundary | IPC surface for the future packet-tunnel integration. |
| `VpnBackend` | VPN backend adapter | Platform-specific integration layer for packet tunnel or proxy modes. |
| `CredentialEpoch` | credential lease | Short-lived secret window used for relay/device access. |
| `TUN` | kernel tunnel interface | The OS device that injects and receives IP packets. |
