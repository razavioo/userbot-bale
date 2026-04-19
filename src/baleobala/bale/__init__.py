"""Bale integration package."""

__all__ = [
    "BaleApiClient",
    "CallCredentials",
    "Endpoint",
    "LiveKitCredentials",
    "LiveKitSink",
    "LiveKitSource",
    "LiveKitSession",
    "OutPeer",
    "RequestStartLiveKitCall",
    "WsClient",
    "fetch_endpoints",
    "parse_call_credentials",
]


def __getattr__(name: str):
    if name in {"BaleApiClient", "LiveKitCredentials"}:
        from baleobala.bale.api import BaleApiClient, LiveKitCredentials

        value = {"BaleApiClient": BaleApiClient, "LiveKitCredentials": LiveKitCredentials}[name]
    elif name in {"Endpoint", "fetch_endpoints"}:
        from baleobala.bale.endpoints import Endpoint, fetch_endpoints

        value = {"Endpoint": Endpoint, "fetch_endpoints": fetch_endpoints}[name]
    elif name in {"LiveKitSink", "LiveKitSource", "LiveKitSession"}:
        from baleobala.bale.livekit_backend import LiveKitSession, LiveKitSink, LiveKitSource

        value = {
            "LiveKitSession": LiveKitSession,
            "LiveKitSink": LiveKitSink,
            "LiveKitSource": LiveKitSource,
        }[name]
    elif name in {"CallCredentials", "OutPeer", "RequestStartLiveKitCall", "parse_call_credentials"}:
        from baleobala.bale.protos import (
            CallCredentials,
            OutPeer,
            RequestStartLiveKitCall,
            parse_call_credentials,
        )

        value = {
            "CallCredentials": CallCredentials,
            "OutPeer": OutPeer,
            "RequestStartLiveKitCall": RequestStartLiveKitCall,
            "parse_call_credentials": parse_call_credentials,
        }[name]
    elif name == "WsClient":
        from baleobala.bale.ws_client import WsClient

        value = WsClient
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
