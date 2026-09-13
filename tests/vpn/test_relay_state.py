"""Tests for the per-account probe-active flag.

`RelayState` is the first piece extracted from the relay's
`on_incoming_call` closure (plan A1a). Behavior here must remain
byte-equivalent to the original `_set_active` helper — production
incident triage greps for the exact stderr line format.
"""

from __future__ import annotations

import io

from userbot_bale.vpn.relay_state import (
    DEFAULT_STALE_ACTIVE_FLAG_SECS,
    RelayState,
)


def _new(num_accounts: int = 2, *, t: float = 0.0):
    clock = {"t": t}

    def _clock() -> float:
        return clock["t"]

    log = io.StringIO()
    state = RelayState(num_accounts=num_accounts, clock=_clock, log_stream=log)
    return state, clock, log


def test_initial_state_is_all_false():
    state, _, _ = _new(num_accounts=3)
    for idx in range(3):
        assert state.is_active(idx) is False
        assert state.active_at(idx) == 0.0
        assert state.age(idx) == 0.0


def test_transition_sets_flag_and_timestamp():
    state, clock, _ = _new()
    clock["t"] = 100.0
    state.transition(0, True, "probe-start")
    assert state.is_active(0) is True
    assert state.active_at(0) == 100.0


def test_transition_to_false_zeros_timestamp():
    """Matches the original `_set_active` behavior at vpn/cli.py:275-278:
    setting False resets the timestamp to 0.0 so a later age computation
    on a stale-but-cleared slot does not return a giant epoch number."""
    state, clock, _ = _new()
    clock["t"] = 100.0
    state.transition(0, True, "probe-start")
    state.transition(0, False, "after-commit")
    assert state.is_active(0) is False
    assert state.active_at(0) == 0.0


def test_age_returns_zero_when_inactive():
    """The original closure printed nothing when the flag was False to
    avoid the 'time.time() - 0' = 56-year-old leak misreport. RelayState
    enforces this directly: age() returns 0.0 when not active."""
    state, clock, _ = _new()
    clock["t"] = 1000.0
    assert state.age(0) == 0.0  # never set
    state.transition(0, True, "probe-start")
    clock["t"] = 1003.5
    assert state.age(0) == 3.5
    state.transition(0, False, "after-commit")
    assert state.age(0) == 0.0


def test_is_stale_requires_active_and_age_over_threshold():
    state, clock, _ = _new()
    clock["t"] = 0.0
    state.transition(0, True, "probe-start")
    clock["t"] = 30.0
    assert state.is_stale(0, threshold=60.0) is False
    clock["t"] = 65.0
    assert state.is_stale(0, threshold=60.0) is True


def test_is_stale_false_when_inactive_even_with_old_timestamp():
    state, clock, _ = _new()
    clock["t"] = 0.0
    state.transition(0, True, "probe-start")
    clock["t"] = 100.0
    state.transition(0, False, "after-commit")
    clock["t"] = 200.0
    # Even though wall clock advanced far past threshold, the flag is
    # False — staleness only applies to leaked True flags.
    assert state.is_stale(0, threshold=60.0) is False


def test_default_stale_threshold_matches_original_constant():
    """The original `STALE_ACTIVE_FLAG_SECS = 60.0` is preserved."""
    assert DEFAULT_STALE_ACTIVE_FLAG_SECS == 60.0


def test_transition_emits_audit_log_line_in_original_format():
    """journalctl greps in production rely on this exact format:

        [vpn-mesh] _active[<idx>]: <prev>→<value> at <where> (thread=<name>)

    Any drift here breaks the operator's incident-triage workflow."""
    state, _, log = _new(num_accounts=3)
    state.transition(2, True, "probe-start")
    out = log.getvalue()
    assert "[vpn-mesh] _active[2]: False→True at probe-start" in out
    assert "(thread=" in out and out.rstrip().endswith(")")


def test_transition_to_same_value_does_not_log():
    """The original suppressed the audit line when prev == value to keep
    the journal readable; preserve that."""
    state, _, log = _new()
    state.transition(0, True, "probe-start")
    log.truncate(0)
    log.seek(0)
    state.transition(0, True, "probe-start-redundant")
    assert log.getvalue() == ""


def test_transition_value_change_back_logs_with_new_arrow():
    state, _, log = _new()
    state.transition(0, True, "probe-start")
    log.truncate(0)
    log.seek(0)
    state.transition(0, False, "on-drop")
    out = log.getvalue()
    assert "_active[0]: True→False at on-drop" in out


def test_concurrent_transitions_do_not_lose_audit_lines():
    """Lock-protected transitions; no torn writes when threads collide."""
    import threading

    state, _, log = _new(num_accounts=4)
    n_iter = 500

    def hammer(idx: int) -> None:
        for i in range(n_iter):
            state.transition(idx, bool(i % 2), f"thread-{idx}-i{i}")

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every transition that flipped a value emits exactly one line. With
    # 500 iterations alternating True/False, that's 500 transitions per
    # thread (the very first transition from initial-False to False is
    # skipped because prev==value, then 499 flips True/False alternation
    # plus the final state). Lower bound: at least 1500 lines across all
    # threads (3 flips × 500 iter / 2 — exact count varies per thread).
    line_count = log.getvalue().count("[vpn-mesh] _active[")
    assert line_count >= 4 * (n_iter - 2)
