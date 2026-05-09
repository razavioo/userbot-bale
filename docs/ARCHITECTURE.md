# Architecture

This document describes the product layers from the user-facing control plane down to the kernel TUN device. It focuses on the runtime path used by `vpn up`, `vpn exit-node`, mesh mode, and the packet-tunnel backend scaffold.

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

### Control Plane

Source files:

- `src/baleobala/control/service.py`
- `src/baleobala/control/provisioning.py`
- `src/baleobala/control/backend.py`
- `src/baleobala/control/tunnel_service.py`

Responsibilities:

- Store auth, pairing, relay enrollment, and device authorization state.
- Issue and rotate credential epochs.
- Select the active backend for the current profile.
- Expose the narrow IPC surface used by the packet-tunnel scaffold.

Key interfaces:

- `VpnBackend` in `src/baleobala/control/backend.py`
- `TunnelService` in `src/baleobala/control/tunnel_service.py`
- `TunnelBridge` in `src/baleobala/control/tunnel_service.py`

### Carrier Layer

Source files:

- `src/baleobala/carrier/interfaces.py`
- `src/baleobala/carrier/livekit.py`
- `src/baleobala/bale/livekit_backend.py`

Responsibilities:

- Own the live media session.
- Provide audio and source/sink access for the carrier path.
- Publish or receive tunnel bytes through the carrier session.

Key interface:

- `CarrierSession` in `src/baleobala/carrier/interfaces.py`

### Transport Layer

Source files:

- `src/baleobala/vpn/transports/__init__.py`
- `src/baleobala/vpn/transports/datachannel_transport.py`
- `src/baleobala/vpn/transports/audio_transport.py`
- `src/baleobala/vpn/transports/video_qr_transport.py`
- `src/baleobala/vpn/transports/rpc_transport.py`
- `src/baleobala/vpn/crypto.py`

Responsibilities:

- Present a simple byte-channel abstraction to the tunnel.
- Hide carrier-specific mechanics behind a common `Transport` protocol.
- Support transport selection, transport chaining, and optional AEAD wrapping.

Key interface:

- `Transport` in `src/baleobala/vpn/transports/__init__.py`

### Tunnel Layer

Source files:

- `src/baleobala/vpn/tunnel.py`
- `src/baleobala/vpn/runner.py`
- `src/baleobala/vpn/router.py`
- `src/baleobala/vpn/supervisor.py`

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

- `src/baleobala/vpn/tun.py`
- platform-specific backend glue under `src/baleobala/control/`

Responsibilities:

- Read IP packets from the kernel.
- Write decrypted or reassembled packets back to the kernel.
- Keep the user-space tunnel isolated from platform details.

## Interface Contracts

| Interface | Source | Contract |
| --- | --- | --- |
| `Transport` | `src/baleobala/vpn/transports/__init__.py` | Opaque byte frames with `send_bytes`, `recv_bytes`, and `close`. |
| `CarrierSession` | `src/baleobala/carrier/interfaces.py` | Live carrier session with audio sink/source access and start/stop lifecycle. |
| `VpnBackend` | `src/baleobala/control/backend.py` | Platform backend with `up`, `down`, `status`, and `probe`. |
| `TunnelBridge` | `src/baleobala/control/tunnel_service.py` | Minimal bridge used by the packet-tunnel scaffold to exchange bytes with the carrier/runtime. |
| `TunnelService` | `src/baleobala/control/tunnel_service.py` | IPC-facing service that owns lifecycle and state for the future native extension. |

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

## Coordinator and Multi-Relay Topology

In production the system runs as three tiers communicating through short-lived LiveKit rooms:

```text
Client ──HELLO──► Coordinator ──EXPECT_CLIENT──► Relay
                   └──ASSIGN──► Client ◄──────────────┘ (relay calls back)
```

### Coordinator (`src/baleobala/coordinator/`)

A single Bale account that listens for inbound calls, runs the control-message protocol defined in `coordinator/protocol.py`, and assigns clients to relays.

**HELLO → ASSIGN flow:**

1. Client dials coordinator, sends `HELLO` with its `client_peer_id` and optional `client_region`.
2. Coordinator calls `pick_relay()` (`coordinator/policy.py`) — region-affinity first, then lowest load fraction, tie-break by `relay_id`.
3. Coordinator generates a 64-char hex ephemeral PSK (`session_psk`) for the session.
4. Coordinator dials the chosen relay, sends `EXPECT_CLIENT` (carrying `client_peer_id`, `session_id`, `session_psk`, TTL). Relay sends `EXPECT_ACK`.
5. Only after `EXPECT_ACK` does the coordinator send `ASSIGN` to the client (carrying `relay_peer_id`, `session_psk`). This ordering prevents the dial-race where the client arrives at the relay before the relay knows to expect it.
6. Client dials relay directly. Relay's `ExpectedClientSet` (TTL-gated) approves the call.

**Relay lifecycle messages** (sent from relay → coordinator via `CoordinatorReporter`):

| Kind          | When                                          |
| ------------- | --------------------------------------------- |
| `ONLINE`      | relay startup, per JWT slot                   |
| `HEARTBEAT`   | every 60 s (default); updates `in_use` list   |
| `RELEASED`    | VPN session ends                              |
| `OFFLINE`     | relay shutdown                                |

Each message carries an HMAC-SHA256 signature (`coordinator/auth.py`) keyed to a per-relay secret stored in the `RelayRegistry`. Unenrolled relays are accepted with a WARNING to allow incremental migration.

**Registry snapshot** (`coordinator/registry.py`) is written to disk after every state change so coordinator restarts don't lose relay enrollments or in-use counts.

### Relay (`src/baleobala/vpn/relay_handler.py`)

Each relay JWT maps to one `RelayCallHandler` instance. The handler is a state machine with a correlation ID (`cid`, 8-char hex UUID prefix) threaded through every log call for tracing.

**Per-call lifecycle:**

1. Inbound Bale call arrives → handler picks the least-loaded JWT slot.
2. Probe phase: joins the LiveKit room, reads the control channel. If the coordinator's `EXPECT_CLIENT` was pre-sent, reads a direct VPN call and promotes to tunnel. If the call is from the coordinator itself, registers the expected client and hangs up (probe-only).
3. Tunnel phase: issues a mesh assignment (`MeshExitNode.issue_client()`), starts the `EncryptedTransport` with the session PSK, sends provisioning ACK.
4. Session reaper (`SessionReaper`) runs one teardown per cycle to avoid FFI contention in the livekit-rtc Rust runtime.

### Metrics and observability

- Relay: `http://127.0.0.1:9201/metrics` and `/healthz`
- Coordinator: `http://127.0.0.1:9202/metrics` and `/healthz`
- Prometheus counters: `baleobala_relay_calls_total{result}`, `baleobala_relay_evictions_total`, `baleobala_relay_stale_recovery_total`, `baleobala_coordinator_assigns_total{result}`, `baleobala_coordinator_relay_events_total{kind}`
- Gauges: `baleobala_relay_sessions_active`, `baleobala_coordinator_relays_online`, `baleobala_coordinator_relays_capacity`
- Log format: `BALEOBALA_LOG_FORMAT=text` (default) or `json` for journald / log aggregators.

### Wire protocol

All coordinator control messages use the format defined in `coordinator/protocol.py`: a JSON-encoded dict with a `kind` field (one of `ONLINE`, `HEARTBEAT`, `RELEASED`, `OFFLINE`, `HELLO`, `ASSIGN`, `DENY`, `EXPECT_CLIENT`, `EXPECT_ACK`) transmitted over a LiveKit DataChannel with topic `"control"`.

## Operational Notes

- Transport hot-swap keeps tunnel state alive across bearer changes.
- The failover controller can rebuild the carrier when the current session becomes terminal.
- Credential epochs are short-lived by design, so operator workflows should expect rotation and renewal.
- The packet-tunnel scaffold stores state locally and is intentionally narrow so the future native extension can stay simple.
- Correlation IDs (`cid=xxxxxxxx`) in relay logs span probe → VPN session → reap; `grep cid=<id>` on the relay journal returns the full call lifecycle.
- All `time.sleep()` FFI workarounds after `session.stop()` have been removed; the Rust tokio runtime is fully torn down synchronously before the call returns.
