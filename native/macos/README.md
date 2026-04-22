# Baleobala macOS Native Integration

This directory holds the native macOS app and packet-tunnel extension scaffold, plus the shared configuration and transport contract that the Python control plane depends on.

The app is the control surface: it installs the packet-tunnel profile into System Settings, shows the installed profile state, and starts/stops the tunnel.

## Shape

- `BaleobalaApp/` is the user-facing macOS app that installs and controls the VPN profile.
- `BaleobalaPacketTunnel/` is the `NEPacketTunnelProvider` extension that owns system networking.
- `Shared/` contains the app-group constants, keychain helpers, tunnel manager, and Unix-socket client used by both targets.

## Design Notes

The structure follows the same split that SimpleTunnel uses:

- app target for profile setup and lifecycle control,
- packet-tunnel target for route/DNS and packet handling,
- shared code for configuration and transport wiring.

The configuration and persistence flow borrows the TunnelKit pattern:

- store the app-group identifier, tunnel metadata, route plan, and DNS plan in one place,
- keep credentials in the keychain,
- load and save `NETunnelProviderManager` through a dedicated manager type.

## Socket Wiring

The packet-tunnel extension connects to the same Unix-domain socket that the Python `CarrierTunnelService` exposes.

The agreed contract is:

- the app group container is the shared root,
- the Python runtime writes its carrier socket under that shared root,
- the packet-tunnel extension resolves the same path and bridges packets to it,
- the packet-tunnel target reads route and DNS settings from the same shared profile payload as the app target.

That keeps the macOS extension responsible for routing, while Python remains the carrier payload engine.

## Build Modes

For a quick local debug build without signing:

```bash
./scripts/build-macos.sh debug-local
```

For a normal signed build:

```bash
./scripts/build-macos.sh build
```

For an archive suitable for distribution:

```bash
./scripts/build-macos.sh archive
```

To export a signed archive, first set `EXPORT_OPTIONS_PLIST` to a valid export options plist and then run:

```bash
EXPORT_OPTIONS_PLIST=/path/to/ExportOptions.plist ./scripts/build-macos.sh export
```

The build script intentionally separates `build`, `archive`, and `export` because the last two steps require the real signing and provisioning environment from Xcode/Apple Developer tooling.

## Acceptance

Run the repo-side native acceptance checks with:

```bash
./scripts/acceptance-macos-native.sh
```

That script validates:

- app-group consistency across Swift constants and entitlements,
- packet-tunnel bundle identifier consistency,
- presence of versioned control/status handling in the packet-tunnel provider,
- local unsigned Xcode compilation of the macOS targets.

## Production Checklist

To finish real macOS shipping outside this repo, the remaining steps are:

1. Configure a real Apple Developer team, signing certificates, and provisioning profiles for both the app target and the packet-tunnel extension.
2. Enable the same app group and Network Extension entitlement in the Developer portal that the code expects locally.
3. Verify that the app target and packet-tunnel target share the same keychain-access and app-group assumptions on a real machine.
4. Archive and export a signed app from Xcode or `./scripts/build-macos.sh archive` plus `export`.
5. Install the exported app on a clean macOS machine and verify `NETunnelProviderManager` install, update, enable, connect, disconnect, and uninstall behavior.
6. Validate that route, DNS, and teardown behavior match the Python runtime status and probe output during real tunnel sessions.

## Pairing Exchange

The repo now supports a versioned pairing request/response flow on the Python side. Until a remote provisioning service exists, the practical bridge is file exchange:

1. On the initiator, create a pairing and export a request bundle:
   `baleobala pair export-request --profile-id <id>`
2. Transfer that JSON bundle to the responder.
3. On the responder, accept it and emit a response bundle:
   `baleobala pair accept-request --request-file request.json`
4. Transfer the response JSON back to the initiator.
5. On the initiator, apply it:
   `baleobala pair apply-response --response-file response.json`

That file-mediated flow is the current stand-in for a future server-backed provisioning exchange.

## Local Run

For a quick developer loop on macOS, run:

```bash
./scripts/run-macos.sh
```

That builds the Debug app bundle with signing disabled for local development and opens the resulting `Baleobala.app`.
