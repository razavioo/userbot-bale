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

Full two-machine walkthrough: **[docs/SETUP.md](docs/SETUP.md)**.

Single-host smoke test (no audio device required):

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

Call-app noise-suppression settings that must be tuned for audible FEC
tones to survive are documented in [docs/SETUP.md](docs/SETUP.md).

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

## How device routing works

PortAudio (what `sounddevice` uses) exposes PulseAudio/PipeWire as a
single ALSA device named `pulse`; individual sinks and sources are not
visible to it directly. Baleobala's CLI detects this and, when
`--device <name>` names a PulseAudio sink or source that isn't a
sounddevice device, sets `PULSE_SINK` / `PULSE_SOURCE` in the process
environment and routes through the `pulse` device. That's why
`baleobala send --device baleobala_sink` and
`baleobala recv --device baleobala` just work, even though neither
name appears in `baleobala devices`.

## Troubleshooting

See [docs/SETUP.md](docs/SETUP.md#troubleshooting).

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
