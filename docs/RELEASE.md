# Release Notes and Packaging

This repo now has the source pieces needed for signed macOS builds and packaged Linux builds.

For production packaging and operator docs, expose `baleobala` as the single entrypoint and center
the user flow on `doctor`, `auth bale-login`, `pair`, `relay`, `vpn`, and `gui`. Legacy and low-level
transport commands remain available for compatibility or debugging, but should not be presented as
parallel first-run paths.

The preferred first-run story should always read as `doctor -> auth -> pair -> connect`.

## macOS direct-distribution release

The supported macOS shipping path is direct distribution outside the App Store. Use the checked-in release tooling under `native/macos/` and `scripts/build-macos.sh`, not an ad hoc Xcode-only flow.

Required release inputs:

- `MACOS_DEVELOPMENT_TEAM`
- `MACOS_CODE_SIGN_IDENTITY`
- `MACOS_APP_PROFILE_SPECIFIER` or `MACOS_APP_PROFILE_UUID`
- `MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER` or `MACOS_PACKET_TUNNEL_PROFILE_UUID`
- `MACOS_NOTARY_PROFILE`

Optional inputs when the default bundle namespace is not available in your Apple Developer team:

- `MACOS_APP_BUNDLE_ID`
- `MACOS_PACKET_TUNNEL_BUNDLE_ID`
- `MACOS_APP_GROUP_IDENTIFIER`

Validate and build with:

```bash
./scripts/build-macos.sh validate-release-env
./scripts/acceptance-macos-native.sh release
./scripts/build-macos.sh release
```

The default export configuration is `native/macos/ExportOptions.direct.plist`, which is set up for `developer-id` export. The packet-tunnel extension consumes the route and DNS configuration from the shared tunnel profile, so the app and extension stay in sync.

Ship only after clean-machine validation confirms:

1. first install of the stapled app succeeds,
2. profile install/update/remove works through `NETunnelProviderManager`,
3. connect/disconnect/relaunch behavior is stable,
4. route, DNS, and teardown behavior match Python runtime probes during a real signed tunnel session.

## Linux packaged build

For Linux, package the Python application as an installable wheel or distro-native bundle:

```bash
python3 -m build
```

Ship the wheel alongside system packages for:

- `sounddevice`
- `numpy`
- `ggwave`
- `PySide6` when the desktop app is included

## Bootstrap checklist

- `baleobala doctor` passes on the target machine.
- `baleobala auth bale-login --phone ... --save` stores auth locally and expired sessions are treated as missing.
- A relay pairing exists or can be created on first run.
- `vpn up` or `gui` starts the correct platform connection flow.
- The first-run smoke tests pass in CI before publishing.
