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

## Operational Notes

- Transport hot-swap keeps tunnel state alive across bearer changes.
- The failover controller can rebuild the carrier when the current session becomes terminal.
- Credential epochs are short-lived by design, so operator workflows should expect rotation and renewal.
- The packet-tunnel scaffold stores state locally and is intentionally narrow so the future native extension can stay simple.
