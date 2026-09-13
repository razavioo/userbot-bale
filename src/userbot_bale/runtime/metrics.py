"""Tiny stdlib-only Prometheus-format metrics.

We intentionally avoid `prometheus_client` to keep the relay/coordinator
deployable without an extra wheel — the Prometheus text exposition
format is small and stable enough to emit by hand. The only types we
use are `Counter` (monotonically increasing) and `Gauge` (settable).

Threading model: every primitive guards its label-values dict with a
`threading.Lock`; reads (i.e. `Registry.render()`) take a snapshot under
the same lock so the scrape never sees a half-written value. The
`start_http_server` helper spins a daemon `http.server.ThreadingHTTPServer`
on the given port and serves `/metrics` (Prometheus text) and
`/healthz` (JSON).

All names follow Prometheus convention: lower-snake-case + `_total`
suffix for counters, units suffix for gauges (e.g. `_seconds`).
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------


def _validate_label_names(labelnames: Iterable[str]) -> tuple[str, ...]:
    out = tuple(labelnames)
    for n in out:
        if not n.replace("_", "").isalnum():
            raise ValueError(f"invalid label name: {n!r}")
    return out


def _label_key(labelnames: tuple[str, ...], labels: dict[str, str]) -> tuple[str, ...]:
    if set(labels.keys()) != set(labelnames):
        raise KeyError(
            f"label mismatch: expected {sorted(labelnames)}, got {sorted(labels.keys())}"
        )
    # Ordered by labelnames so the same label set always hashes to the
    # same key regardless of dict iteration order.
    return tuple(str(labels[n]) for n in labelnames)


def _format_labels(labelnames: tuple[str, ...], key: tuple[str, ...]) -> str:
    if not labelnames:
        return ""
    pairs = []
    for name, value in zip(labelnames, key):
        # Escape per Prometheus text exposition spec: backslash, newline,
        # double-quote.
        escaped = (
            value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        )
        pairs.append(f'{name}="{escaped}"')
    return "{" + ",".join(pairs) + "}"


class _Metric:
    """Base for Counter and Gauge — owns name, help, labelnames, and
    the per-label-set values dict + lock."""

    _kind: str = ""  # "counter" or "gauge"

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.help = help_text
        self.labelnames = _validate_label_names(labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()
        # Pre-seed the empty-label entry so a never-incremented counter
        # still renders as `name 0` (Prometheus best practice for
        # operational visibility).
        if not self.labelnames:
            self._values[()] = 0.0

    def _snapshot(self) -> list[tuple[tuple[str, ...], float]]:
        with self._lock:
            return list(self._values.items())

    def render(self) -> str:
        lines: list[str] = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} {self._kind}",
        ]
        for key, value in self._snapshot():
            lines.append(f"{self.name}{_format_labels(self.labelnames, key)} {value!r}")
        return "\n".join(lines)


class Counter(_Metric):
    _kind = "counter"

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        if amount < 0:
            raise ValueError("counter cannot be decremented")
        key = _label_key(self.labelnames, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(amount)


class Gauge(_Metric):
    _kind = "gauge"

    def set(self, value: float, **labels: Any) -> None:
        key = _label_key(self.labelnames, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        key = _label_key(self.labelnames, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def dec(self, amount: float = 1.0, **labels: Any) -> None:
        self.inc(-amount, **labels)

    def set_function(self, fn: Callable[[], float], **labels: Any) -> None:
        """Register a no-arg callback that returns the current value.
        The registry calls it on every scrape — useful for gauges whose
        source-of-truth is owned by another object (e.g. `len(sessions)`)."""
        key = _label_key(self.labelnames, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._functions[key] = fn

    @property
    def _functions(self) -> dict[tuple[str, ...], Callable[[], float]]:
        # Lazy-create so we don't add the attribute to every Counter/Gauge.
        attr = self.__dict__.get("__functions")
        if attr is None:
            attr = {}
            self.__dict__["__functions"] = attr
        return attr

    def _snapshot(self) -> list[tuple[tuple[str, ...], float]]:
        with self._lock:
            data = dict(self._values)
            for key, fn in self._functions.items():
                try:
                    data[key] = float(fn())
                except Exception:
                    # A misbehaving callback must not break the scrape.
                    pass
        return list(data.items())


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------


@dataclass
class Registry:
    """Holds metrics and renders the Prometheus text exposition format."""

    metrics: list[_Metric] = field(default_factory=list)

    def register(self, metric: _Metric) -> None:
        # Idempotent registration — re-registering the same instance is
        # a no-op (so module-level metrics don't double-register on
        # test reloads).
        for existing in self.metrics:
            if existing.name == metric.name:
                if existing is metric:
                    return
                raise ValueError(
                    f"metric {metric.name!r} already registered with a different instance"
                )
        self.metrics.append(metric)

    def render(self) -> str:
        return "\n".join(m.render() for m in self.metrics) + "\n"


_DEFAULT_REGISTRY = Registry()


def default_registry() -> Registry:
    return _DEFAULT_REGISTRY


def counter(name: str, help_text: str, labelnames: Iterable[str] = ()) -> Counter:
    """Create + register a Counter on the default registry. Idempotent."""
    for existing in _DEFAULT_REGISTRY.metrics:
        if existing.name == name:
            assert isinstance(existing, Counter), (
                f"metric {name!r} already registered as {type(existing).__name__}"
            )
            return existing
    c = Counter(name, help_text, labelnames=labelnames)
    _DEFAULT_REGISTRY.register(c)
    return c


def gauge(name: str, help_text: str, labelnames: Iterable[str] = ()) -> Gauge:
    """Create + register a Gauge on the default registry. Idempotent."""
    for existing in _DEFAULT_REGISTRY.metrics:
        if existing.name == name:
            assert isinstance(existing, Gauge), (
                f"metric {name!r} already registered as {type(existing).__name__}"
            )
            return existing
    g = Gauge(name, help_text, labelnames=labelnames)
    _DEFAULT_REGISTRY.register(g)
    return g


# ---------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------


_PROCESS_START = time.time()
_HEALTH_HOOKS: list[Callable[[], dict[str, Any]]] = []


def add_health_hook(fn: Callable[[], dict[str, Any]]) -> None:
    """Register a hook that contributes JSON keys to /healthz. Each hook
    returns a dict that gets merged into the response. Exceptions are
    caught and surfaced as `<key>_error: "<repr>"` entries."""
    _HEALTH_HOOKS.append(fn)


def _build_handler(registry: Registry) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] == "/metrics":
                body = registry.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.split("?", 1)[0] == "/healthz":
                payload: dict[str, Any] = {
                    "ok": True,
                    "uptime_seconds": time.time() - _PROCESS_START,
                }
                for hook in _HEALTH_HOOKS:
                    try:
                        payload.update(hook())
                    except Exception as exc:  # noqa: BLE001
                        payload[f"{hook.__name__}_error"] = repr(exc)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            # Silence the default access log — these are internal scrapes
            # and would drown the journal.
            return

    return _Handler


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_http_server(
    port: int,
    *,
    host: str = "127.0.0.1",
    registry: Registry | None = None,
) -> _ThreadingHTTPServer:
    """Spin a metrics + healthz server on a daemon thread. Returns the
    server (so callers can shutdown() on teardown). Bind to 127.0.0.1 by
    default — operators wire a node_exporter scrape target locally."""
    reg = registry or _DEFAULT_REGISTRY
    handler_cls = _build_handler(reg)
    server = _ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever, name="metrics-http", daemon=True,
    )
    thread.start()
    return server
