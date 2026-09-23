# Architecture

This document describes the product layers for the VPN/relay path and the separate messaging
automation path. The VPN sections focus on `vpn up`, `vpn exit-node`, mesh mode, and the
packet-tunnel backend scaffold. Userbot and MCP use the authenticated Bale WebSocket API and do
not enter the carrier, transport, or kernel layers.

## End-To-End Data Flow

```mermaid
flowchart LR
    User[User / Operator]
    ControlPlane[Control Plane]
    Carrier[CarrierSession]
    Transport[Transport]
    Tunnel[Tunnel]
    Runner[VpnRunner]
    TUN[TUN device]

    User --> ControlPlane
    ControlPlane --> Carrier
    Carrier --> Transport
    Transport --> Tunnel
    Tunnel --> Runner
    Runner --> TUN

    ControlPlane -. auth, pairing, provisioning, backend state .-> User
    Tunnel -. status, failover, recovery state .-> ControlPlane
```

The important rule is that each layer owns one boundary:

- The control plane decides who is allowed to connect and which backend should run.
- The carrier session keeps the live media session open.
- The transport carries opaque byte frames inside that carrier.
- The tunnel owns packet framing, ARQ, and hot-swap preservation.
- The runner connects the tunnel to the kernel TUN device.

## Layer Map

### Bale API And Automation Layer

Source files:

- `src/userbot-bale/bale/auth.py`
- `src/userbot-bale/bale/ws_client.py`
- `src/userbot-bale/bale/api.py`
- `src/userbot-bale/userbot/`
- `src/userbot-bale/mcp/server.py`

Responsibilities:

- Authenticate a locally owned Bale account with phone/SMS and retain the JWT in the configured
  secret backend.
- Keep one authenticated WebSocket session alive, re-subscribe to updates after reconnect, and
  decode text-message pushes.
- Persist userbot messages, deduplicate inbound deliveries across restarts, and dispatch plugins.
- Offer a narrow local MCP interface for account status, approved dialogs/messages, and approved
  outbound text.

Boundaries:

- `BaleUserClient` disables call auto-accept; calls remain a carrier/VPN concern.
- `UserbotStore` persists message and audit data in SQLite but never stores a JWT.
- MCP is stdio-only, restricts reads and sends to the local peer allowlist, and applies the same
  20-message-per-peer-per-minute outbound cap as the userbot.
- MCP tool results are normalized JSON objects (`{items, count}` / status objects) so
  `structuredContent` is always type `object`; `send_text` requires a two-phase
  `confirm_token` handshake (preview first, deliver only on matching confirm).
- Neither the userbot nor MCP exposes VPN routing, proxy setup, contact import, raw RPC payloads,
  pairing, or call control.

### Control Plane

Source files:

- `src/userbot-bale/control/service.py`
- `src/userbot-bale/control/provisioning.py`
- `src/userbot-bale/control/backend.py`
- `src/userbot-bale/control/tunnel_service.py`

Responsibilities:

- Store auth, pairing, relay enrollment, and device authorization state.
- Issue and rotate credential epochs.
- Select the active backend for the current profile.
- Expose the narrow IPC surface used by the packet-tunnel scaffold.

Key interfaces:

- `VpnBackend` in `src/userbot-bale/control/backend.py`
- `TunnelService` in `src/userbot-bale/control/tunnel_service.py`
- `TunnelBridge` in `src/userbot-bale/control/tunnel_service.py`

### Carrier Layer

Source files:

- `src/userbot-bale/carrier/interfaces.py`
- `src/userbot-bale/carrier/livekit.py`
- `src/userbot-bale/bale/livekit_backend.py`

Responsibilities:

- Own the live media session.
- Provide audio and source/sink access for the carrier path.
- Publish or receive tunnel bytes through the carrier session.

Key interface:

- `CarrierSession` in `src/userbot-bale/carrier/interfaces.py`

### Transport Layer

Source files:

- `src/userbot-bale/vpn/transports/__init__.py`
- `src/userbot-bale/vpn/transports/datachannel_transport.py`
- `src/userbot-bale/vpn/transports/audio_transport.py`
- `src/userbot-bale/vpn/transports/video_qr_transport.py`
- `src/userbot-bale/vpn/transports/rpc_transport.py`
- `src/userbot-bale/vpn/crypto.py`

Responsibilities:

- Present a simple byte-channel abstraction to the tunnel.
- Hide carrier-specific mechanics behind a common `Transport` protocol.
- Support transport selection, transport chaining, and optional AEAD wrapping.

Key interface:

- `Transport` in `src/userbot-bale/vpn/transports/__init__.py`

### Tunnel Layer

Source files:

- `src/userbot-bale/vpn/tunnel.py`
- `src/userbot-bale/vpn/runner.py`
- `src/userbot-bale/vpn/router.py`
- `src/userbot-bale/vpn/supervisor.py`

Responsibilities:

- Fragment and reassemble packets.
- Provide selective-repeat ARQ.
- Keep the tunnel session alive across bearer swaps.
- Observe health and initiate failover when the current bearer stalls.

Key runtime objects:

- `Tunnel`
- `VpnRunner`
- `TransportPool`
- `FailoverController`
- `HealthMonitor`

### Kernel Boundary

Source files:

- `src/userbot-bale/vpn/tun.py`
- platform-specific backend glue under `src/userbot-bale/control/`

Responsibilities:

- Read IP packets from the kernel.
- Write decrypted or reassembled packets back to the kernel.
- Keep the user-space tunnel isolated from platform details.

## Interface Contracts

| Interface | Source | Contract |
| --- | --- | --- |
| `Transport` | `src/userbot-bale/vpn/transports/__init__.py` | Opaque byte frames with `send_bytes`, `recv_bytes`, and `close`. |
| `CarrierSession` | `src/userbot-bale/carrier/interfaces.py` | Live carrier session with audio sink/source access and start/stop lifecycle. |
| `VpnBackend` | `src/userbot-bale/control/backend.py` | Platform backend with `up`, `down`, `status`, and `probe`. |
| `TunnelBridge` | `src/userbot-bale/control/tunnel_service.py` | Minimal bridge used by the packet-tunnel scaffold to exchange bytes with the carrier/runtime. |
| `TunnelService` | `src/userbot-bale/control/tunnel_service.py` | IPC-facing service that owns lifecycle and state for the future native extension. |
| `BaleUserClient` | `src/userbot-bale/userbot/client.py` | One-account messaging lifecycle, durable inbound events, peer policy, and bounded outbound text. |
| `BaleMcpService` | `src/userbot-bale/mcp/server.py` | Stdio MCP tool boundary over the allowlist-gated userbot surface. |

## Data Boundaries

### User And Control Plane

The user-facing commands and GUI drive the control plane first. That layer decides:

- whether auth is present,
- whether the relay/device is approved,
- which backend should be used,
- and whether the current credential epoch is still valid.

### Control Plane And Carrier

The control plane creates or resumes a carrier session and ties the session to a profile or pairing record. The carrier itself does not decide VPN policy; it only provides the live session that can move bytes.

### Carrier And Transport

The carrier session exposes the media-side mechanisms used by the transport. A `Transport` implementation can be backed by LiveKit DataChannel, audio, QR, RPC, or another future carrier-specific path.

### Transport And Tunnel

The tunnel only sees opaque byte frames. It does not know whether the bytes came from DataChannel, audio, QR, or RPC. This keeps the tunnel logic stable while the bearer changes.

### Tunnel And TUN

The tunnel consumes IP packets from the kernel TUN device and writes reassembled packets back to it. This is the only place where kernel packet handling appears in the main user-space runtime.

### Userbot And MCP

The messaging path is intentionally independent of the VPN runtime:

```text
Bale phone/SMS auth -> secret backend -> BaleApiClient / WebSocket updates
                                      -> BaleUserClient -> SQLite state -> plugins
                                                                   -> MCP stdio tools
```

An approved peer is required before the MCP server can read that peer's local messages or expose
its dialog metadata. The same allowlist is required for every outbound send.

## Topology (post-v0.4: no coordinator)

The system is two tiers: clients and relays. There is no rendezvous service. Each client app keeps a user-managed list of relay Bale `peer_id`s; to connect it picks one, places a Bale call to it, and runs the PSK handshake. If that fails it falls back to the next peer_id in the list.

```text
Client (app holds list of relay peer_ids)
   │
   │ pick → Bale StartCall → LiveKit DataChannel
   │ PSK handshake → BBMESH1 ack → tunnel up
   ▼
Relay (mesh exit-node) — one per Bale JWT, all on one VPS
   • accepts any inbound call (PSK authenticates)
   • resolves caller user_id from LiveKit participant identity
   • allocates a /30 from the pool, issues client via MeshExitNode
   • MASQUERADEs egress to eth0
```

### Relay (`src/userbot-bale/vpn/relay_handler.py`)

Each relay JWT maps to one `RelayCallHandler` instance shared across all account-indexed listen callbacks. The handler is a state machine with a correlation ID (`cid`, 8-char hex UUID prefix) threaded through every log call for tracing.

**Per-call lifecycle:**

1. Inbound Bale call arrives. If the per-account active flag is set (a previous session in flight) the new call is refused with `slot_busy` — the client will fall back to the next peer_id in its list.
2. Join phase: opens a LiveKit session (publish_audio=False) and waits for the caller to join the room.
3. Identity resolution: reads the caller's real user_id from `LiveKitSession.remote_participant_identities()`. `event.peer_id` from Bale is the relay's own user_id under push semantics, so this step is mandatory — without it every caller on the same relay account would collide on `mesh.issue_client`.
4. Tunnel phase: issues a mesh assignment (`MeshExitNode.issue_client()`), enables audio on the session (so the SFU keeps it alive past ~20 s), starts the `EncryptedTransport` with the global PSK, sends provisioning ACK.
5. Session reaper (`SessionReaper`) runs one teardown per cycle to avoid FFI contention in the livekit-rtc Rust runtime.

### Metrics and observability

- Relay: `http://127.0.0.1:9201/metrics` and `/healthz`
- Prometheus counters: `userbot_bale_relay_calls_total{outcome}` (committed, slot_busy, no_caller_identity, provision_failed, …), `userbot_bale_relay_evictions_total`, `userbot_bale_relay_stale_recovery_total`
- Gauges: `userbot_bale_relay_sessions_active`, `userbot_bale_relay_in_use_per_account{account}`, `userbot_bale_account_active_age_seconds{account}`
- Log format: `USERBOT_BALE_LOG_FORMAT=text` (default) or `json` for journald / log aggregators.

## Operational Notes

- Transport hot-swap keeps tunnel state alive across bearer changes.
- The failover controller can rebuild the carrier when the current session becomes terminal.
- Credential epochs are short-lived by design, so operator workflows should expect rotation and renewal.
- The packet-tunnel scaffold stores state locally and is intentionally narrow so the future native extension can stay simple.
- Correlation IDs (`cid=xxxxxxxx`) in relay logs span probe → VPN session → reap; `grep cid=<id>` on the relay journal returns the full call lifecycle.
- All `time.sleep()` FFI workarounds after `session.stop()` have been removed; the Rust tokio runtime is fully torn down synchronously before the call returns.
