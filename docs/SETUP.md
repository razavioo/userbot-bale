# Getting Started

This guide covers the current repo state and the easiest way to install and try it on Linux and macOS.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

If you want Bale LiveKit support:

```bash
pip install -e ".[dev,bale]"
```

If you want the desktop-app stack that is planned for the VPN release:

```bash
pip install -e ".[dev,bale,desktop]"
```

## What works today

- `baleobala loopback` verifies codec/framing in-process.
- `baleobala tunnel-loopback` verifies the byte tunnel in-process.
- `baleobala tunnel` runs the full IP tunnel over Bale LiveKit.
- `baleobala bale-call` uses Bale LiveKit credentials directly.
- `baleobala bale-tunnel` runs the tunnel over Bale LiveKit.
- `baleobala bale-proxy` exposes a local SOCKS5/HTTP CONNECT endpoint over the tunnel.
- `baleobala auth`, `pair`, `relay`, and `vpn` manage the product control plane and saved state.
- On Linux, `baleobala vpn up` defaults to the native `linux-tun` backend.
- On macOS, `baleobala vpn agent install` creates a LaunchAgent that can start `vpn up` automatically at login.

The Bale-backed commands still need either explicit LiveKit credentials or the Bale auth flow that is being finished for the desktop product.

## Linux audio path

The current Linux audio transport still uses the virtual-mic helper.

```bash
baleobala virtmic
```

The command prints the sink/source names. Use the sink as the playback target and the source as the microphone input in your call app.

For browser or conferencing-app flows, continue to use the existing docs:

- [Web path](BALE_WEB.md)
- [Waydroid path](BALE_WAYDROID.md)

## macOS notes

The repo already treats macOS as a first-class target for the future desktop product, and the current `vpn up` command now applies system proxy settings on macOS so you can test the end-to-end path right now.

For now, macOS users should treat the current audio helper as legacy tooling and focus on the proxy/tunnel path plus the system-proxy bridge in [VPN_PLAN.md](VPN_PLAN.md).

## Verify the install

```bash
baleobala loopback "hello" "world"
baleobala tunnel-loopback
baleobala doctor
baleobala vpn status
```

If these pass, the codec and tunnel core are healthy and the local machine has the baseline dependencies it needs.

## Current troubleshooting

- If `sounddevice` cannot see your devices, install the system audio backend packages for your distro.
- If `pactl` is missing, install `pulseaudio-utils` or the PipeWire Pulse compatibility package.
- If Bale LiveKit setup fails, start by verifying the current `baleobala bale-call` path with explicit LiveKit credentials before moving to the automated auth work.
- If `baleobala doctor` reports missing `sounddevice`, re-check the Python environment that is currently active.
- If `baleobala doctor` is green but the proxy still fails, test `baleobala tunnel-loopback` first so we know the byte-tunnel core is healthy.
- If `baleobala vpn up` complains about auth, run `baleobala auth login` first and then retry with the saved session.
- If `baleobala relay enable` says there is no pairing record, create one with `baleobala pair start` and `baleobala pair accept` first.
- If a paired relay already exists locally, `baleobala vpn up` will use it automatically.
- If you want the app to come back on login on macOS, run `baleobala vpn agent install` once after pairing.
- On macOS, `vpn up` now prefers the packet-tunnel backend recorded in the saved profile. If you explicitly choose the proxy fallback, `vpn down` restores the stored system proxy settings.

## Where to go next

- [Desktop VPN Plan](VPN_PLAN.md)
- [Bale Headless Notes](BALE_HEADLESS.md)
- [Native macOS Scaffold](../native/macos/README.md)
