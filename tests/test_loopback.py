"""
In-process loopback: encode a frame, feed the waveform back through the
decoder, and confirm we recover the exact bytes. No audio device required;
ggwave runs entirely in memory here.

Skipped if the native ggwave module is not available (e.g. in CI without
audio tooling).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("ggwave")

from userbot_bale.codec import Codec, Protocol  # noqa: E402
from userbot_bale.framing import Frame, Reassembler, fragment  # noqa: E402


@pytest.mark.timeout(30)
@pytest.mark.parametrize("protocol", [Protocol.AUDIBLE_FAST, Protocol.AUDIBLE_FASTEST])
def test_codec_single_frame_roundtrip(protocol: Protocol) -> None:
    text = b"userbot-bale-loopback"
    (frame,) = list(fragment(text, msg_id=1))
    with Codec(protocol=protocol) as codec:
        waveform = codec.encode(frame.encode())
        assert waveform.dtype == np.float32
        assert len(waveform) > 0

        recovered: bytes | None = None
        chunk = 1024
        for start in range(0, len(waveform), chunk):
            result = codec.decode_chunk(waveform[start : start + chunk])
            if result is not None:
                recovered = result
                break
        assert recovered is not None, "decoder never produced a packet"
        decoded = Frame.decode(recovered)
        assert decoded is not None
        assert decoded.payload == text


@pytest.mark.timeout(60)
def test_multi_fragment_audio_roundtrip() -> None:
    payload = b"Lorem ipsum dolor sit amet, " * 20  # ~560 bytes → ~5 frames
    reasm = Reassembler()
    assembled: bytes | None = None

    with Codec(protocol=Protocol.AUDIBLE_FAST) as codec:
        for f in fragment(payload, msg_id=3):
            waveform = codec.encode(f.encode())
            recovered: bytes | None = None
            for start in range(0, len(waveform), 1024):
                result = codec.decode_chunk(waveform[start : start + 1024])
                if result is not None:
                    recovered = result
                    break
            assert recovered is not None
            decoded = Frame.decode(recovered)
            assert decoded is not None
            assembled = reasm.push(decoded)
    assert assembled == payload
