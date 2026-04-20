# Baleobala macOS Native Scaffold

This directory holds the native macOS app and packet-tunnel extension scaffold.

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

- store the app-group identifier and tunnel metadata in one place,
- keep credentials in the keychain,
- load and save `NETunnelProviderManager` through a dedicated manager type.

## Socket Wiring

The packet-tunnel extension connects to the same Unix-domain socket that the Python `CarrierTunnelService` exposes.

The agreed contract is:

- the app group container is the shared root,
- the Python runtime writes its carrier socket under that shared root,
- the packet-tunnel extension resolves the same path and bridges packets to it.

That keeps the macOS extension responsible for routing, while Python remains the carrier payload engine.

## Next Build Step

Open this directory in Xcode, add the app and packet-tunnel targets, and point both targets at the files here.
The code is scaffolded to be split into the exact targets you would expect from a SimpleTunnel-style project.
