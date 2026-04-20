# Desktop VPN Delivery Plan

## Summary

The product target is a desktop-first VPN experience for Linux and macOS that uses Bale as the carrier but hides that complexity from the user.

The first release should feel like a normal VPN:

- install the app,
- sign in with Bale,
- pair a relay once,
- connect,
- and route all device traffic through the tunnel.

The current repo already has the tunnel, proxy, and carrier split. The remaining work is to finish the auth/pairing layer, keep the macOS packet-tunnel path as the default system-VPN backend, preserve the proxy bridge as fallback/debug mode, and package the result for easy install.

The repo now also has a first-pass product control plane:

- `auth` stores a Bale session locally,
- `pair` manages relay pairing records,
- `relay` saves relay runtime settings,
- and `vpn` orchestrates the current managed proxy-backed path.

That control plane is the bridge between the current proxy tunnel and the future native Linux/macOS VPN backends.

On macOS, the default backend is now `direct`: an in-process SOCKS5 + HTTP CONNECT listener bound to `127.0.0.1:1080`, combined with `networksetup`-driven system proxy settings. `baleobala vpn up` produces a working system-wide proxy on one machine with no Apple Developer Team ID, no paired remote peer, and no JWT. The listener is bound before the system proxy is flipped, and unwound cleanly on `vpn down` or crash (atexit + signal handlers perform a best-effort restore).

The `proxy` backend remains available for users who have a paired Bale relay reachable over LiveKit: it bridges the same local SOCKS5 endpoint through the audio-carrier tunnel to a remote peer. The `packet-tunnel` backend and the Swift scaffold under `native/macos/` are parked — `NEPacketTunnelProvider` requires a paid Apple Developer Team ID and the Network Extension entitlement, which we do not have and do not plan to acquire. The scaffold remains in the tree as reference for future contributors who do.

If a paired relay already exists locally, `vpn up --backend proxy` will select it automatically so the first-run flow stays simple. The macOS release also gets a LaunchAgent install path so the VPN can come back on login without extra steps.

## Implementation Goals

- Keep carrier logic, tunnel logic, and VPN logic separate.
- Treat Bale as the media/control carrier only.
- Add a desktop app as the primary UI and keep CLI commands for advanced users and automation.
- Support two release modes:
  - `VPN` as the main full-device experience.
  - `Proxy` as the fallback/debug transport.
- Make relay setup durable with saved pairing and secure credential storage.
- Remove manual JWT capture from the end-user flow.

## Release Phases

### Phase 1
- Package the current Python core cleanly.
- Refresh docs and install instructions.
- Keep proxy and tunnel modes stable and testable.
- Keep the new control-plane commands (`auth`, `pair`, `relay`, `vpn`, `doctor`) stable and easy to use.

### Phase 2
- Finish Bale phone/SMS auth and session refresh.
- Add saved relay pairing.
- Add secure credential storage per OS.
- Keep a CLI for `auth`, `pair`, `relay`, `vpn`, `proxy`, and `doctor`.

### Phase 3
- Add Linux TUN plumbing and route/DNS management.
- Add macOS packet-tunnel plumbing and route/DNS management.
- Wire the desktop app to the same service layer as the CLI.

### Phase 4
- Ship signed macOS builds.
- Ship packaged Linux builds.
- Add installer/bootstrap docs and smoke tests for first-run setup.

## Test Strategy

- Unit tests for auth, pairing, secure storage, and tunnel/proxy session behavior.
- Integration tests for client/relay pairing and reconnect.
- OS smoke tests for Linux and macOS route/DNS setup and teardown.
- End-to-end tests for proxy fallback and full VPN routing.

## Assumptions

- Linux is the first system-VPN target.
- macOS is a first-class target for the desktop product, not an afterthought.
- The user-owned relay-device model is the default release shape.
- Manual JWT is not part of the public install flow.
- Full-tunnel routing is the v1 default; split tunnel can come later.
