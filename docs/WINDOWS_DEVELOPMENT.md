# Windows Development

This runbook covers the current Windows development path for `userbot-bale`.
The main product path is still `doctor -> auth -> pair -> connect`.

## Current backend

On Windows, `userbot-bale vpn up` now defaults to `windows-proxy`.

That backend does two things:

1. Starts the in-process SOCKS5 / HTTP CONNECT listener already used by the direct proxy backend.
2. Applies a WinHTTP proxy override through `netsh winhttp` so Windows-side development traffic can route through the local listener.

This is a development path, not the final native tunnel implementation. It is the Windows equivalent of getting the control plane, proxy listener, and local routing story working end to end before a packet-tunnel-specific backend exists.

## Bootstrap

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,bale,desktop]"
userbot-bale doctor
```

`doctor` should report:

- `python` present
- `sounddevice` importable
- `PySide6` importable if you want the desktop app
- `powershell` available for the proxy-control path

## Sign-in and pairing

```powershell
userbot-bale auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
userbot-bale pair enroll --name home-relay --role client
userbot-bale pair request-access --profile-id "<profile-id>"
userbot-bale pair approve --profile-id "<profile-id>"
```

If you are working only on the local Windows proxy backend, pairing is not required when the saved profile backend is `windows-proxy` and you are exercising the direct local path.

## Start the Windows development backend

```powershell
userbot-bale vpn up
```

Expected behavior:

- the local listener binds to `127.0.0.1:1080` by default
- backend status reports `windows-proxy`
- readiness reports `mode=winhttp-system-proxy`
- `vpn down` restores the previous WinHTTP proxy state

Check status with:

```powershell
userbot-bale vpn status
netsh winhttp show proxy
```

## Development notes

- WinHTTP proxy settings affect tools that honor the WinHTTP layer; browser traffic can still depend on app-specific proxy behavior.
- The backend persists the prior WinHTTP state in the normal `userbot-bale` config directory and restores it on shutdown.
- `USERBOT_BALE_VPN_BACKEND=windows-proxy` can force the Windows backend explicitly if you are moving profiles across machines.

## Verification

Run the focused platform and control-plane tests:

```bash
pytest -q tests/test_control.py tests/test_direct_backend.py tests/test_linux_backend.py tests/test_runtime.py
```
