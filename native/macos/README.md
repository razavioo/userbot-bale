# Baleobala macOS Native Integration

This directory holds the native macOS companion app and packet-tunnel extension scaffold, plus the shared configuration and transport contract that the Python control plane depends on.

The native app is the system-control companion: it installs the packet-tunnel profile into System Settings, shows tunnel readiness, and starts or stops the macOS tunnel after sign-in and pairing are already handled in the shared CLI/Qt flow.

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

For a Release archive suitable for distribution:

```bash
./scripts/build-macos.sh archive
```

To export a signed archive for direct distribution:

```bash
./scripts/build-macos.sh export
```

To notarize and staple the exported app:

```bash
./scripts/build-macos.sh notarize
./scripts/build-macos.sh staple
```

To run the full release pipeline:

```bash
./scripts/build-macos.sh release
```

The release pipeline expects real signing, provisioning, and notarization inputs from the local Apple Developer environment. The repo ships the command surface and validation; it does not store certificates, provisioning profiles, or notarization credentials.

## Acceptance

Run the repo-side native acceptance checks with:

```bash
./scripts/acceptance-macos-native.sh
```

That script validates:

- app-group consistency across Swift constants and entitlements,
- packet-tunnel bundle identifier consistency,
- network-extension entitlement presence on the packet-tunnel target,
- presence of versioned control/status handling in the packet-tunnel provider,
- local unsigned Xcode compilation of the macOS targets.

Run the release-gate checks with real signing inputs configured:

```bash
./scripts/acceptance-macos-native.sh release
```

Release acceptance additionally fails when:

- the effective `DEVELOPMENT_TEAM` is still empty,
- app or packet-tunnel provisioning inputs are missing,
- code-sign identity is missing,
- the direct-distribution export options plist is missing or invalid.

## Direct Distribution Runbook

The supported ship-ready path is direct distribution outside the Mac App Store.

### 1. Apple-side prerequisites

- Create Developer ID signing assets for the macOS app.
- Create provisioning profiles for both `com.baleobala.app` and `com.baleobala.app.packet-tunnel`.
- Enable the app group `group.com.baleobala.vpn` for both targets.
- Enable the `packet-tunnel-provider` Network Extension entitlement for the packet-tunnel target.
- Configure a notarytool keychain profile on the release machine.

### 2. Local release inputs

Set these before running any signed build or release command:

```bash
export MACOS_DEVELOPMENT_TEAM=YOURTEAMID
export MACOS_CODE_SIGN_IDENTITY="Developer ID Application: Your Name (YOURTEAMID)"
export MACOS_APP_PROFILE_SPECIFIER="Baleobala Direct App"
export MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER="Baleobala Packet Tunnel"
export MACOS_NOTARY_PROFILE="baleobala-notary"
```

`EXPORT_OPTIONS_PLIST` defaults to `native/macos/ExportOptions.direct.plist`. The build script expands that template into a generated plist with the resolved team and provisioning-profile values at export time. Override it only when the release machine needs a different direct-distribution export configuration.

### 3. Repo validation and release build

Validate the release inputs:

```bash
./scripts/build-macos.sh validate-release-env
./scripts/acceptance-macos-native.sh release
```

Create the distributable app:

```bash
./scripts/build-macos.sh release
```

Artifacts land under `build/macos/`:

- `archive/Baleobala.xcarchive`
- `export/Baleobala.app`
- `notary/submission.json`
- `release-metadata.txt`

### 4. Clean-machine release validation

Install the stapled exported app on a clean macOS machine and verify:

1. first install succeeds without Xcode present,
2. `NETunnelProviderManager` installs the profile,
3. enable, connect, disconnect, and relaunch all work,
4. upgrading from the previous shipped app preserves or safely refreshes the profile,
5. uninstall removes app state without leaving a stuck tunnel profile.

### 5. Runtime validation during a real tunnel session

During a real signed session, verify:

- the packet tunnel connects to the same carrier socket contract the Python runtime exposes,
- route behavior matches the configured included and excluded route plan,
- DNS servers and search domains applied by the extension match the shared tunnel profile,
- teardown restores the machine to a clean post-disconnect state,
- Python-side probe or status output agrees with the native app’s connected state.

## Relay Provisioning

Relay pairing is now modeled as a provisioning workflow instead of a JSON file exchange:

1. Enroll the relay:
   `baleobala pair enroll --name home-relay --role client`
2. Request access for the current device:
   `baleobala pair request-access --profile-id <id>`
3. Approve the pending request from an authorized owner session:
   `baleobala pair approve --profile-id <id>`
4. Sync local state and credentials when needed:
   `baleobala pair sync --profile-id <id>`

The older `export-request`, `accept-request`, and `apply-response` commands remain debug-only compatibility paths.

## Local Run

For a quick developer loop on macOS, run:

```bash
./scripts/run-macos.sh
```

That builds the Debug app bundle with signing disabled for local development and opens the resulting `Baleobala.app`.
