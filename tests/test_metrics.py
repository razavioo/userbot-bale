"""Tests for the stdlib Prometheus metrics primitives + HTTP server.

Covers:
  - Counter / Gauge value semantics, label keying, callback gauges.
  - Prometheus text-format escaping (backslash, newline, double-quote).
  - Registry rendering of multiple metrics.
  - HTTP server: /metrics text-format response + /healthz JSON, with
    health hooks merged in, including hooks that raise.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from baleobala.runtime import metrics as M


# ---- Primitives --------------------------------------------------------


def test_counter_starts_at_zero_with_no_labels():
    c = M.Counter("sample_total", "test", labelnames=())
    assert c.render().splitlines()[-1].startswith("sample_total ")


def test_counter_inc_accumulates():
    c = M.Counter("sample_total", "test")
    c.inc()
    c.inc(2)
    last = c.render().splitlines()[-1]
    assert last.endswith("3.0")


def test_counter_rejects_negative():
    c = M.Counter("sample_total", "test")
    with pytest.raises(ValueError):
        c.inc(-1)


def test_counter_with_labels_keeps_each_set_separate():
    c = M.Counter("calls_total", "test", labelnames=("result",))
    c.inc(result="ok")
    c.inc(result="ok")
    c.inc(result="fail")
    out = c.render()
    assert 'calls_total{result="ok"} 2.0' in out
    assert 'calls_total{result="fail"} 1.0' in out


def test_label_set_mismatch_raises():
    c = M.Counter("calls_total", "test", labelnames=("result",))
    with pytest.raises(KeyError):
        c.inc()  # missing required label
    with pytest.raises(KeyError):
        c.inc(result="x", extra="y")  # extra label


def test_gauge_set_inc_dec():
    g = M.Gauge("level", "test")
    g.set(10)
    g.inc(5)
    g.dec(3)
    last = g.render().splitlines()[-1]
    assert last.endswith("12.0")


def test_gauge_set_function_resolves_at_render_time():
    holder = {"v": 0.0}
    g = M.Gauge("derived", "test")
    g.set_function(lambda: holder["v"])
    holder["v"] = 7.5
    assert "derived 7.5" in g.render()
    holder["v"] = 100.0
    assert "derived 100.0" in g.render()


def test_gauge_callback_exception_does_not_break_scrape():
    g = M.Gauge("flaky", "test")

    def bad():
        raise RuntimeError("boom")

    g.set_function(bad)
    # Render still completes without raising; the value is simply
    # absent (the only entry remains the empty pre-seed).
    out = g.render()
    assert "# TYPE flaky gauge" in out


def test_label_value_escaping_for_prometheus_text_format():
    """Backslash, newline, double-quote must be escaped per spec."""
    c = M.Counter("calls_total", "test", labelnames=("user",))
    c.inc(user='al"ice\\bob\nz')
    out = c.render()
    assert r'calls_total{user="al\"ice\\bob\nz"} 1.0' in out


def test_label_name_validation_rejects_invalid():
    with pytest.raises(ValueError):
        M.Counter("x", "h", labelnames=("bad-name",))  # hyphen


# ---- Registry ----------------------------------------------------------


def test_registry_render_includes_all_metrics():
    r = M.Registry()
    r.register(M.Counter("a_total", "h"))
    r.register(M.Gauge("b", "h"))
    out = r.render()
    assert "# TYPE a_total counter" in out
    assert "# TYPE b gauge" in out


def test_registry_rejects_duplicate_name_with_different_instance():
    r = M.Registry()
    r.register(M.Counter("dup_total", "h"))
    with pytest.raises(ValueError):
        r.register(M.Counter("dup_total", "h"))


def test_registry_idempotent_for_same_instance():
    r = M.Registry()
    c = M.Counter("once_total", "h")
    r.register(c)
    r.register(c)  # second register of the same instance is a no-op
    assert sum(1 for m in r.metrics if m.name == "once_total") == 1


def test_default_registry_factories_idempotent():
    """`counter()` / `gauge()` may be called from multiple modules at
    import time — repeated calls with the same name return the same
    metric."""
    a = M.counter("idemp_total", "h")
    b = M.counter("idemp_total", "h")
    assert a is b


# ---- HTTP server -------------------------------------------------------


@pytest.fixture
def http_server():
    # Bind an ephemeral port (0) so concurrent test runs don't collide.
    server = M.start_http_server(port=0)
    try:
        yield server
    finally:
        server.shutdown()


def _scrape(server, path: str) -> tuple[int, str]:
    host, port = server.server_address
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=2.0) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_metrics_endpoint_returns_text_exposition(http_server):
    M.counter("scrape_total", "scraped").inc()
    status, body = _scrape(http_server, "/metrics")
    assert status == 200
    assert "# TYPE scrape_total counter" in body
    assert "scrape_total " in body


def test_healthz_returns_json_with_uptime(http_server):
    status, body = _scrape(http_server, "/healthz")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["uptime_seconds"] >= 0


def test_health_hooks_are_merged_into_response(http_server):
    M.add_health_hook(lambda: {"my_key": 42, "another": "x"})
    _, body = _scrape(http_server, "/healthz")
    payload = json.loads(body)
    assert payload["my_key"] == 42
    assert payload["another"] == "x"


def test_health_hook_exception_surfaced_not_fatal(http_server):
    def broken_hook():
        raise RuntimeError("fail")

    M.add_health_hook(broken_hook)
    status, body = _scrape(http_server, "/healthz")
    assert status == 200
    payload = json.loads(body)
    # The error is surfaced as a `<hook_name>_error` field.
    assert "broken_hook_error" in payload


def test_unknown_path_returns_404(http_server):
    host, port = http_server.server_address
    req = urllib.request.Request(f"http://{host}:{port}/nope")
    try:
        urllib.request.urlopen(req, timeout=2.0)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:  # pragma: no cover
        pytest.fail("expected 404")


# ---- Per-component instrumentation -------------------------------------


def test_relay_state_transitions_increment_counter(monkeypatch):
    """RelayState.transition() bumps baleobala_relay_active_transitions_total
    only on real flips, not on no-op same-value writes."""
    from baleobala.vpn.relay_state import RelayState

    state = RelayState(num_accounts=1, log_stream=__import__("io").StringIO())
    transitions = M.counter(
        "baleobala_relay_active_transitions_total",
        "test",
        labelnames=("account", "value", "where"),
    )
    before = transitions.render()

    state.transition(0, True, "test-start")
    state.transition(0, True, "test-noop-same")  # no-op: prev==value
    state.transition(0, False, "test-end")

    after = transitions.render()
    assert before != after
    # Two real flips → at least two new metric entries with the test
    # `where` label values.
    assert 'where="test-start"' in after
    assert 'where="test-end"' in after
    assert 'where="test-noop-same"' not in after, (
        "no-op transition should not create a metric entry"
    )


def test_relay_state_active_age_gauge_uses_callback(monkeypatch):
    """The age gauge should resolve via the per-account callback so it
    reflects current time, not last-transition time."""
    from baleobala.vpn.relay_state import RelayState

    clock = {"t": 0.0}
    state = RelayState(num_accounts=1, clock=lambda: clock["t"])
    state.transition(0, True, "test")
    clock["t"] = 12.5
    g = M.gauge("baleobala_relay_active_flag_age_seconds", "test", labelnames=("account",))
    out = g.render()
    assert 'baleobala_relay_active_flag_age_seconds{account="0"} 12.5' in out
