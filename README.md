# baleobala

`baleobala` is a CLI and GUI project for Bale login, pairing, and tunnel/proxy flows.

## Install

Create a virtual environment and install the Bale and GUI dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,bale,desktop]"
```

If `python` is not on `PATH` in your environment, use `./.venv/bin/python`.

## Quick Start

```bash
baleobala doctor
baleobala bale-auth --phone +98912xxxxxxx
baleobala gui
```

- `doctor`: checks local readiness and required dependencies.
- `bale-auth`: runs the phone/SMS login flow in the terminal and returns a JWT.
- `gui`: launches the Qt app for login and connection flows.

## Login Modes

Headless terminal login:

```bash
baleobala bale-auth --phone +98912xxxxxxx
```

Visible browser login flow:

```bash
BALE_HEADLESS=0 ./.venv/bin/python -m baleobala.cli gui
```

- `BALE_HEADLESS=0`: opens Chromium visibly so you can watch the Bale login flow.
- `BALE_HEADLESS=1` or unset: keeps the browser headless.
- In this repo, the most reliable non-headless form is `./.venv/bin/python -m baleobala.cli gui`.

## Common Commands

```bash
baleobala auth status
baleobala vpn status
baleobala vpn up
baleobala vpn down
baleobala pair start --name home-relay --role client
baleobala pair accept --code "<pair-code>"
baleobala relay status
```

- `auth status`: shows the stored auth/session state.
- `vpn status`: shows the current control-plane and backend state.
- `vpn up`: starts the active VPN/proxy backend.
- `vpn down`: stops the active backend and cleans up state.
- `pair start`: creates a new pairing record.
- `pair accept`: accepts and stores a pairing code.
- `relay status`: shows saved relay settings.

## Transport Commands

```bash
baleobala loopback "hello" "world"
baleobala tunnel-loopback
baleobala send --text "hello"
baleobala recv
```

- `loopback`: runs an in-process codec/framing self-test without real audio devices.
- `tunnel-loopback`: runs an in-process byte-tunnel self-test.
- `send`: encodes text and writes it to the current audio sink.
- `recv`: reads audio input and prints decoded messages.

## When To Use What

- Use `bale-auth` when you only need a JWT.
- Use `BALE_HEADLESS=0 ... gui` when you want to watch the real Bale login flow.
- Use `doctor` before debugging environment issues.
- Use `vpn up` when you want to bring up the current tunnel/proxy path.

## Docs

- [Getting Started](docs/SETUP.md)
- [Install and Bootstrap](docs/INSTALL.md)
- [Bale Headless Notes](docs/BALE_HEADLESS.md)
- [Native macOS Scaffold](native/macos/README.md)
