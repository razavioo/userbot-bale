"""
Virtual-microphone manager for PipeWire / PulseAudio.

Creates a null-sink and a remap-source so that any audio we play to
`<name>.sink` is exposed to conferencing apps (Zoom, Meet, Chrome, ...)
as an input device called `<name>`.

Usage:

    with VirtualMic("baleobala") as vm:
        # vm.sink is the playback target; feed audio here
        # vm.source is what call apps see as a microphone
        play_to(vm.sink, waveform)

On exit, both modules are unloaded so the host audio graph is left clean.
Unload failures are logged but do not mask the original exception.

This module shells out to `pactl`, which talks to PipeWire's Pulse
compatibility layer on modern distros and to PulseAudio directly on
older ones. It is the most portable option — a native PipeWire path
(pw-loopback) requires parsing different output and is not necessary
here because `pactl` has worked on every distro shipping PipeWire since
2021.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from types import TracebackType

log = logging.getLogger(__name__)


class VirtualMicError(RuntimeError):
    pass


@dataclass
class VirtualMic:
    name: str = "baleobala"
    description: str = "Baleobala Virtual Mic"
    _sink_module: int | None = None
    _source_module: int | None = None

    @property
    def sink(self) -> str:
        """PulseAudio sink name. Play audio here."""
        return f"{self.name}_sink"

    @property
    def source(self) -> str:
        """PulseAudio source name. Call apps select this as their microphone."""
        return self.name

    def __enter__(self) -> "VirtualMic":
        self._ensure_pactl()
        self._sink_module = self._load_module(
            "module-null-sink",
            f"sink_name={self.sink}",
            f"sink_properties=device.description='{self.description}'",
        )
        try:
            self._source_module = self._load_module(
                "module-remap-source",
                f"master={self.sink}.monitor",
                f"source_name={self.source}",
                f"source_properties=device.description='{self.description}'",
            )
        except VirtualMicError:
            self._safe_unload(self._sink_module)
            self._sink_module = None
            raise
        log.info("virtual mic up: sink=%s source=%s", self.sink, self.source)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._safe_unload(self._source_module)
        self._safe_unload(self._sink_module)
        self._source_module = None
        self._sink_module = None

    @staticmethod
    def _ensure_pactl() -> None:
        if shutil.which("pactl") is None:
            raise VirtualMicError(
                "`pactl` not found. Install pulseaudio-utils "
                "(or ensure PipeWire's pulse compatibility is enabled)."
            )

    @staticmethod
    def _load_module(module: str, *args: str) -> int:
        cmd = ["pactl", "load-module", module, *args]
        log.debug("exec: %s", " ".join(cmd))
        try:
            out = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=5
            )
        except subprocess.CalledProcessError as e:
            raise VirtualMicError(
                f"pactl load-module {module} failed: {e.stderr.strip() or e.stdout.strip()}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise VirtualMicError(
                f"pactl load-module {module} timed out"
            ) from e
        try:
            return int(out.stdout.strip())
        except ValueError as e:
            raise VirtualMicError(
                f"unexpected pactl output: {out.stdout!r}"
            ) from e

    @staticmethod
    def _safe_unload(module_id: int | None) -> None:
        if module_id is None:
            return
        try:
            subprocess.run(
                ["pactl", "unload-module", str(module_id)],
                check=True, capture_output=True, text=True, timeout=5,
            )
            log.debug("unloaded module %d", module_id)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to unload module %d: %s", module_id, e)
