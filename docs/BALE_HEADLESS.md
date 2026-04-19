# Headless Bale client (Phase 3)

The "in the heart of" integration: a Python process that joins a Bale
LiveKit call directly, pushes baleobala frames into its audio track,
and reads the other side's audio from the room. No browser, no Android
container.

## Status

| Layer                             | State           |
| --------------------------------- | --------------- |
| LiveKit audio sink/source         | **Live-verified**                             |
| `baleobala bale-call` CLI command | **Live-verified** (both send and answer)      |
| Bale endpoint bootstrap           | **Live-verified** (`BaleApiClient.bootstrap()`) |
| Bale WS transport + RPC           | **Live-verified** (`BaleApiClient`)           |
| StartCall → LiveKit credentials   | **Live-verified** (creds in RPC response)     |
| Phone/SMS auth flow               | Pending — needs another capture of the web gRPC-Web POSTs |

The LiveKit half is complete: given a room URL + access token, the
code in [src/baleobala/bale/livekit_backend.py](../src/baleobala/bale/livekit_backend.py)
wraps `livekit-rtc` and exposes baleobala's `AudioSink` / `AudioSource`
protocols. Plugging it into `Transmitter` / `Receiver` is automatic.

The Bale half (getting that URL + token) needs the Nasim-MTProto
transport to be implemented. See
[BALE_RE_NOTES.md](./BALE_RE_NOTES.md) for the RE plan.

## Install

```bash
pip install -e ".[bale]"          # adds livekit-rtc on top of the base deps
```

## Three usage modes

### 1. Place a call from Python (fully headless)

Given a Bale `access_token` JWT (see *Getting the JWT* below), call a
peer by `user_id`:

```bash
baleobala bale-call send \
  --peer-id 460260975 \
  --bale-jwt-file /tmp/bale_jwt.txt \
  --text "hello from headless Python"
```

This opens the WS to `next-ws.bale.ai`, invokes
`bale.meet.v1.Meet/StartCall` with the peer, extracts the LiveKit
URL/token/room from the server's response, joins the LiveKit room,
and transmits baleobala audio.

### 2. Answer an incoming call

Run in listen mode. When any caller rings you, Bale pushes the
LiveKit credentials to the session and baleobala joins the room:

```bash
baleobala bale-call recv \
  --answer \
  --bale-jwt-file /tmp/bale_jwt.txt \
  --answer-timeout 120
```

### 3. Debug with pre-captured credentials

If you already have a LiveKit URL + token (e.g. sniffed via mitmproxy),
skip the Bale auth dance:

```bash
baleobala bale-call send \
  --livekit-url wss://meet-gwe.ble.ir \
  --livekit-token eyJhbGciOi... \
  --text "سلام"
```

## Getting the JWT

Until the phone-auth RPC port is written (see below), the easiest way
to get an `access_token` is:

1. `mitmdump -p 8080 -w /tmp/capture.mitm` on the host.
2. Open Chrome with `--proxy-server=127.0.0.1:8080` at
   `https://web.bale.ai`, log in with your phone + SMS.
3. Extract the `access_token` cookie from the `Set-Cookie` header on
   the `/bale.auth.v1.Auth/ValidateCode` response. A one-liner:

   ```bash
   mitmdump -nr /tmp/capture.mitm -s - <<'EOF'
   def response(flow):
       if "ValidateCode" in flow.request.path:
           for k, v in flow.response.headers.items():
               if k.lower() == "set-cookie" and "access_token" in v:
                   print(v.split(";", 1)[0].split("=", 1)[1])
   EOF
   ```

4. Save to `/tmp/bale_jwt.txt` (or set `BALE_JWT=...`).

The JWT's `exp` claim is ~1 year from issue, so this is a one-time
setup per account.

## How the LiveKit backend works

```
┌──────────────────┐   capture_frame (10 ms,   ┌──────────────┐
│  Transmitter     │ → 480 samples, int16,  →  │ rtc.AudioSrc │
│  + LiveKitSink   │   48 kHz, 1-channel)      │ → LiveKit SFU│
└──────────────────┘                           └──────────────┘
                                                      │
                                                      ▼
┌──────────────────┐   AudioStream (int16      ┌──────────────┐
│  Receiver        │ ← frames, any rate, ←     │ rtc.AudioSrc │
│  + LiveKitSource │   resampled to 48k f32)   │ ← LiveKit SFU│
└──────────────────┘                           └──────────────┘
```

The livekit-rtc SDK is asyncio-based. `LiveKitSession` runs its own
event loop on a background thread and bridges to the synchronous
`AudioSink.play()` / `AudioSource.iter_blocks()` API via thread-safe
queues. Consumers (Transmitter, Receiver) don't see the asyncio.

## Tomorrow: end-to-end via Bale account

Once `baleobala.bale.api.BaleApiClient.fetch_livekit_credentials` is
implemented, the CLI will grow a no-token mode that takes a peer's
user/phone, issues `RequestStartLiveKitCall`, and uses the returned
URL + token automatically:

```bash
# future form
baleobala bale-call send --peer +989120000000 --text "hello"
```

See [BALE_RE_NOTES.md](./BALE_RE_NOTES.md) for the RE milestones that
gate this feature.

## Legal / ethical scope

This tool is for the user's own calls between their own accounts.
Reverse engineering for interoperability is permitted under DMCA
§1201(f) (US) and the EU Software Directive Art 6. Do not use it to:

- Impersonate another user or join calls you were not invited to.
- Automate Bale account creation at scale.
- Bypass Bale's anti-abuse controls (flood-wait, rate-limits).

The scaffolded code intentionally does not implement account
registration automation — only logging into an existing account the
user already controls.
