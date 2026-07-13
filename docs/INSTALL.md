# Install and Bootstrap

This is the shortest path to try baleobala from a fresh checkout.
The main user journey is `doctor -> auth -> pair -> connect`.

The production-facing VPN CLI surface is `baleobala doctor`, `auth`, `pair`, `relay`, `vpn`, and `gui`.
Messaging automation is available through `baleobala userbot` and the optional `baleobala mcp serve`.
Direct module execution and lower-level transport commands remain available as fallback/debug tools.

## Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
baleobala doctor
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
baleobala vpn status
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
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
```

Then open `native/macos/` in Xcode and configure:

- the app target,
- the packet-tunnel extension target,
- the app-group entitlement,
- and signing for your Apple Developer Team.

The repo now includes `native/macos/Baleobala.xcodeproj`, which is the project file you should open.
Use `scripts/open-macos-xcode.sh` to open it, `scripts/build-macos.sh` for a terminal build, and `scripts/run-macos.sh` to build and launch the app once Xcode is set up.
For a distributable direct build, follow the runbook in `native/macos/README.md` and validate with `./scripts/acceptance-macos-native.sh release` before publishing.

The macOS packet tunnel reads its route and DNS plan from `BaleTunnelConfiguration`, so the same profile data drives both the app and the extension.

## Windows

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,desktop]"
baleobala doctor
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
baleobala vpn status
```

For the current Windows development path:

```powershell
baleobala vpn up
```

On Windows that now defaults to the `windows-proxy` backend. It starts the in-process SOCKS5/HTTP CONNECT listener and points WinHTTP at it for development traffic.

## First Run

1. Start with `baleobala doctor`.
2. Sign in with `baleobala auth bale-login --phone ... --method browser --save` or the Qt sign-in flow.
3. Save a relay pairing with `baleobala pair enroll`, then `pair request-access`, then `pair approve`.
4. Start the secure connection with `baleobala vpn up` or the desktop app.

## Userbot And MCP

Install the Bale and MCP extras, then explicitly allow each peer that automation may read or
message:

```bash
pip install -e ".[bale,mcp]"
baleobala userbot allow-peer 123456789
baleobala userbot run
baleobala mcp serve
```

The MCP server uses stdio, not a network listener. See [Userbot and MCP](USERBOT.md) for the
tool contract and outbound rate limit.

For engineering-only validation, `baleobala loopback` and `baleobala tunnel-loopback` remain useful,
but they are not the primary production quick-start path.

## Verification

```bash
pytest -q tests/test_control.py tests/test_linux_backend.py tests/test_runtime.py tests/test_direct_backend.py
```
