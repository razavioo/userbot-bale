# Release Notes and Packaging

This repo now has the source pieces needed for signed macOS builds and packaged Linux builds.

## macOS signed build

Use Xcode to archive both native targets under `native/macos/`:

1. Open the macOS project in Xcode.
2. Set the team, signing identity, and app-group entitlement.
3. Archive the app and packet-tunnel targets.
4. Export the archive with the signed distribution profile.
5. Notarize the app bundle if you are distributing outside the App Store.

The packet-tunnel extension now consumes the route and DNS configuration from the shared tunnel profile, so the app and extension stay in sync.

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
- Auth is stored locally and expired sessions are treated as missing.
- A relay pairing exists or can be created on first run.
- `vpn up` starts the correct platform backend.
- The first-run smoke tests pass in CI before publishing.

