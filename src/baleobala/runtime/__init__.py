"""Runtime primitives for byte-stream tunneling over an arbitrary carrier."""

from baleobala.runtime.channel import MemoryByteChannel
from baleobala.runtime.audio_channel import AudioByteChannel
from baleobala.runtime.bridge import AudioTunnelBridge
from baleobala.runtime.frame import (
    TunnelFrame,
    TunnelFrameType,
    TunnelRole,
    encode_tunnel_frame,
    decode_tunnel_frame,
)
from baleobala.runtime.interfaces import ByteChannel, SecurityProvider
from baleobala.runtime.proxy import (
    DirectFirstSocks5ProxyServer,
    ProxyPacket,
    ProxyPacketType,
    QueuedTunnelTransport,
    Socks5ProxyServer,
    TunnelTcpRelay,
)
from baleobala.runtime.security import NullSecurityProvider
from baleobala.runtime.session import TunnelConfig, TunnelSession, TunnelStats

__all__ = [
    "ByteChannel",
    "AudioByteChannel",
    "AudioTunnelBridge",
    "MemoryByteChannel",
    "NullSecurityProvider",
    "ProxyPacket",
    "ProxyPacketType",
    "QueuedTunnelTransport",
    "DirectFirstSocks5ProxyServer",
    "Socks5ProxyServer",
    "SecurityProvider",
    "TunnelConfig",
    "TunnelFrame",
    "TunnelFrameType",
    "TunnelRole",
    "TunnelSession",
    "TunnelStats",
    "TunnelTcpRelay",
    "decode_tunnel_frame",
    "encode_tunnel_frame",
]
