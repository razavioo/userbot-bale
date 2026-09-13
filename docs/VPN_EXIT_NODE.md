# Exit-node deployment (VPS)

Checklist for running a persistent userbot-bale VPN exit node on a cloud
VPS.

## 1. Install

```bash
sudo apt update
sudo apt install -y python3-venv iproute2 iptables-persistent
git clone https://github.com/razavioo/userbot-bale.git
cd userbot-bale
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[bale]"
```

## 2. Bale JWT

Acquire an access-token JWT for the exit-node's Bale account with `userbot-bale auth bale-login` on
a trusted machine. Export it only for this file-based service, then store it outside `$HOME`:

```bash
sudo install -m 0600 -o root -g root /dev/null /etc/userbot-bale/jwt.txt
sudo vi /etc/userbot-bale/jwt.txt   # paste the JWT
```

## 3. Kernel + NAT

```bash
sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 "$USER"
sudo ./scripts/vpn-exit-node.sh vpn0 eth0      # replace eth0 with your WAN
sudo netfilter-persistent save                 # persist iptables rules
echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.d/99-userbot-bale-vpn.conf
```

## 4. systemd unit

`/etc/systemd/system/userbot-bale-vpn-exit.service`:

```ini
[Unit]
Description=userbot-bale VPN exit node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=userbot-bale
WorkingDirectory=/opt/userbot-bale
ExecStart=/opt/userbot-bale/.venv/bin/userbot-bale tunnel exit-node \
    --bale-jwt-file /etc/userbot-bale/jwt.txt \
    --tun vpn0 --wan eth0 --answer --skip-nat-setup
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
```

Create the `userbot-bale` user (`sudo useradd -r -s /usr/sbin/nologin userbot-bale`),
chown the checkout and JWT to it (the JWT file must be readable by that
user, e.g. `chgrp userbot-bale /etc/userbot-bale/jwt.txt && chmod 0640 ...`),
then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now userbot-bale-vpn-exit
journalctl -u userbot-bale-vpn-exit -f
```

## 5. Client-side routing gotchas

The VPS is reachable over the same Bale carrier you're tunneling, so
pin Bale's hosts to the real gateway on the *client* side:

```bash
gw=$(ip route show default | awk '/default/ {print $3; exit}')
sudo ip route add next-ws.bale.ai via "$gw"
sudo ip route add <livekit-SFU-hostname> via "$gw"
```

Otherwise the tunnel tries to carry its own carrier → deadlock.

## 6. Monitoring

```bash
sudo iptables -t nat -nvL POSTROUTING     # MASQUERADE hit counter
ip -s link show vpn0                      # tun counters
journalctl -u userbot-bale-vpn-exit -f       # tunnel logs
```
