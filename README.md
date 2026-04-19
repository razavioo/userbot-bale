# baleobala

Acoustic data bridge over voice/video calls. Creates a virtual microphone
on Linux, encodes text into audible packets with [GGWave], plays them
into a live Zoom / Google Meet / WhatsApp call, and decodes each packet
independently on the receiver — so streamed messages show up one by one
as soon as they arrive, not when the whole stream finishes.

Why the audible band and not ultrasound? Voice codecs (Opus, the one
Zoom/Meet/WhatsApp all use) aggressively suppress non-voice signals and
cut everything above ~8 kHz in narrowband mode. GGWave's audible
protocols are tuned to sound voice-like and carry Reed-Solomon FEC,
which is why they survive real VoIP conditions.

## Architecture

```
  ┌────────────┐    ┌───────────┐    ┌───────────────┐    ┌──────────┐
  │  text      │ ─▶ │  framing  │ ─▶ │   ggwave      │ ─▶ │ PipeWire │ ═╗
  │  stream    │    │  8-byte   │    │  encode       │    │ null-sink│  ║
  │            │    │  header + │    │  (audible)    │    │          │  ║
  │            │    │  CRC-8    │    │               │    │          │  ║
  └────────────┘    └───────────┘    └───────────────┘    └──────────┘  ║
                                                                         ║
                                                           Zoom / Meet ══╝
                                                           picks up
                                                           "VirtualMic"
                                                           as microphone
                                                                ║
                                                                ▼
                                                   (transported by Opus)
                                                                ║
                                                                ▼
  ┌────────────┐    ┌───────────┐    ┌───────────────┐    ┌──────────┐
  │  display   │ ◀─ │ reassembl │ ◀─ │   ggwave      │ ◀─ │ speaker  │
  │  per msg   │    │  + order  │    │  streaming    │    │ / mic    │
  │            │    │  + dedup  │    │  decode       │    │ capture  │
  └────────────┘    └───────────┘    └───────────────┘    └──────────┘
```

## Installation

```bash
cd /home/razavioo/StudioProjects/baleobala

# One-time system deps (Debian/Ubuntu)
sudo apt install -y pulseaudio-utils libportaudio2

# Python env
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick start

```bash
# 1. Create the virtual mic (keeps running until Ctrl-C)
baleobala virtmic
# or, from a shell script:
./scripts/setup-virtmic.sh

# 2. In your call app (Zoom/Meet/etc), select "Baleobala Virtual Mic"
#    as the input device.

# 3. In another terminal, send messages (one per line from stdin):
baleobala send --device baleobala_sink
> hello world
> streaming chunk 1
> streaming chunk 2

# 4. On the receiving machine, capture and print:
baleobala recv
```

### Self-test (no audio hardware required)

```bash
baleobala loopback "hello" "streaming test" "unicode: سلام"
pytest
```

## CLI reference

| Command                  | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `baleobala virtmic`      | Create PipeWire null-sink + remap-source             |
| `baleobala send`         | Read lines from stdin, encode & play                 |
| `baleobala recv`         | Capture audio, decode, print each message            |
| `baleobala devices`      | List audio devices visible to sounddevice            |
| `baleobala loopback`     | In-process self-test (no audio devices)              |

Shared flags: `--device NAME`, `--protocol {normal,fast,fastest}`, `-v` for
debug logging.

## Protocol choice

| Protocol  | Throughput | Robustness through Opus/NS           |
| --------- | ---------- | ------------------------------------ |
| `normal`  | ~ 8 B/s    | best — use if `fast` drops packets   |
| `fast`    | ~16 B/s    | **recommended default**              |
| `fastest` | ~32 B/s    | acceptable only with NS turned off   |

## Recommended call-app settings (maximize survival)

Any ML-based noise suppression will shred FEC signals. Before the call:

**Zoom** — Settings → Audio → Advanced
- `Original sound for musicians` → **ON**
- `Echo cancellation` → *Auto*
- `Background noise suppression` → **Low**
- `High fidelity music mode` → **ON**

**Google Meet** — ⋮ → Settings → Audio
- `Noise cancellation` → **OFF**

**Discord** — Settings → Voice & Video
- `Noise Suppression` → **OFF**
- `Echo Cancellation` → off if tolerable
- `Advanced Voice Activity` → OFF

## Framing protocol

8-byte header + up to 132-byte payload, fits inside one 140-byte GGWave
packet:

```
 offset  size  field        notes
 ------  ----  ---------    ----------------------------------------
   0      1    magic        0xBA  (baleobala)
   1      1    version      0x01
   2      1    flags        START=0x01  END=0x02  SINGLE=0x04
   3      2    msg_id       u16 little-endian, wraps
   5      1    frag_idx     0..frag_total-1
   6      1    frag_total   1..255
   7      1    hdr_crc8     CRC-8/SMBUS over bytes [0..6]
```

Messages up to `132 × 255 ≈ 33 KiB` are fragmented and reassembled
in-order. Short messages take the SINGLE fast path: one frame, no
reassembly state, emitted immediately.

## Troubleshooting

**`pactl: command not found`** — install `pulseaudio-utils`.

**Virtual mic doesn't appear in Zoom** — Zoom caches the device list at
launch. Create the virtmic *before* opening Zoom, or fully quit and
relaunch.

**Nothing decodes on the receiver** — first run `baleobala loopback` to
confirm the software path works. If that passes, the issue is either
(a) noise suppression on the transmit side (see settings above) or
(b) the receiver is capturing a different device than the one the call
app is playing to. Use `baleobala devices` to pick the right input.

**Decoder reports non-baleobala packets** — GGWave decoded something
but it didn't match our magic byte. Usually benign: ambient audio can
occasionally survive RS at random. The magic + CRC-8 reject it.

## Roadmap / non-goals

* **Video channel** (v4l2loopback + streaming QR codes) for ~10×
  throughput is intentionally out of scope — use [cimbar] or [txqr]
  with a virtual camera if you need it.
* **macOS / Windows** virtual-mic creation is out of scope; the codec
  and framing layers work cross-platform, but creating the virtual
  device on those OSes is a different problem (BlackHole, VB-Cable).

[GGWave]: https://github.com/ggerganov/ggwave
[cimbar]: https://github.com/sz3/cimbar
[txqr]: https://github.com/divan/txqr
