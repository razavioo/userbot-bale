# VPN over Bale

For the main product experience, start with `baleobala doctor`, sign in with `auth bale-login`, create or accept a relay pairing, then use `vpn up` or `gui`. The lower-level tunnel flows in this document remain important for Linux tunnel validation and recovery, but they are not the primary first-run story.

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

## Normal Linux Session Runner Flow

For product-like Linux validation, prefer the session runner. It now owns
the TUN, route, DNS, NAT, and Bale carrier bypass setup for tunnel
scenarios:

```bash
baleobala vpn netns-session \
  --kind tunnel-pair \
  --server-jwt-file /etc/baleobala/jwt.txt \
  --client-jwt-file ~/.bale_jwt \
  --peer-id 123456789 \
  --server-wan eth0 \
  --artifact-dir ./artifacts
```

That path is the source of truth for Linux full-device orchestration. The
manual commands below remain as debug and recovery references.

Useful toggles:

- `--tunnel-only` disables default-route, DNS, and egress automation.
- `--dns-server <ip>` overrides the per-namespace resolver file used by
  `ip netns exec`.
- `--carrier-host <host>` adds a Bale bypass host-route over the namespace
  uplink so the tunnel does not consume its own carrier.
- `--skip-nat-setup` and `--skip-host-route-setup` keep the old manual
  steps available for investigation.

## Manual One-time setup

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

The `tunnel exit-node` subcommand runs this second script automatically on
startup (use `--skip-nat-setup` to disable).

## Manual Tunnel Bring-up

### Exit node side (start first — it needs to answer the call)

```bash
baleobala tunnel exit-node \
  --bale-jwt-file /etc/baleobala/jwt.txt \
  --tun vpn0 \
  --wan eth0 \
  --answer
```

### Client side

```bash
baleobala tunnel up \
  --bale-jwt-file ~/.bale_jwt \
  --peer-id 123456789 \
  --tun vpn0
```

Once both sides print `[tunnel] up`, test:

```bash
ping -c 3 10.77.0.1                # exit node over tunnel
sudo ip route add default via 10.77.0.1 dev vpn0 metric 50   # route all traffic
curl https://ifconfig.me           # should show VPS IP
```

Remember a host-route for Bale's carrier so the tunnel doesn't swallow
its own connection when you are debugging outside the session runner:

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

## Transport Hot-Swap

The live tunnel now keeps running while the bearer transport changes.
`SIGUSR1` is the operator trigger: the signal handler in
`src/baleobala/vpn/cli.py` asks the failover controller for the next
candidate in the chain, swaps that transport into the active `Tunnel`,
and closes the old bearer after the swap succeeds.

Practical notes:

- `--transport auto` builds the default chain in order: `dc -> qr -> audio -> rpc -> mtproto_rpc`.
- Each `SIGUSR1` advances to the next viable transport in that chain.
- The tunnel keeps its sequence number space, ARQ window, and reassembly state, so existing TCP sessions normally survive the swap.
- If no replacement transport is available, the controller leaves the current bearer in place until a later retry succeeds.
- The carrier session itself is not renegotiated by the signal; this is a transport-level swap, not a new call.

## Multi-client exit node (mesh mode)

One exit node can serve many clients concurrently — each incoming Bale
call becomes its own per-client tunnel mapped to a unique `/30` inside
the pool:

```bash
baleobala tunnel exit-node-mesh \
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
baleobala tunnel up --bale-jwt-file … --peer-id <id> --psk-file ~/.baleo-psk
baleobala tunnel exit-node --bale-jwt-file … --psk-file /etc/baleobala/vpn.psk
```

## Authentication

Bootstrap a JWT for either side with phone-SMS login (live-verified
against Bale Web's browser/web flow or the older gRPC-Web path):

```bash
baleobala auth bale-login --phone +989XXXXXXXXX --method browser --save --jwt-out ~/.bale_jwt
chmod 0600 ~/.bale_jwt
```

The older `baleobala bale-auth --phone ...` command is still available
for backwards compatibility, but `auth bale-login` is the preferred
entry point because it can force the real browser login path that we
verified against the GUI flow.

## Credential Lifecycle

Relay and device access is backed by short-lived credential epochs in
`src/baleobala/control/provisioning.py`.

- `approve_authorization()` ensures there is an active `CredentialEpoch` for the relay.
- `current_epoch()` returns the live epoch when it has not expired yet.
- `refresh_credentials()` rotates the epoch immediately.
- Revocation also rotates the epoch so previously issued secrets stop being the active lease.

Each epoch carries `issued_at`, `refresh_after`, and `expires_at`. In
operator terms, this is a rotating credential lease rather than a
permanent secret: clients should expect renewal and expiry, not an
infinite token.

## Real Two-Account Smoke

To validate the full live path on one machine with two Bale accounts:

```bash
baleobala vpn live-smoke \
  --caller-jwt-file ~/.bale_jwt_a \
  --callee-jwt-file ~/.bale_jwt_b \
  --callee-peer-id <callee-user-id> \
  --timeout 120
```

If Bale's WebSocket TLS is being intercepted by a local or corporate
certificate chain, prefer:

```bash
baleobala vpn live-smoke \
  --caller-jwt-file ~/.bale_jwt_a \
  --callee-jwt-file ~/.bale_jwt_b \
  --callee-peer-id <callee-user-id> \
  --ws-ca-file /path/to/intercepting-ca.pem
```

`--ws-ssl-no-verify` still exists only as a debug-only workaround for
temporary investigation. Production and normal operator flows should
use a CA override instead of disabling TLS verification.

## Troubleshooting

- **`cannot open TUN vpn0: permission denied`** — re-run the setup
  script with your current `$USER`, or check that `/dev/net/tun` exists
  (`ls /dev/net/tun`; if not, `sudo modprobe tun`).
- **Tunnel comes up but traffic doesn't flow** — verify both tun
  devices have addresses on the same /24 and MTU matches; `ping`
  between them first, then debug routing.
- **Call drops after ~30 min** — the keepalive is wired into the VPN
  entry points now, so this usually points to carrier connectivity or a
  terminated process rather than a missing keepalive. Check `vpn
  status`, LiveKit connectivity, and whether the process stayed alive.

## Self-test (no network)

```bash
baleobala tunnel loopback --packets 100 --size 1400
baleobala tunnel loopback --packets 50  --size 1400 --loss 0.2   # ARQ test
```
