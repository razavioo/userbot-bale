# Running userbot-bale with the Bale Android app (Phase 2)

Phase 1 (`docs/BALE_WEB.md`) handles the web client. This page covers
the Android APK running in Waydroid on the same Linux host.

## Why Waydroid

Waydroid is a lightweight Android container that uses the host's
PipeWire for audio. Because it shares the audio graph with the host,
the same `userbot-bale virtmic` that already works for web.bale.ai also
works for the in-Waydroid Bale app — no root, no modified APK.

If Waydroid refuses to run the APK (e.g. Play Integrity attestation
or a missing GApps component), fall back to a real Android phone
with `scrcpy --audio=output`. See [Fallback](#fallback-real-device).

## Setup

```bash
# One-time install
sudo apt install -y waydroid
sudo waydroid init                  # pulls LineageOS-based image + GApps
sudo systemctl start waydroid-container
waydroid session start &            # user session (opens display)

# Install Bale
waydroid app install ./bale.apk

# Launch Bale, register your phone number, complete SMS verification.
waydroid app launch ai.bale.messenger
```

Find the package name for `waydroid app launch` with
`waydroid app list | grep -i bale`.

## Wire userbot-bale into Waydroid's mic

Waydroid's audio routing honours PulseAudio environment variables.
After starting the userbot-bale virtmic on the host, restart Waydroid's
session with `PULSE_SOURCE` pointing at it:

```bash
# Terminal 1 — host
userbot-bale virtmic    # leaves running; virtual source is "userbot-bale"

# Terminal 2 — restart Waydroid session with the virtual mic
waydroid session stop
PULSE_SOURCE=userbot-bale waydroid session start &
```

In-app, start a Bale call. The app's mic is now fed by the null-sink;
whatever you play to `userbot_bale_sink` on the host reaches the peer.

```bash
# Terminal 3
userbot-bale send --device userbot_bale_sink --text "hello from waydroid"
```

## Receiving on the Waydroid side

Bale renders the incoming call audio to Waydroid's playback, which
the host sees as a monitor source. Tap it:

```bash
pactl list short sources | grep -i monitor
userbot-bale recv --device <the_waydroid_monitor_source>
```

## Fallback: real device

Some GApps-enabled Waydroid images trip Bale's Play Integrity check
and fail at login. In that case, use a real phone:

1. Install Bale on the phone as normal.
2. Connect via USB + `adb` and mirror with
   `scrcpy --audio=output --audio-source=output`.
3. On the host, the scrcpy audio shows up as a PulseAudio source named
   something like `scrcpy-audio`. Point `userbot-bale recv` at it.
4. For the outgoing direction (host → phone mic), Android does not
   expose a virtual-mic API without root or a custom
   `MediaProjection`-based app. The practical fallback is acoustic:
   hold the phone near a speaker playing the userbot-bale output. This
   is the same posture as the Zoom/Meet/WhatsApp flow the main
   README describes and it works for Bale too.

The fully-headless alternative is Phase 3 (`docs/BALE_HEADLESS.md`),
which bypasses both the browser and the Android app.

## Troubleshooting

- **Bale app doesn't see the virtual mic:** Waydroid caches PulseAudio
  info at session start. Stop and restart the session with
  `PULSE_SOURCE=userbot-bale` in the environment (above).
- **Audio is one-way:** the app's output is routed to PipeWire but
  isn't auto-visible as a separate monitor. List sources with
  `pactl list short sources` and look for one owned by the Waydroid
  app — point `userbot-bale recv --device` at its monitor.
- **Play Integrity failure:** GApps-certified Waydroid images are
  rare; fall back to the real-device flow above or move to Phase 3.
