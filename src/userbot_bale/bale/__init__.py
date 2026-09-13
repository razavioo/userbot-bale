"""Bale integration package."""

__all__ = [
    "BaleApiClient",
    "CallCredentials",
    "Endpoint",
    "IncomingCallEvent",
    "LiveKitCredentials",
    "LiveKitSink",
    "LiveKitSource",
    "LiveKitSession",
    "MessagingBackend",
    "MtprotoMessagingBackend",
    "MtprotoSessionState",
    "MtprotoTransportNotReady",
    "OutPeer",
    "RequestStartLiveKitCall",
    "WsClient",
    "fetch_endpoints",
    "parse_call_credentials",
]


def __getattr__(name: str):
    if name in {"BaleApiClient", "LiveKitCredentials"}:
        from userbot_bale.bale.api import BaleApiClient, LiveKitCredentials

        value = {"BaleApiClient": BaleApiClient, "LiveKitCredentials": LiveKitCredentials}[name]
    elif name in {"Endpoint", "fetch_endpoints"}:
        from userbot_bale.bale.endpoints import Endpoint, fetch_endpoints

        value = {"Endpoint": Endpoint, "fetch_endpoints": fetch_endpoints}[name]
    elif name in {"LiveKitSink", "LiveKitSource", "LiveKitSession"}:
        from userbot_bale.bale.livekit_backend import LiveKitSession, LiveKitSink, LiveKitSource

        value = {
            "LiveKitSession": LiveKitSession,
            "LiveKitSink": LiveKitSink,
            "LiveKitSource": LiveKitSource,
        }[name]
    elif name in {"CallCredentials", "IncomingCallEvent", "OutPeer", "RequestStartLiveKitCall", "parse_call_credentials"}:
        from userbot_bale.bale.protos import (
            CallCredentials,
            IncomingCallEvent,
            OutPeer,
            RequestStartLiveKitCall,
            parse_call_credentials,
        )

        value = {
            "CallCredentials": CallCredentials,
            "IncomingCallEvent": IncomingCallEvent,
            "OutPeer": OutPeer,
            "RequestStartLiveKitCall": RequestStartLiveKitCall,
            "parse_call_credentials": parse_call_credentials,
        }[name]
    elif name == "MessagingBackend":
        from userbot_bale.bale.messaging_backend import MessagingBackend

        value = MessagingBackend
    elif name in {"MtprotoMessagingBackend", "MtprotoSessionState", "MtprotoTransportNotReady"}:
        from userbot_bale.bale.mtproto_backend import (
            MtprotoMessagingBackend,
            MtprotoSessionState,
            MtprotoTransportNotReady,
        )

        value = {
            "MtprotoMessagingBackend": MtprotoMessagingBackend,
            "MtprotoSessionState": MtprotoSessionState,
            "MtprotoTransportNotReady": MtprotoTransportNotReady,
        }[name]
    elif name == "WsClient":
        from userbot_bale.bale.ws_client import WsClient

        value = WsClient
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
