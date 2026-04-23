from __future__ import annotations

import socket

from baleobala.control.tunnel_bridge import VpnTunnelBridge
from baleobala.control.tunnel_service import CarrierTunnelService
from baleobala.vpn.runner import RunnerConfig, VpnRunner
from baleobala.vpn.transports import InMemoryTransport


def _make_bridge_pair(*, mtu: int = 256) -> tuple[VpnTunnelBridge, VpnTunnelBridge, InMemoryTransport, InMemoryTransport]:
    left_tx, right_tx = InMemoryTransport.pair(mtu=mtu)
    cfg = RunnerConfig(sess_id=0xBEEF, ack_timeout=0.05, window=4, max_retries=10, mtu_override=mtu)

    left_bridge = VpnTunnelBridge(tun_name="left-bridge")
    right_bridge = VpnTunnelBridge(tun_name="right-bridge")
    left_runner = VpnRunner(left_bridge.tun, left_tx, cfg)
    right_runner = VpnRunner(right_bridge.tun, right_tx, cfg)
    left_bridge.attach_runner(left_runner)
    right_bridge.attach_runner(right_runner)
    return left_bridge, right_bridge, left_tx, right_tx


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise EOFError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def test_vpn_tunnel_bridge_roundtrips_packets_with_inmemory_transport() -> None:
    left_bridge, right_bridge, left_tx, right_tx = _make_bridge_pair()
    try:
        left_bridge.start()
        right_bridge.start()

        payload = b"bridge-packet"
        assert left_bridge.send(payload) == len(payload)
        assert right_bridge.recv(timeout=2.0) == payload

        reply = b"bridge-reply"
        assert right_bridge.send(reply) == len(reply)
        assert left_bridge.recv(timeout=2.0) == reply
    finally:
        left_bridge.close()
        right_bridge.close()
        left_tx.close()
        right_tx.close()


def test_carrier_tunnel_service_bridges_packets_via_socket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    left_bridge, right_bridge, left_tx, right_tx = _make_bridge_pair()
    service = CarrierTunnelService(left_bridge, socket_path=tmp_path / "carrier.sock", manage_bridge=True)
    try:
        right_bridge.start()
        service.start(profile_id="p1", backend="packet-tunnel", pairing_id="pair-1")
        endpoint = service.status().endpoint
        assert endpoint is not None
        socket_path = endpoint.removeprefix("unix://")

        payload = b"socket-packet"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(socket_path)
            client.sendall(len(payload).to_bytes(4, "big") + payload)
            assert right_bridge.recv(timeout=2.0) == payload

            reply = b"socket-reply"
            right_bridge.send(reply)
            assert _recv_exact(client, 4) == len(reply).to_bytes(4, "big")
            assert _recv_exact(client, len(reply)) == reply
    finally:
        service.stop()
        right_bridge.close()
        left_tx.close()
        right_tx.close()
