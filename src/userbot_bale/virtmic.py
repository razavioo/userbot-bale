"""
Virtual-microphone manager for PipeWire / PulseAudio.

Creates a null-sink and a remap-source so that any audio we play to
`<name>.sink` is exposed to conferencing apps (Zoom, Meet, Chrome, ...)
as an input device called `<name>`.

Usage:

    with VirtualMic("userbot-bale") as vm:
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
import platform
import shutil
import subprocess
from dataclasses import dataclass
from types import TracebackType

log = logging.getLogger(__name__)


class VirtualMicError(RuntimeError):
    pass


@dataclass
class VirtualMic:
    name: str = "userbot-bale"
    description: str = "Userbot Bale Virtual Mic"
    _sink_module: int | None = None
    _source_module: int | None = None
    _is_darwin: bool = False

    def __post_init__(self) -> None:
        self._is_darwin = platform.system() == "Darwin"

    @property
    def sink(self) -> str:
        """Play audio here. On Linux, this is the null-sink name.
        On macOS, this is the virtual device name (e.g. 'BlackHole 2ch')."""
        if self._is_darwin:
            return self.name if self.name != "userbot-bale" else "BlackHole 2ch"
        return f"{self.name}_sink"

    @property
    def source(self) -> str:
        """Call apps select this as their microphone."""
        if self._is_darwin:
            return self.sink
        return self.name

    def __enter__(self) -> "VirtualMic":
        if self._is_darwin:
            self._verify_darwin_device()
            log.info("virtual mic (macOS): using existing device '%s'", self.sink)
            return self

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
        if self._is_darwin:
            return
        self._safe_unload(self._source_module)
        self._safe_unload(self._sink_module)
        self._source_module = None
        self._sink_module = None

    def _verify_darwin_device(self) -> None:
        """Check if the requested device exists on macOS."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            names = [d["name"] for d in devices]
            if self.sink not in names:
                # Try case-insensitive search
                match = next((n for n in names if n.lower() == self.sink.lower()), None)
                if match:
                    # found but maybe case differs? Actually sounddevice is usually exact.
                    pass 
                else:
                    raise VirtualMicError(
                        f"Virtual device '{self.sink}' not found on macOS. "
                        "Please install BlackHole (https://existential.audio/blackhole/) "
                        "or specify an existing virtual device with --name."
                    )
        except ImportError:
            # If sounddevice isn't here, we can't verify, but we'll fail later anyway
            pass

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
