# Baleobala macOS Native Scaffold

This directory holds the native macOS app and packet-tunnel extension scaffold.

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

## Next Build Step

Open `native/macos/Baleobala.xcodeproj` in Xcode, then archive the `Baleobala` app target with the `BaleobalaPacketTunnel` extension embedded inside it.
The project is scaffolded to be split into the exact targets you would expect from a SimpleTunnel-style project.

## Local Run

For a quick developer loop on macOS, run:

```bash
./scripts/run-macos.sh
```

That builds the Debug app bundle with signing disabled for local development and opens the resulting `Baleobala.app`.
