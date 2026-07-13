# Bale Headless Notes

These notes document the Bale carrier and authenticated WebSocket client work and how they map
into the VPN, userbot, and MCP product surfaces.

## What is implemented

- Bale LiveKit credential bootstrap is wired through the WS API client.
- Phone/SMS sign-in is live through the browser and gRPC-Web paths; saved sessions are used by the
  VPN, userbot, and MCP commands.
- The userbot runtime persists text events, deduplicates them across restarts, applies an outbound
  peer allowlist and rate limit, and never auto-accepts calls.
- The MCP server is stdio-only and exposes only approved Bale peers; it cannot invoke calls, VPN,
  proxy, contact-import, or raw RPC operations.
- Audio transport is abstracted behind `AudioSink` and `AudioSource`.
- The byte tunnel, proxy transport, and carrier adapters are split into separate runtime layers.
- The CLI can exercise the carrier stack, tunnel core, and proxy fallback without the future desktop shell.
- The product control plane now covers auth, pairing, relay settings, and the current vpn/proxy orchestration path.
- Stored JWTs now carry expiry metadata; expired sessions are treated as missing so the app can ask for a fresh login.
- On macOS, the current vpn path uses saved pairing/auth and defaults to the packet-tunnel backend shape; the system-proxy bridge remains available as fallback/debug mode.
- The packet-tunnel path now has a local Unix-domain runtime socket scaffold that forwards bytes through the carrier tunnel runtime.
- macOS also has a LaunchAgent install path so the current tunnel can come back on login.
- The native app/extension scaffold now lives in `native/macos/` and uses the same app-group and socket contract that the Python runtime exposes.

## What remains for the full product

- Relay pairing should become a first-class concept instead of a manual peer-id flow.
- The desktop app should call the same service layer as the CLI.
- System VPN plumbing now has Linux TUN and a macOS packet-tunnel scaffold; the remaining work is hardening and release packaging.
- The current `vpn` command now orchestrates the shared service layer, while the packet-tunnel target owns the native macOS route/DNS settings.

## How the current headless path fits

The current headless path is useful for:

- verifying Bale LiveKit call setup,
- keeping the tunnel core testable,
- and giving us a fallback transport for early development.

It is not limited to the desktop VPN shell: the same authenticated API layer now also supports
local userbot and MCP integrations. MTProto remains experimental and is not used by those paths.

## References

- [Bale RE notes](BALE_RE_NOTES.md)
- [Getting Started](SETUP.md)
- [Userbot and MCP](USERBOT.md)
