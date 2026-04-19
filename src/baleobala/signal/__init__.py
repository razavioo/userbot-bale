"""Signal-layer primitives for audio encoding and framing."""

__all__ = [
    "Codec",
    "Frame",
    "FrameFlag",
    "Protocol",
    "Reassembler",
    "SAMPLE_RATE",
    "fragment",
]


def __getattr__(name: str):
    if name in {"Codec", "Protocol", "SAMPLE_RATE"}:
        from baleobala.codec import Codec, Protocol, SAMPLE_RATE

        value = {
            "Codec": Codec,
            "Protocol": Protocol,
            "SAMPLE_RATE": SAMPLE_RATE,
        }[name]
    elif name in {"Frame", "FrameFlag", "Reassembler", "fragment"}:
        from baleobala.framing import Frame, FrameFlag, Reassembler, fragment

        value = {
            "Frame": Frame,
            "FrameFlag": FrameFlag,
            "Reassembler": Reassembler,
            "fragment": fragment,
        }[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
