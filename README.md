# baleobala

`baleobala` is a transport project for moving bytes over Bale-hosted audio or media sessions, with a clear path toward a full desktop VPN experience on Linux and macOS.

The repo now has three layers:

- a transport core for framing, tunneling, and proxying bytes,
- Bale carrier integration for LiveKit session bootstrap,
- and a product plan that moves this into an easy-to-install desktop VPN client plus relay.

## Current state

- Linux-first audio transport works today through `send`, `recv`, `virtmic`, and the Bale LiveKit tunnel path.
- A tunneled proxy path is already present for SOCKS5 and HTTP CONNECT.
- On macOS, `vpn up` currently uses the local proxy plus system proxy settings so the machine can be exercised end-to-end now.
- The native macOS app/packet-tunnel scaffold now lives under `native/macos/` and is wired around the same carrier socket contract as the Python runtime.
- The long-term product direction is a signed desktop client with full-device VPN support, saved relay pairing, and no manual JWT workflow.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

For Bale LiveKit integration:

```bash
pip install -e ".[dev,bale]"
```

For the desktop app stack that is planned for the VPN release:

```bash
pip install -e ".[dev,bale,desktop]"
```

## Quick start

If you want the current byte-transport path:

```bash
baleobala loopback "hello" "world"
baleobala tunnel-loopback
```

If you want the current Bale tunnel path:

```bash
baleobala bale-tunnel send --text "hello"
```

If you want the local proxy fallback:

```bash
baleobala bale-proxy client --listen-port 1080
```

That proxy path still needs a carrier session, either through explicit
LiveKit credentials or the Bale auth flow in the headless commands.

Before you try the carrier or proxy commands, run:

```bash
baleobala doctor
```

It prints the local readiness check and highlights any missing pieces.

### Running the VPN on macOS (single machine, no Apple Dev, no remote peer)

On macOS the default backend is `direct`: `baleobala vpn up` starts an
in-process SOCKS5 + HTTP CONNECT listener on `127.0.0.1:1080` and points
the macOS system proxy at it via `networksetup`. Traffic from every app
that honors the system proxy flows through the local forwarder and out
to the real internet. No Apple Developer Team ID, no paired peer, no
JWT required.

```bash
baleobala vpn up          # default: --backend direct on macOS
# ... Ctrl-C to stop; system proxy is restored on exit.
baleobala vpn down        # explicit restore if a crash skipped cleanup
baleobala vpn status
```

The listener is bound *before* the system proxy is flipped, and if
either step fails the other is unwound — networking is never left
proxied-to-nowhere. An `atexit` + signal handler performs a best-effort
restore on crash.

If you have a paired Bale relay on another machine and want to route
traffic through it over the LiveKit carrier, use the `proxy` backend
instead:

```bash
baleobala auth login --jwt "$BALE_JWT"
baleobala pair start --name home-relay --role client
baleobala pair accept --code "<pair-code>"
baleobala relay enable
baleobala vpn up --backend proxy
```

The `packet-tunnel` backend in [native/macos/](native/macos/) is parked —
it would require a paid Apple Developer Team ID and the Network
Extension entitlement, which we do not have.

For an always-on macOS launch agent, install it once with
`baleobala vpn agent install`.

## What we are delivering

- A Linux/macOS desktop product that can connect a client to a relay and carry full-device traffic.
- A saved pairing flow so the user does not re-enter peer details on every connect.
- A secure auth/session model that replaces manual JWT capture.
- A fallback proxy path for debugging and environments where full VPN setup is not available yet.
- Signed, installable release builds for macOS and packaged release builds for Linux.

## CLI surface

| Command | Purpose |
| --- | --- |
| `baleobala virtmic` | Create the current Linux virtual mic bridge |
| `baleobala send` | Encode messages and play them to an audio sink |
| `baleobala recv` | Read audio from a source and decode messages |
| `baleobala loopback` | Verify the codec/framing path in-process |
| `baleobala tunnel-loopback` | Verify the byte tunnel in-process |
| `baleobala doctor` | Check local readiness and missing dependencies |
| `baleobala auth` | Store, inspect, or clear Bale auth state |
| `baleobala pair` | Create and accept relay pairing records |
| `baleobala relay` | Save relay runtime settings |
| `baleobala vpn` | Run or inspect the product control plane |
| `baleobala vpn agent` | Install or manage the macOS LaunchAgent |
| `baleobala bale-call` | Use Bale LiveKit credentials directly |
| `baleobala bale-tunnel` | Run the byte tunnel over Bale LiveKit |
| `baleobala bale-proxy` | Run the SOCKS5/HTTP CONNECT proxy transport |

## Platform notes

- Linux is the primary platform for current audio transport and the first full VPN target.
- macOS is a first-class target for the desktop product and signed release flow.
- The current audio mic helper is Linux-native; macOS support in the product plan is based on an app shell and platform-specific VPN plumbing, not the current `virtmic` helper.

## Docs

- [Getting Started](docs/SETUP.md)
- [Desktop VPN Plan](docs/VPN_PLAN.md)
- [Bale Headless Notes](docs/BALE_HEADLESS.md)
- [Native macOS Scaffold](native/macos/README.md)
- [Bale Web Path](docs/BALE_WEB.md)
- [Waydroid Path](docs/BALE_WAYDROID.md)
- [RE Notes](docs/BALE_RE_NOTES.md)
