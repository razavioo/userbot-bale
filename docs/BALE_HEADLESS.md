# Headless Bale client (Phase 3)

The "in the heart of" integration: a Python process that joins a Bale
LiveKit call directly, pushes baleobala frames into its audio track,
and reads the other side's audio from the room. No browser, no Android
container.

## Status

| Layer                             | State           |
| --------------------------------- | --------------- |
| LiveKit audio sink/source         | **Working, tested against real LiveKit SFU** |
| `baleobala bale-call` CLI command | Working         |
| Bale endpoint bootstrap           | Working (`BaleApiClient.bootstrap()`)         |
| Bale MTProto transport + auth     | Scaffolded, needs mitmproxy capture to finish |

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

## Today: use the CLI with a pre-obtained token

Capture a real call's LiveKit URL + token once via mitmproxy (any
Bale client will do — Waydroid, real phone, or web), then replay with
no app in the loop. The URL + token are typically valid for the
lifetime of the call.

```bash
# sender
baleobala bale-call send \
  --livekit-url wss://meet.bale.ai/rtc \
  --livekit-token eyJhbGciOiJIUzI1NiIs... \
  --identity baleobala-sender \
  --text "سلام"

# receiver
baleobala bale-call recv \
  --livekit-url wss://meet.bale.ai/rtc \
  --livekit-token eyJhbGciOiJIUzI1NiIs... \
  --identity baleobala-receiver
```

Both sides connect to the same LiveKit room (encoded in the token),
publish an audio track, and subscribe to the other side's track. The
sender feeds its baleobala-encoded waveform into the track; the
receiver's ggwave decoder consumes what it gets back.

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
