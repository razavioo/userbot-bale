# macOS client + Linux VPS production-test

This runbook is the first production-like path for a macOS packet-tunnel
client using a Linux VPS as the Bale exit node. It intentionally targets one
macOS client, IPv4 full tunnel, custom DNS, and a shared PSK.

## Contract

- macOS client tunnel address: `10.77.0.2/24`
- Linux VPS tunnel address: `10.77.0.1/24`
- macOS included routes: `0.0.0.0/0`
- macOS IPv6 tunnel routes: disabled for this phase
- Carrier socket: `carrier_tunnel.sock` in the app-group container
- Transport: `auto`, with LiveKit DataChannel expected for usable throughput
- Security: the pairing secret is used as the tunnel PSK on macOS; the same
  value must be stored in `/etc/baleobala/vpn.psk` on the VPS

## VPS setup

Use an Ubuntu/Debian VPS with root or sudo access, public IPv4, and TUN
support.

```bash
sudo apt update
sudo apt install -y git python3-venv iproute2 iptables-persistent
sudo useradd -r -m -s /usr/sbin/nologin baleobala || true
sudo mkdir -p /opt /etc/baleobala
sudo git clone https://github.com/<you>/baleobala.git /opt/baleobala
sudo chown -R baleobala:baleobala /opt/baleobala
cd /opt/baleobala
sudo -u baleobala python3 -m venv .venv
sudo -u baleobala .venv/bin/pip install -e ".[bale]"
```

Store the exit-node Bale JWT and PSK:

```bash
sudo install -m 0640 -o root -g baleobala /dev/null /etc/baleobala/jwt.txt
sudo install -m 0640 -o root -g baleobala /dev/null /etc/baleobala/vpn.psk
sudo editor /etc/baleobala/jwt.txt
sudo editor /etc/baleobala/vpn.psk
```

Create the server TUN and NAT. Replace `eth0` with the default WAN interface
from `ip route show default`.

```bash
sudo /opt/baleobala/scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 baleobala
sudo /opt/baleobala/scripts/vpn-exit-node.sh vpn0 eth0
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-baleobala-vpn.conf
sudo sysctl --system
sudo netfilter-persistent save
```

Install `/etc/systemd/system/baleobala-vpn-exit.service`:

```ini
[Unit]
Description=baleobala VPN exit node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=baleobala
WorkingDirectory=/opt/baleobala
ExecStart=/opt/baleobala/.venv/bin/baleobala tunnel exit-node \
    --bale-jwt-file /etc/baleobala/jwt.txt \
    --tun vpn0 --wan eth0 --answer --transport auto \
    --psk-file /etc/baleobala/vpn.psk --skip-nat-setup
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
```

Then start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now baleobala-vpn-exit
journalctl -u baleobala-vpn-exit -f
```

## macOS client setup

Install dependencies and run the repo checks:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,bale,desktop]"
baleobala doctor
pytest -q
./scripts/acceptance-macos-native.sh
```

Sign in with the macOS Bale account, create/sync a pairing for the VPS Bale
peer, and install the dev-signed native app:

```bash
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save
./scripts/run-macos.sh
```

In the app:

1. Confirm the network policy uses custom DNS and transport `auto`.
2. Install the system profile.
3. Connect. The app-control helper starts the Python carrier runtime before
   the packet tunnel starts, so `carrier_tunnel.sock` is available to the
   extension.

## Validation

On macOS after connecting:

```bash
curl https://ifconfig.me
scutil --dns | head -80
```

The public IP should be the VPS IPv4 address. App diagnostics should show:

- `call_established=yes`
- `transport_selected=dc` or another selected fallback
- `route_ready=yes`
- `dns_ready=yes`
- `carrierSocketExists=true`

On the VPS:

```bash
ip addr show vpn0
sysctl net.ipv4.ip_forward
sudo iptables -t nat -nvL POSTROUTING
journalctl -u baleobala-vpn-exit -f
```
