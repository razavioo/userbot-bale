# Desktop VPN Delivery Plan

## Summary

The product target is a desktop-first VPN experience for Linux and macOS that uses Bale as the carrier but hides that complexity from the user.

The first release should feel like a normal VPN:

- install the app,
- sign in with Bale,
- pair a relay once,
- connect,
- and route all device traffic through the tunnel.

The current repo already has the tunnel, proxy, and carrier split. The Linux TUN backend is now wired into the control plane. The remaining work is to finish the auth/pairing layer, keep the macOS packet-tunnel path as the default system-VPN backend, preserve the proxy bridge as fallback/debug mode, and package the result for easy install.

The repo now also has a first-pass product control plane:

- `auth` stores a Bale session locally,
- `pair` manages relay pairing records,
- `relay` saves relay runtime settings,
- and `vpn` orchestrates the current managed proxy-backed path.

That control plane is the bridge between the current proxy tunnel and the future native Linux/macOS VPN backends.

On macOS, the packet-tunnel backend is now the primary system-VPN path. The native extension consumes the shared tunnel profile, including the route and DNS plan, so the app target and packet-tunnel target stay aligned. The fallback proxy path still exists for debugging and for systems where the native path is not available.

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
- Finish macOS packet-tunnel plumbing and route/DNS management.
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
