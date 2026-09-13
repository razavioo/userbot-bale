# Running userbot-bale over web.bale.ai (Phase 1)

The simplest working integration: open https://web.bale.ai/chat in a
browser on a Linux host that's running `userbot-bale virtmic`, and pick
the virtual mic as the site's microphone. Bale's web client uses
standard `getUserMedia`, so the PipeWire virtual source shows up
exactly like a real mic. No reverse engineering needed.

## Setup (sender side)

```bash
# Terminal 1 — create the virtual mic and leave it running
userbot-bale virtmic
#   sink   (play here):      userbot_bale_sink
#   source (mic for apps):   userbot-bale

# Terminal 2 — start a call in the browser
chromium --use-fake-ui-for-media-stream=false https://web.bale.ai/chat
# Grant mic permission, then in the site's call settings pick:
#   Microphone: Userbot Bale Virtual Mic
# Start the call with your peer.

# Terminal 3 — transmit
echo "hello bale" | userbot-bale send --device userbot_bale_sink
#   or for a longer message:
userbot-bale send --device userbot_bale_sink --text "سلام از userbot-bale"
```

## Setup (receiver side)

On the peer's machine, tap the speaker output (or the headset mic if
listening acoustically) with `userbot-bale recv`. If you want to decode
what a Linux receiver *hears* from the call, the cleanest loop is:

```bash
# Create a loopback source from the default sink's monitor so sounddevice
# can read what the call app is playing:
pactl load-module module-loopback source=@DEFAULT_MONITOR@ sink_dont_move=1
# Or more usefully, point --device directly at the monitor source:
userbot-bale recv --device alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
```

List available sources with `pactl list short sources`.

## Gotchas

### Chrome's per-origin mic selection

Chrome remembers the selected mic per origin. First time: open
https://web.bale.ai, start a call (or open the in-site mic test),
explicitly pick `Userbot Bale Virtual Mic`. After that it sticks for
the origin.

If Chrome won't show the virtual mic: make sure it was created
*before* Chrome was launched. Chrome enumerates devices at startup.
Quick fix: `pkill chromium && chromium ...` after running
`userbot-bale virtmic`.

### Firefox quirks

Firefox re-enumerates devices on every `getUserMedia` call, so the
device-created-after-browser issue doesn't bite — but
`media.navigator.permission.disabled` must stay `false` (the default)
for the mic picker to appear.

### Noise suppression

Bale web, like every WebRTC app, passes the mic through a noise
suppressor by default. GGWave's audible protocols are tuned to sound
voice-like and mostly survive NS, but `fast` and especially `fastest`
degrade noticeably when NS is aggressive. Two mitigations:

- Use `--protocol normal` (8 B/s, most robust) if `fast` drops
  fragments.
- Disable NS in the browser layer via `chrome://flags` →
  *WebRTC audio processing* → Disabled. Bale web itself may also
  pass `{ noiseSuppression: true }` to `getUserMedia`; that can be
  overridden with the Chrome extension
  [Media Devices Controller](https://chrome.google.com/webstore/)
  or by running Chrome with `--disable-features=WebRtcApm`.

### Volume

Browser auto-gain-control (AGC) will try to normalise input levels.
`userbot-bale send --volume 50` already runs below clipping; bumping
to 70–80 helps against aggressive AGC. Disable AGC with the same
`--disable-features=WebRtcApm` flag as above.

## Verifying it works before a real call

Before inviting a peer, verify locally:

1. Run `userbot-bale virtmic` in one terminal.
2. Open https://web.bale.ai/chat.
3. In the site, start a test/self call if available — or use any page
   that shows a live mic-level meter (e.g.
   https://mictests.com).
4. Run `userbot-bale send --device userbot_bale_sink --text "self test"`.
5. The meter should swing; the content is audible GGWave tones, not
   voice, so your peer will hear a brief chirp.

If the meter doesn't swing, you chose the wrong mic in the browser
settings; re-check.
