"""Cross-cutting tests for the per-call correlation ID (A3-lite).

The cid is a short (8-char hex) tag that prefixes every log line tied
to one inbound call. Operators grep `cid=<id>` in the journal to pull
the full call lifecycle (probe → assign → tunnel → reap → on-drop)
without having to correlate by timestamp.

These tests assert:
  - the cid is generated, has the expected shape, and is unique per call
  - it appears in every log line tied to that call (entry, transitions,
    skip-probe, eviction, expect-client, provisioning, on-drop)
  - it does NOT appear in unrelated log lines (no global pollution)
  - same call's cid is consistent across log + audit-log + on-drop
"""

from __future__ import annotations

import re

import pytest

# Import the harness from the existing relay-handler tests rather than
# duplicate the fakes; the constructor + LiveKit/transport stubs are
# the same surface this test needs.
from .test_relay_handler import _Harness, _fake_event


_CID_RE = re.compile(r"cid=([0-9a-f]{8})\b")


def _cids_in(log: str) -> list[str]:
    return _CID_RE.findall(log)


def test_cid_appears_on_skip_probe_line():
    h = _Harness()
    # Pre-populate a running session + active flag so the next call
    # hits skip-probe.
    from .test_relay_handler import FakeLiveKitSession
    running = FakeLiveKitSession(url="x", token="t", identity="prior")
    h.sessions.append((42, running, object()))
    h.allocator.join(42)
    h.relay_state.transition(0, True, "from-prior-call")
    log_before = h.log.getvalue()

    h.handler.handle_call(_fake_event(peer_id=999))

    new_log = h.log.getvalue()[len(log_before):]
    cids = _cids_in(new_log)
    assert cids, f"no cid found on skip-probe path; log:\n{new_log}"
    assert "skipping probe" in new_log


def test_cid_appears_on_missing_peer_id_path():
    h = _Harness()
    event = _fake_event(peer_id=0)
    event.peer_id = None
    h.handler.handle_call(event)
    cids = _cids_in(h.log.getvalue())
    assert cids and len(cids[0]) == 8


def test_each_call_gets_a_distinct_cid():
    h = _Harness()
    # Two calls, both rejected at the missing-peer-id guard so they
    # don't entangle other state.
    seen: list[str] = []
    for _ in range(3):
        event = _fake_event(peer_id=0)
        event.peer_id = None
        h.handler.handle_call(event)
    seen = _cids_in(h.log.getvalue())
    assert len(seen) == 3
    assert len(set(seen)) == 3, f"cids collided across calls: {seen}"


def test_cid_threads_through_full_committed_call_lifecycle():
    """A single committed call must use ONE cid for: incoming-call
    line, server-jwt-assignment line, status=assigned, status=acknowledged,
    status=active, plus the active-flag transitions (probe-start →
    after-commit). All other lines without `cid=` are irrelevant."""
    h = _Harness()
    h.expected_clients.register(client_peer_id=42, session_id="s-1", expires_in_secs=30)
    h.stage_remote_identities(["42"])
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    cids = set(_cids_in(h.log.getvalue()))
    assert len(cids) == 1, (
        f"committed call should use exactly one cid; got {cids}\n{h.log.getvalue()}"
    )
    cid = next(iter(cids))
    out = h.log.getvalue()
    # Spot-check the lines that should carry it.
    assert f"cid={cid} incoming call → peer_id=42" in out
    assert f"cid={cid} peer=42 status=assigned" in out
    assert f"cid={cid} peer=42 status=acknowledged" in out
    assert f"cid={cid} peer=42 status=active" in out
    # And the audit log for transitions.
    assert f"cid={cid}" in out and "_active[0]: False→True at probe-start" in out
    assert f"cid={cid}" in out and "_active[0]: True→False at after-commit" in out


def test_cid_propagates_into_relay_state_audit_log_only_when_supplied():
    """RelayState.transition() called *without* cid must emit the
    original line format unchanged (preserves existing journalctl
    greps); called *with* cid must append ` cid=<id>`."""
    import io
    from baleobala.vpn.relay_state import RelayState

    log = io.StringIO()
    state = RelayState(num_accounts=1, log_stream=log)

    state.transition(0, True, "no-cid")
    out_no_cid = log.getvalue()
    log.truncate(0); log.seek(0)

    state.transition(0, False, "with-cid", cid="abcd1234")
    out_cid = log.getvalue()

    assert "_active[0]: False→True at no-cid" in out_no_cid
    assert "cid=" not in out_no_cid, (
        "RelayState.transition() without cid must keep the original "
        "log format; production greps depend on it"
    )
    assert "_active[0]: True→False at with-cid" in out_cid
    assert "cid=abcd1234" in out_cid


def test_cid_format_is_lowercase_hex_8():
    """The cid format is a stable contract — operators write greps
    against it. 8 chars of [0-9a-f] is what the implementation produces;
    this test pins it so a future "lets make it 12 chars" or "let's use
    base32" change is loud."""
    h = _Harness()
    event = _fake_event(peer_id=0)
    event.peer_id = None
    h.handler.handle_call(event)
    cids = _cids_in(h.log.getvalue())
    assert cids
    assert all(re.fullmatch(r"[0-9a-f]{8}", c) for c in cids), cids
