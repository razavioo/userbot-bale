from __future__ import annotations

import random

import pytest

from userbot_bale.vpn.framing_vpn import (
    HEADER_SIZE,
    MAGIC,
    SEQ_MODULO,
    SESS_MODULO,
    VpnFlag,
    VpnFrame,
    split_packet,
)


def test_single_frame_roundtrip():
    f = VpnFrame(sess_id=0x1234, seq=42, flags=VpnFlag.LAST, payload=b"hello")
    encoded = f.encode()
    assert encoded[0] == MAGIC
    assert len(encoded) == HEADER_SIZE + 5
    decoded = VpnFrame.decode(encoded)
    assert decoded == f


def test_decode_rejects_bad_magic():
    assert VpnFrame.decode(b"\x00" * HEADER_SIZE) is None
    assert VpnFrame.decode(b"") is None


def test_seq_24bit_wrap():
    f = VpnFrame(sess_id=1, seq=(1 << 24) - 1, flags=VpnFlag.LAST, payload=b"x")
    d = VpnFrame.decode(f.encode())
    assert d is not None and d.seq == (1 << 24) - 1
    with pytest.raises(ValueError):
        VpnFrame(sess_id=1, seq=1 << 24, flags=VpnFlag.LAST, payload=b"x").encode()


def test_split_single_frame_when_fits():
    frames = split_packet(b"ab", sess_id=0, start_seq=10, max_payload=100)
    assert len(frames) == 1
    assert VpnFlag.LAST in frames[0].flags
    assert VpnFlag.SPLIT not in frames[0].flags


def test_split_multi_frame():
    packet = bytes(range(256)) * 2  # 512 bytes
    frames = split_packet(packet, sess_id=7, start_seq=1000, max_payload=100)
    assert len(frames) == 6  # 5*100 + 12
    assert all(VpnFlag.SPLIT in f.flags for f in frames)
    assert VpnFlag.LAST in frames[-1].flags
    assert VpnFlag.LAST not in frames[0].flags
    assert [f.seq for f in frames] == [1000, 1001, 1002, 1003, 1004, 1005]
    assert b"".join(f.payload for f in frames) == packet


def test_flags_roundtrip():
    f = VpnFrame(
        sess_id=0, seq=0, flags=VpnFlag.SPLIT | VpnFlag.RETRY, payload=b""
    )
    d = VpnFrame.decode(f.encode())
    assert d is not None
    assert VpnFlag.SPLIT in d.flags
    assert VpnFlag.RETRY in d.flags
    assert VpnFlag.ACK not in d.flags


# --- property / scenario tests -----------------------------------------------


@pytest.mark.parametrize(
    "packet_len,max_payload",
    [
        (0, 8),           # zero-byte payload, single LAST frame
        (1, 8),           # below cap
        (8, 8),           # exact cap, still single frame
        (9, 8),           # one byte over cap → 2 frames
        (1024, 100),      # 11-fragment packet
        (1500, 1480),     # typical IP MTU split into 2 frames
        (60_000, 1192),   # ~51-frame split (DataChannel 1200 - header)
        (256, 1),         # 1-byte payload chunks, forces 256 frames
    ],
)
def test_split_then_concat_reconstructs_packet(packet_len, max_payload):
    """Property: split_packet().payload concatenated equals the input,
    regardless of how many fragments it produced."""
    rng = random.Random(f"{packet_len}-{max_payload}")
    pkt = bytes(rng.randrange(256) for _ in range(packet_len))
    frames = split_packet(
        pkt, sess_id=0x1234, start_seq=1000, max_payload=max_payload
    )
    assert b"".join(f.payload for f in frames) == pkt
    # Exactly one frame carries LAST.
    assert sum(1 for f in frames if VpnFlag.LAST in f.flags) == 1
    assert VpnFlag.LAST in frames[-1].flags
    # SPLIT is set on every frame iff there is more than one.
    if len(frames) == 1:
        assert VpnFlag.SPLIT not in frames[0].flags
    else:
        assert all(VpnFlag.SPLIT in f.flags for f in frames)


def test_split_seq_wraps_at_24_bit_boundary():
    """Splitting at the end of seq space wraps cleanly through 2^24 — the
    tunnel relies on this for long-lived sessions that exhaust the
    24-bit space."""
    pkt = b"x" * 250  # 5 fragments at max_payload=50
    near_wrap = SEQ_MODULO - 2
    frames = split_packet(
        pkt, sess_id=1, start_seq=near_wrap, max_payload=50
    )
    seqs = [f.seq for f in frames]
    assert seqs == [
        near_wrap,
        SEQ_MODULO - 1,
        0,
        1,
        2,
    ]
    # Each wrapped seq must still encode/decode cleanly.
    for f in frames:
        d = VpnFrame.decode(f.encode())
        assert d == f


def test_split_rejects_non_positive_max_payload():
    with pytest.raises(ValueError, match="max_payload"):
        split_packet(b"x", sess_id=0, start_seq=0, max_payload=0)
    with pytest.raises(ValueError, match="max_payload"):
        split_packet(b"x", sess_id=0, start_seq=0, max_payload=-1)


def test_encode_rejects_out_of_range_sess_and_seq():
    """Both bounds are explicit — silently truncating would corrupt sess
    routing on the receive side."""
    with pytest.raises(ValueError, match="sess_id"):
        VpnFrame(
            sess_id=SESS_MODULO, seq=0, flags=VpnFlag.LAST, payload=b""
        ).encode()
    with pytest.raises(ValueError, match="sess_id"):
        VpnFrame(sess_id=-1, seq=0, flags=VpnFlag.LAST, payload=b"").encode()
    with pytest.raises(ValueError, match="seq"):
        VpnFrame(
            sess_id=0, seq=SEQ_MODULO, flags=VpnFlag.LAST, payload=b""
        ).encode()
    with pytest.raises(ValueError, match="seq"):
        VpnFrame(sess_id=0, seq=-1, flags=VpnFlag.LAST, payload=b"").encode()


def test_decode_preserves_arbitrary_payload_bytes():
    """All 256 byte values must roundtrip, including the magic byte 0xBB
    inside the payload."""
    payload = bytes(range(256))
    f = VpnFrame(sess_id=0, seq=0, flags=VpnFlag.LAST, payload=payload)
    d = VpnFrame.decode(f.encode())
    assert d is not None and d.payload == payload


def test_decode_rejects_wrong_version():
    """Version mismatch is silent: the recv loop should drop the frame
    instead of crashing on flags it does not understand."""
    f = VpnFrame(sess_id=0, seq=0, flags=VpnFlag.LAST, payload=b"x")
    encoded = bytearray(f.encode())
    encoded[1] = 0x02  # bump version
    assert VpnFrame.decode(bytes(encoded)) is None
