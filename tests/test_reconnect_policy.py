from userbot_bale.control.reconnect_policy import DEFAULT_RECONNECT_POLICY, ReconnectPolicy


def test_default_matches_android_constants():
    p = DEFAULT_RECONNECT_POLICY
    assert p.base_delay_ms == 3_000
    assert p.max_delay_ms == 30_000
    assert p.stable_reset_ms == 60_000


def test_backoff_caps_at_max():
    p = ReconnectPolicy(base_delay_ms=1000, max_delay_ms=10_000)
    assert p.delay_ms_for_attempt(1) == 1000
    assert p.delay_ms_for_attempt(2) == 2000
    assert p.delay_ms_for_attempt(3) == 4000
    assert p.delay_ms_for_attempt(5) == 10_000  # capped (would be 16000)
    assert p.delay_ms_for_attempt(99) == 10_000


def test_serialisable():
    d = DEFAULT_RECONNECT_POLICY.to_dict()
    assert d["base_delay_ms"] == 3_000
