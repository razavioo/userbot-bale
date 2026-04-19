"""baleobala — acoustic data bridge over voice/video calls."""

from baleobala.audio_backend import AudioSink, AudioSource
from baleobala.framing import Frame, FrameFlag, Reassembler, fragment

__all__ = [
    "AudioSink",
    "AudioSource",
    "Frame",
    "FrameFlag",
    "Reassembler",
    "fragment",
    "Codec",
    "Protocol",
    "SAMPLE_RATE",
]

__version__ = "0.2.0"


def __getattr__(name: str):
    if name in {"Codec", "Protocol", "SAMPLE_RATE"}:
        from baleobala import codec as _codec

        value = getattr(_codec, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
