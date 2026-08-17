"""
Linux TUN device helper. Opens /dev/net/tun in TUN (L3, IP-only) mode,
returning a blocking file-like object that yields raw IP packets on
read() and accepts them on write().

This module is *Linux-only* and pulls zero third-party deps — it uses
ioctl directly so we don't force pyroute2 on users who only want the
audio path. IP address / MTU / route configuration is done by the
shipped `scripts/vpn-setup-tun.sh`; keeping that out of Python means
this module needs no CAP_NET_ADMIN once the device exists.

Typical lifecycle:

    1. Script (run once as root, or with CAP_NET_ADMIN):
         ip tuntap add name vpn0 mode tun user $USER
         ip addr add 10.77.0.2/24 dev vpn0
         ip link set vpn0 mtu 1400 up
    2. Process (unprivileged):
         dev = TunDevice.open("vpn0")
         while ...:
             pkt = dev.read_packet()
             ...
             dev.write_packet(pkt)
"""

from __future__ import annotations

import errno
import logging
import os
import struct
import sys
from typing import Optional

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - unavailable on Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# from <linux/if_tun.h>
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

# from <net/if.h>
IFNAMSIZ = 16


class TunDevice:
    """
    Thin wrapper around /dev/net/tun for a pre-created TUN device.

    Reads/writes raw IP packets (IFF_NO_PI: no 4-byte packet-info header).
    """

    def __init__(self, fd: int, name: str) -> None:
        self._fd = fd
        self._name = name
        self._closed = False

    @classmethod
    def open(cls, name: str) -> "TunDevice":
        if fcntl is None or not sys.platform.startswith("linux"):
            raise RuntimeError("TunDevice is available only on Linux")
        if len(name.encode()) >= IFNAMSIZ:
            raise ValueError(f"device name too long: {name!r}")
        try:
            fd = os.open("/dev/net/tun", os.O_RDWR)
        except FileNotFoundError as e:
            raise RuntimeError(
                "/dev/net/tun not found. Load the tun module: sudo modprobe tun"
            ) from e
        try:
            ifr = struct.pack("16sH", name.encode(), IFF_TUN | IFF_NO_PI)
            fcntl.ioctl(fd, TUNSETIFF, ifr)
        except OSError as e:
            os.close(fd)
            if e.errno == errno.EPERM:
                raise PermissionError(
                    f"TUNSETIFF on {name} needs CAP_NET_ADMIN, or pre-create "
                    f"the device with: ip tuntap add name {name} mode tun user $USER"
                ) from e
            raise
        log.info("opened TUN device %s (fd=%d)", name, fd)
        return cls(fd, name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def fileno(self) -> int:
        return self._fd

    def read_packet(self, bufsize: int = 2048) -> Optional[bytes]:
        """Blocking read of one IP packet. Returns None on EOF/close."""
        if self._closed:
            return None
        try:
            data = os.read(self._fd, bufsize)
        except OSError as e:
            if e.errno in (errno.EBADF, errno.EINTR):
                return None
            raise
        return data or None

    def write_packet(self, packet: bytes) -> int:
        if self._closed:
            return 0
        return os.write(self._fd, packet)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._fd)
        except OSError:
            pass

    def __enter__(self) -> "TunDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
