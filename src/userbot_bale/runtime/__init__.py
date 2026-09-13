"""Runtime primitives for byte-stream tunneling over an arbitrary carrier."""

from userbot_bale.runtime.channel import MemoryByteChannel
from userbot_bale.runtime.audio_channel import AudioByteChannel
from userbot_bale.runtime.bridge import AudioTunnelBridge
from userbot_bale.runtime.frame import (
    TunnelFrame,
    TunnelFrameType,
    TunnelRole,
    encode_tunnel_frame,
    decode_tunnel_frame,
)
from userbot_bale.runtime.interfaces import ByteChannel, SecurityProvider
from userbot_bale.runtime.proxy import (
    DirectFirstSocks5ProxyServer,
    ProxyPacket,
    ProxyPacketType,
    QueuedTunnelTransport,
    Socks5ProxyServer,
    TunnelTcpRelay,
)
from userbot_bale.runtime.security import NullSecurityProvider
from userbot_bale.runtime.session import TunnelConfig, TunnelSession, TunnelStats

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
