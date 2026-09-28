"""userbot-bale — comprehensive Bale Messenger automation framework, userbot engine, and transport core."""

from userbot_bale import events, filters
from userbot_bale.bridge import DeliveryStore, MultiTenantBridge
from userbot_bale.events import Message, MessageEvent
from userbot_bale.framing import Frame, FrameFlag, Reassembler, fragment
from userbot_bale.userbot.async_client import AsyncBaleClient, BaleClient
from userbot_bale.userbot.client import BaleUserClient

__all__ = [
    "AsyncBaleClient",
    "AudioSink",
    "AudioSource",
    "BaleClient",
    "BaleUserClient",
    "Codec",
    "DeliveryStore",
    "Frame",
    "FrameFlag",
    "Message",
    "MessageEvent",
    "MultiTenantBridge",
    "Protocol",
    "Reassembler",
    "SAMPLE_RATE",
    "events",
    "filters",
    "fragment",
]

__version__ = "0.3.1"


def __getattr__(name: str):
    if name in {"AudioSink", "AudioSource"}:
        from userbot_bale.audio_backend import AudioSink, AudioSource

        globals()["AudioSink"] = AudioSink
        globals()["AudioSource"] = AudioSource
        return globals()[name]
    if name in {"Codec", "Protocol", "SAMPLE_RATE"}:
        from userbot_bale import codec as _codec

        value = getattr(_codec, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
