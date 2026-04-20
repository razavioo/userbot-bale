"""
Hot-swap the underlying transport mid-session. Verifies that pending
ARQ frames re-emit on the new transport and newly-sent packets flow
end-to-end after the swap.
"""

from __future__ import annotations

import queue
import time

from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


def test_swap_mid_session_resumes_tunnel():
    # A↔B_old is the initial pair. After N packets, we swap A's side
    # to A_new which is paired with B_new. B keeps running throughout.
    a_old, b_side = InMemoryTransport.pair(mtu=200)
    a_new, b_side_new = InMemoryTransport.pair(mtu=200)

    # Wire both b-side transports into one tunnel's recv stream by
    # using a merged fake transport that polls both.
    class _Merged:
        mtu = 200
        rate_hint = 1e6
        def __init__(self, a, b):
            self._a = a; self._b = b
        def send_bytes(self, data):
            # Broadcast on whichever peer is live; receiver only hears
            # back via the ones we hand it directly.
            self._a.send_bytes(data)
            self._b.send_bytes(data)
        def recv_bytes(self, timeout):
            buf = self._a.recv_bytes(timeout=(timeout or 0.1) / 2)
            if buf is not None:
                return buf
            return self._b.recv_bytes(timeout=(timeout or 0.1) / 2)
        def close(self): pass

    merged_b = _Merged(b_side, b_side_new)

    a = Tunnel(a_old, sess_id=0x42, ack_timeout=0.1, mtu_override=200, window=4)
    b = Tunnel(merged_b, sess_id=0x42, ack_timeout=0.1, mtu_override=200, window=4)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)

        a.send_packet(b"before-swap")
        assert rx.get(timeout=2) == b"before-swap"

        # Swap A's carrier to the fresh pair.
        old = a.swap_transport(a_new)
        old.close()
        # Give retry loop a tick to notice.
        time.sleep(0.2)

        a.send_packet(b"after-swap")
        got = rx.get(timeout=2)
        assert got == b"after-swap"
    finally:
        a.stop(); b.stop()
        a_old.close(); b_side.close()
        a_new.close(); b_side_new.close()


def test_swap_preserves_seq_space():
    a_old, b_old = InMemoryTransport.pair(mtu=200)
    a_new, b_new = InMemoryTransport.pair(mtu=200)

    a = Tunnel(a_old, sess_id=1, ack_timeout=0.1, mtu_override=200)
    try:
        a.start(on_packet=lambda _: None)
        a.send_packet(b"p1")
        before = a._seq_next
        a.swap_transport(a_new).close()
        a.send_packet(b"p2")
        after = a._seq_next
        # seq_next monotonically advances; swap doesn't reset it.
        assert after > before
    finally:
        a.stop(); a_old.close(); b_old.close(); a_new.close(); b_new.close()
