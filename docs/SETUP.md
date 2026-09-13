# Getting Started

This guide covers the current repo state and the easiest way to install and try it on Linux, macOS, and Windows.
The main user journey is `doctor -> auth -> pair -> connect`.

For production-facing setup, prefer `userbot-bale doctor`, `userbot-bale auth bale-login`,
`userbot-bale pair ...`, `userbot-bale relay ...`, `userbot-bale vpn ...`, and `userbot-bale gui`.
For local messaging automation, use the separate `userbot-bale userbot` and `userbot-bale mcp` surfaces.
The lower-level transport commands below remain available as engineering and debug tools.

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

For the userbot and local MCP server:

```bash
pip install -e ".[dev,bale,mcp]"
```

If you want the desktop-app stack that is planned for the VPN release:

```bash
pip install -e ".[dev,bale,desktop]"
```

## What works today

- `userbot-bale auth`, `pair`, `relay`, and `vpn` manage the product control plane and saved state.
- `userbot-bale userbot` persists inbound text events locally and dispatches explicit plugins.
- `userbot-bale mcp serve` exposes allowlist-gated messaging tools over stdio.
- On Linux, `userbot-bale vpn up` defaults to the native `linux-tun` backend.
- On macOS, `userbot-bale vpn agent install` creates a LaunchAgent that can start `vpn up` automatically at login.

## Advanced And Debug Tools

- `userbot-bale loopback` verifies codec/framing in-process.
- `userbot-bale tunnel-loopback` verifies the byte tunnel in-process.
- `userbot-bale tunnel` runs the full IP tunnel over Bale LiveKit.
- `userbot-bale bale-call` uses Bale LiveKit credentials directly.
- `userbot-bale bale-tunnel` runs the tunnel over Bale LiveKit.
- `userbot-bale bale-proxy` exposes a local SOCKS5/HTTP CONNECT endpoint over the tunnel.

These commands use the authenticated Bale WebSocket path when supplied a saved session or an
explicit JWT. They remain lower-level transport tools and are not the primary first-run path.

## Linux audio path

The current Linux audio transport still uses the virtual-mic helper.

```bash
userbot-bale virtmic
```

The command prints the sink/source names. Use the sink as the playback target and the source as the microphone input in your call app.

For browser or conferencing-app flows, continue to use the existing docs:

- [Web path](BALE_WEB.md)
- [Waydroid path](BALE_WAYDROID.md)

## macOS notes

The repo already treats macOS as a first-class target for the desktop product, and the packet-tunnel scaffold now consumes shared route and DNS configuration from the same profile data as the CLI.

For now, macOS users should treat the proxy path as fallback/debug mode and the packet-tunnel path
as the primary release path described in [Architecture](ARCHITECTURE.md) and [VPN](VPN.md).

## Windows notes

Windows now has a first-class development path through the shared CLI and GUI flow. The default backend is `windows-proxy`, which starts the local direct proxy listener and applies WinHTTP proxy settings for development traffic.

Use the Windows-specific runbook in [WINDOWS_DEVELOPMENT.md](WINDOWS_DEVELOPMENT.md) for environment setup, proxy behavior, and validation steps.

## Verify the install

```bash
userbot-bale doctor
userbot-bale auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
userbot-bale pair enroll --name home-relay
userbot-bale vpn status
```

If these pass, the machine is ready for the main sign-in, pairing, and secure-connection path.

## Current troubleshooting

- If `sounddevice` cannot see your devices, install the system audio backend packages for your distro.
- If `pactl` is missing, install `pulseaudio-utils` or the PipeWire Pulse compatibility package.
- If Bale WebSocket or LiveKit setup fails, confirm `userbot-bale auth status` first, then use the
  relevant VPN or call command with verbose logging. Phone/SMS login is already supported through
  the browser and gRPC-Web flows.
- If `userbot-bale doctor` reports missing `sounddevice`, re-check the Python environment that is currently active.
- If `userbot-bale doctor` is green but the proxy still fails, test `userbot-bale tunnel-loopback` first so we know the byte-tunnel core is healthy.
- If `userbot-bale vpn up` complains about auth or your stored JWT has expired, run `userbot-bale auth bale-login --phone ... --save` first and then retry with the saved session.
- If `userbot-bale relay enable` says there is no pairing record, create one with `userbot-bale pair enroll`, then request and approve access first.
- If a paired relay already exists locally, `userbot-bale vpn up` will use the most recently used paired relay automatically.
- If you want the app to come back on login on macOS, run `userbot-bale vpn agent install` once after pairing.
- On macOS, `vpn up` now prefers the packet-tunnel backend recorded in the saved profile. If you explicitly choose the proxy fallback, `vpn down` restores the stored system proxy settings.
- For messaging automation, approve each destination first with `userbot-bale userbot allow-peer <peer_id>`.
  MCP runs only over stdio and exposes messages, dialogs, and sends for approved peers.

## Where to go next

- [VPN Transport](VPN.md)
- [Userbot and MCP](USERBOT.md)
- [Bale Headless Notes](BALE_HEADLESS.md)
- [Native macOS Scaffold](../native/macos/README.md)
