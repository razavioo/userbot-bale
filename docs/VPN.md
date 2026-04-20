# VPN over Bale

Run a full Linux IP tunnel over a Bale voice call. One side (client)
routes traffic from a `tun0` device through Bale to the exit node (a
VPS running a second Bale account), which MASQUERADEs the packets onto
the public internet.

> **Throughput:** DataChannel is the primary transport (~100 KB/s+ in
> reliable mode). Audio is the PoC fallback (~16 B/s — enough for a DNS
> resolution, not much else).

## Prerequisites

- Linux (client and exit node). Kernel `tun` module, iproute2, iptables.
- Python 3.9+, `pip install -e ".[bale]"` on both ends.
- Bale accounts on both ends, each with a JWT access-token saved in a
  file (see [BALE_HEADLESS.md](./BALE_HEADLESS.md) for capturing one).
- On the exit node: know your public interface name (`ip route show default`).

## One-time setup

### Client

```bash
# Create persistent TUN device (once, as root)
sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.2/24 1400 "$USER"
```

### Exit node (VPS)

```bash
# TUN device
sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 "$USER"

# IPv4 forwarding + MASQUERADE (eth0 = your WAN interface)
sudo ./scripts/vpn-exit-node.sh vpn0 eth0
```

The `vpn exit-node` subcommand runs this second script automatically on
startup (use `--skip-nat-setup` to disable).

## Bringing the tunnel up

### Exit node side (start first — it needs to answer the call)

```bash
baleobala vpn exit-node \
  --bale-jwt-file /etc/baleobala/jwt.txt \
  --tun vpn0 \
  --wan eth0 \
  --answer
```

### Client side

```bash
baleobala vpn up \
  --bale-jwt-file ~/.bale_jwt \
  --peer-id 123456789 \
  --tun vpn0
```

Once both sides print `[vpn] up`, test:

```bash
ping -c 3 10.77.0.1                # exit node over tunnel
sudo ip route add default via 10.77.0.1 dev vpn0 metric 50   # route all traffic
curl https://ifconfig.me           # should show VPS IP
```

Remember a host-route for Bale's carrier so the tunnel doesn't swallow
its own connection:

```bash
# look up the real gateway first
gw=$(ip route show default | awk '/default/ {print $3; exit}')
sudo ip route add next-ws.bale.ai via "$gw"
```

## Transports

| Flag                | Transport     | MTU    | Rate       | Notes                                           |
|---------------------|---------------|--------|------------|-------------------------------------------------|
| `--transport dc`    | DataChannel   | 14 KiB | ~100 KB/s+ | **Default.** Reliable, ordered.                 |
| `--transport qr`    | Video QR      | 240 B  | ~1 KB/s    | Needs `pip install 'baleobala[vpn-video]'`.     |
| `--transport audio` | GGWave audio  | 132 B  | ~16 B/s    | Universal fallback when WebRTC is blocked.      |
| `--transport rpc`   | Chat messages | 3 KiB  | 1–20 KB/s  | Store-and-forward; works when the call drops.   |
| `--transport auto`  | Auto-select   | —      | —          | dc → qr → audio → rpc, first to build wins.     |

**Mid-session hot-swap:** send `SIGUSR1` to the process (`kill -USR1 $PID`)
to tear down the current transport and advance to the next one in the
auto chain — TCP sessions survive because the tunnel's seq/ARQ state is
preserved across the swap.

## Multi-client exit node (mesh mode)

One exit node can serve many clients concurrently — each incoming Bale
call becomes its own per-client tunnel mapped to a unique `/30` inside
the pool:

```bash
baleobala vpn exit-node-mesh \
  --bale-jwt-file /etc/baleobala/jwt.txt \
  --tun vpn0 --wan eth0 \
  --pool-cidr 10.77.0.0/16 \
  --psk-file /etc/baleobala/vpn.psk \
  --answer
```

Every client that rings the exit node's Bale account gets allocated
a slot (deterministic from peer_id so reconnects are sticky). The
shared TUN device routes outbound replies back to the correct
client's tunnel via the destination-IP lookup in `mesh.router`.

## Encryption (PSK)

Both sides need the same passphrase for end-to-end AEAD (ChaCha20-Poly1305)
above the Bale transport — otherwise the Bale SFU sees plaintext
VPN frames:

```bash
baleobala vpn up --bale-jwt-file … --peer-id <id> --psk-file ~/.baleo-psk
baleobala vpn exit-node --bale-jwt-file … --psk-file /etc/baleobala/vpn.psk
```

## Authentication

Bootstrap a JWT for either side with phone-SMS login (live-verified
against Bale Web's gRPC-Web auth endpoint):

```bash
baleobala bale-auth --phone +989XXXXXXXXX > ~/.bale_jwt
chmod 0600 ~/.bale_jwt
```

## Troubleshooting

- **`cannot open TUN vpn0: permission denied`** — re-run the setup
  script with your current `$USER`, or check that `/dev/net/tun` exists
  (`ls /dev/net/tun`; if not, `sudo modprobe tun`).
- **Tunnel comes up but traffic doesn't flow** — verify both tun
  devices have addresses on the same /24 and MTU matches; `ping`
  between them first, then debug routing.
- **Call drops after ~30 min** — the keepalive layer (Phase 7) isn't
  wired yet. Workaround: relaunch.

## Self-test (no network)

```bash
baleobala vpn loopback --packets 100 --size 1400
baleobala vpn loopback --packets 50  --size 1400 --loss 0.2   # ARQ test
```
