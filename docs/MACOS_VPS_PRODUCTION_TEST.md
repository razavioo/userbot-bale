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
baleobala auth bale-login --phone +98912xxxxxxx --method browser --save --no-print-jwt
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

## April 2026 field notes: no Developer Team fallback

This section records the production-test findings from the real macOS client
and the VPS at `68.183.118.171`.

### What worked

- The macOS NetworkExtension packet tunnel still requires a valid Apple
  Developer Team/signing identity. Without that, the system-wide packet tunnel
  cannot be installed and started reliably.
- The practical fallback on this Mac is the `bale-proxy` path:

  ```bash
  baleobala bale-proxy client \
    --transport dc \
    --bale-jwt-file ~/.bale_jwt_b \
    --peer-id 1519372475 \
    --listen-host 127.0.0.1 \
    --listen-port 1080 \
    --proxy-secret-file /tmp/baleobala-vpn.psk
  ```

- With the VPS relay active, the SOCKS path was live-verified:

  ```bash
  curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
  # 68.183.118.171
  ```

- Direct, non-proxied egress from the Mac was different, so this confirmed that
  traffic through the local SOCKS proxy exited through the VPS.

### VPS relay service used for fallback

The packet-tunnel exit-node service was stopped during proxy fallback testing
to avoid answer races. The active VPS service became:

```ini
[Unit]
Description=baleobala proxy relay over Bale DataChannel
After=network-online.target
Wants=network-online.target
ConditionFileNotEmpty=/etc/baleobala/jwt.txt
ConditionFileNotEmpty=/etc/baleobala/vpn.psk

[Service]
Type=simple
User=baleobala
WorkingDirectory=/opt/baleobala
ExecStart=/opt/baleobala/.venv/bin/baleobala bale-proxy relay \
    --transport dc \
    --bale-jwt-file /etc/baleobala/jwt.txt \
    --answer \
    --answer-timeout 3600 \
    --proxy-secret-file /etc/baleobala/vpn.psk
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

The VPS was very small, with roughly 458 MiB RAM and no swap at the start of
the test. Sending broad macOS system-proxy traffic through it caused the relay
to be killed by the OOM killer. The mitigation applied on the VPS:

```bash
fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

mkdir -p /etc/systemd/system/baleobala-proxy-relay.service.d
cat >/etc/systemd/system/baleobala-proxy-relay.service.d/resource-limits.conf <<'EOF'
[Service]
MemoryMax=320M
MemoryHigh=260M
TasksMax=160
Restart=always
RestartSec=5
EOF

systemctl daemon-reload
systemctl restart baleobala-proxy-relay.service
```

The runtime was also changed to unregister closed proxy connection queues, cap
active relay connections, and time out upstream sockets so broad system-proxy
traffic cannot grow relay memory unbounded.

### macOS system proxy behavior

The macOS fallback can set Web, Secure Web, and SOCKS proxy settings to
`127.0.0.1:1080` for selected network services. The safe tested target list was:

```bash
BALEOBALA_MACOS_PROXY_SERVICES="Wi-Fi,V2BOX"
```

Do not run privileged `networksetup` commands one-by-one. macOS will ask for
the admin password once per privileged AppleScript execution, so the code now
batches all proxy changes into one `osascript ... with administrator privileges`
call. Start and restore should each require at most one admin prompt.

Safe manual restore command if the proxy is left on while no listener is up:

```bash
osascript -e 'do shell script "networksetup -setwebproxystate Wi-Fi off && networksetup -setsecurewebproxystate Wi-Fi off && networksetup -setsocksfirewallproxystate Wi-Fi off && networksetup -setwebproxystate V2BOX off && networksetup -setsecurewebproxystate V2BOX off && networksetup -setsocksfirewallproxystate V2BOX off" with administrator privileges'
```

At the end of the run, system proxy was restored to disabled for both `Wi-Fi`
and `V2BOX`.

### LaunchAgent finding

A dedicated per-user LaunchAgent for `bale-proxy client` was implemented:

```bash
baleobala vpn agent install \
  --mode proxy-client \
  --jwt-file ~/.bale_jwt_b \
  --proxy-secret-file /tmp/baleobala-vpn.psk \
  --ssl-cert-file /tmp/baleobala-macos-ca.pem

baleobala vpn agent start --mode proxy-client
```

The plist includes a stable environment (`HOME`, `PATH`, `TMPDIR`,
`SSL_CERT_FILE`), `StandardInPath=/dev/null`, stderr/stdout logs, and
`ThrottleInterval=30`.

However, on the tested Mac this LaunchAgent could not connect to the Bale
WebSocket endpoint. Diagnostics showed:

- foreground Python could connect to `next-ws.bale.ai` / `2.189.68.126:443`
- a LaunchAgent Python process could connect to unrelated hosts like
  `api.ipify.org`
- the same LaunchAgent process failed connecting to `2.189.68.126:443` with
  `OSError(9, Bad file descriptor)`

This was reproduced with a minimal Python socket script, outside the Bale code,
so it is treated as a local macOS launchd/network-context blocker rather than a
proxy runtime bug. The LaunchAgent path is committed for future work but should
not be considered production-ready on this Mac until this launchd-specific Bale
connectivity issue is resolved.

### Current operational recommendation

Until the launchd blocker is resolved, use this order for real testing:

1. Keep the VPS relay service active and healthy.
2. Start `bale-proxy client` from a normal foreground/session-managed process.
3. Confirm:

   ```bash
   curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
   ```

4. Only after the listener is up, enable macOS system proxy with the batched
   privileged helper.
5. If the listener drops, disable system proxy immediately with the one-prompt
   restore command above.

This is not equivalent to a full packet VPN: apps must honor the macOS proxy
settings or the local SOCKS proxy. UDP and raw IP traffic are not covered by
this fallback.
