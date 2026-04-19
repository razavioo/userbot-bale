# Setup Guide

End-to-end walkthrough for two machines: **A** (sender) and **B** (receiver),
connected via any voice/video call app (Zoom, Meet, WhatsApp, Discord, …).

## Prerequisites

Both machines: Linux with PipeWire or PulseAudio, Python ≥ 3.9.

```bash
sudo apt install -y pulseaudio-utils libportaudio2
git clone https://github.com/razavioo/baleobala.git
cd baleobala
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

If `libportaudio2` is unavailable via apt (restricted mirrors), install
the `.deb` directly:

```bash
wget http://archive.ubuntu.com/ubuntu/pool/universe/p/portaudio19/libportaudio2_19.6.0-1.1_amd64.deb
sudo dpkg -i libportaudio2_19.6.0-1.1_amd64.deb
```

Verify the install:

```bash
baleobala loopback "hello" "world"
```

All messages should print `OK`.

## Machine A — Sender

### 1. Create the virtual microphone (terminal 1, keep open)

```bash
baleobala virtmic
```

This registers two PipeWire/PulseAudio modules: a null-sink `baleobala_sink`
and a remap-source `baleobala`. `Ctrl-C` tears them down cleanly.

### 2. Select the virtual mic in the call app

Open the call app **after** terminal 1 is running. In the app's audio
settings, select **Baleobala Virtual Mic** as the microphone.

Call apps cache the device list at launch; if the app was already open
when you ran `virtmic`, fully quit and relaunch.

### 3. Disable noise suppression

ML-based noise suppression shreds FEC-encoded tones. Configure before
the call:

| App     | Setting                                                           |
| ------- | ----------------------------------------------------------------- |
| Zoom    | Audio → Advanced: Original Sound **ON**, Noise Suppression **Low**, High Fidelity Music Mode **ON** |
| Meet    | Settings → Audio: Noise Cancellation **OFF**                      |
| Discord | Voice & Video: Noise Suppression **OFF**, Echo Cancellation **OFF** if tolerable |

### 4. Send messages (terminal 2)

```bash
baleobala send --device baleobala_sink
```

Each line you type (and press Enter) is framed, encoded, and played into
the virtual sink. The call app picks it up as microphone input and
transports it to B over Opus.

Non-interactive sources work the same way:

```bash
cat messages.txt | baleobala send --device baleobala_sink
echo "one-off" | baleobala send --device baleobala_sink
```

## Machine B — Receiver

### 1. Join the call

Speakers or headphones on, moderate volume. No virtual mic is needed on B.

### 2. Choose the capture source

Two options:

| Source                    | Quality | Notes                                       |
| ------------------------- | ------- | ------------------------------------------- |
| Default sink's `.monitor` | Best    | Taps the speaker output digitally; zero ambient noise |
| Physical microphone       | Worse   | Speaker → air → mic; picks up room noise    |

Monitor capture is strongly preferred. Find the current default sink:

```bash
MONITOR=$(pactl get-default-sink).monitor
echo "$MONITOR"
```

### 3. Receive

```bash
baleobala recv --device "$MONITOR"
```

Each completed message prints on its own line, in order, as soon as it
is decoded — no buffering across messages.

## Single-machine loopback test

Before a real call, confirm the full pipeline on one host:

```bash
# terminal 1
baleobala virtmic

# terminal 2
baleobala recv --device baleobala

# terminal 3
echo "loopback works" | baleobala send --device baleobala_sink
```

Terminal 2 prints `loopback works`.

## Protocol tuning

`--protocol` flag, default `fast`:

| Value     | Throughput | Use when                                                  |
| --------- | ---------- | --------------------------------------------------------- |
| `normal`  | ~ 8 B/s    | `fast` drops packets, or noise suppression cannot be disabled |
| `fast`    | ~16 B/s    | Default. Recommended for VoIP                             |
| `fastest` | ~32 B/s    | Noise suppression is off on both sides                    |

Both endpoints must use the same protocol.

## Troubleshooting

**Virtual mic missing from the app's device list.** The app cached the
list at launch. Quit fully and relaunch.

**Receiver prints nothing.** Run the loopback test above first. If it
works, the call-path is the issue: check noise suppression is off, drop
to `--protocol normal`, and confirm B is capturing the right device
(`baleobala devices` lists everything sounddevice sees).

**`ALSA lib pcm.c: underrun occurred` on send.** Cosmetic PortAudio
warnings; payload is not affected.

**Messages appear to be missing on B.** The receiver deduplicates
messages with identical 16-bit `msg_id`. Sender sessions start at a
random id by default; if you pinned `start_msg_id` in tests, two
concurrent senders could collide. Use different starts or restart the
receiver.

**B captures its own mic instead of the call.** Pass
`--device $(pactl get-default-sink).monitor` explicitly.
