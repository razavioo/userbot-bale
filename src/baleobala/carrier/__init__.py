"""Carrier abstractions for moving bytes over Bale/LiveKit or other media."""

__all__ = [
    "BaleCarrierController",
    "CarrierCredentials",
    "CarrierSession",
    "LiveKitCarrierSession",
]


def __getattr__(name: str):
    if name in {"BaleCarrierController", "CarrierCredentials"}:
        from baleobala.carrier.bale import BaleCarrierController, CarrierCredentials

        value = {
            "BaleCarrierController": BaleCarrierController,
            "CarrierCredentials": CarrierCredentials,
        }[name]
    elif name == "CarrierSession":
        from baleobala.carrier.interfaces import CarrierSession

        value = CarrierSession
    elif name == "LiveKitCarrierSession":
        from baleobala.carrier.livekit import LiveKitCarrierSession

        value = LiveKitCarrierSession
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
