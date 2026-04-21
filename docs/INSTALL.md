# Install and Bootstrap

This is the shortest path to try baleobala from a fresh checkout.

## Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
baleobala doctor
baleobala loopback "hello"
baleobala tunnel-loopback
```

For the native VPN path:

```bash
baleobala vpn up
```

On Linux that now defaults to the `linux-tun` backend and uses the native route/DNS helper.

## macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,desktop]"
baleobala doctor
```

Then open `native/macos/` in Xcode and configure:

- the app target,
- the packet-tunnel extension target,
- the app-group entitlement,
- and signing for your Apple Developer Team.

The repo now includes `native/macos/Baleobala.xcodeproj`, which is the project file you should open.
Use `scripts/open-macos-xcode.sh` to open it, `scripts/build-macos.sh` for a terminal build, and `scripts/run-macos.sh` to build and launch the app once Xcode is set up.

The macOS packet tunnel reads its route and DNS plan from `BaleTunnelConfiguration`, so the same profile data drives both the app and the extension.

## First Run

1. Start with `baleobala auth login` or the Qt sign-in flow.
2. Save a relay pairing with `baleobala pair start` and `baleobala pair accept`.
3. Run `baleobala relay enable` if you want the relay profile saved.
4. Run `baleobala vpn up`.

## Verification

```bash
pytest -q tests/test_control.py tests/test_linux_backend.py tests/test_runtime.py
```
