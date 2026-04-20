# Bale Headless Notes

These notes document the Bale carrier work and how it maps into the desktop VPN/product plan.

## What is implemented

- Bale LiveKit credential bootstrap is wired through the WS API client.
- Audio transport is abstracted behind `AudioSink` and `AudioSource`.
- The byte tunnel, proxy transport, and carrier adapters are split into separate runtime layers.
- The CLI can exercise the carrier stack, tunnel core, and proxy fallback without the future desktop shell.
- The product control plane now covers auth, pairing, relay settings, and the current vpn/proxy orchestration path.
- On macOS, the current vpn path uses saved pairing/auth and defaults to the packet-tunnel backend shape; the system-proxy bridge remains available as fallback/debug mode.
- The packet-tunnel path now has a local Unix-domain runtime socket scaffold that forwards bytes through the carrier tunnel runtime.
- macOS also has a LaunchAgent install path so the current tunnel can come back on login.
- The native app/extension scaffold now lives in `native/macos/` and uses the same app-group and socket contract that the Python runtime exposes.

## What remains for the full product

- Bale phone/SMS auth and token refresh need to be completed in the transport/auth layer.
- Relay pairing should become a first-class concept instead of a manual peer-id flow.
- The desktop app should call the same service layer as the CLI.
- System VPN plumbing still needs Linux TUN and macOS packet-tunnel implementations.
- The current `vpn` command is a managed proxy-backed bridge, not the final native packet tunnel.

## How the current headless path fits

The current headless path is useful for:

- verifying Bale LiveKit call setup,
- keeping the tunnel core testable,
- and giving us a fallback transport for early development.

It is not the final user-facing product shell. The final release target is the desktop VPN flow described in [VPN_PLAN.md](VPN_PLAN.md).

## References

- [Bale RE notes](BALE_RE_NOTES.md)
- [Getting Started](SETUP.md)
