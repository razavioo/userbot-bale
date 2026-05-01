# baleobala

`baleobala` هسته‌ی ارتباطی برای Bale است — یک پروکسی SOCKS5 که ترافیک شبکه را از طریق DataChannel یک تماس Bale بین دو حساب کاربری انتقال می‌دهد. هیچ سرور میانجی مجزایی لازم نیست؛ کانال مستقیم‌ترین مسیر ممکن است.

---

## اتصال سریع (macOS)

> **پیش‌نیاز:** سرویس relay سمت VPS باید روشن باشد (دستور آن پایین‌تر آمده).

### وصل شدن

```bash
bash scripts/run-proxy-client.sh
```

یا اگر LaunchAgent نصب شده (بعد از login خودکار بالا می‌آید):

```bash
launchctl start ai.baleobala.proxy-client
```

وقتی پروکسی آماده شد این پیام‌ها در log می‌آیند:

```
call_established
transport_selected=dc
proxy_listening=127.0.0.1:1080
system_proxy_active=127.0.0.1:1080
```

Wi-Fi SOCKS proxy سیستم به‌صورت خودکار روشن می‌شود.

### تست

```bash
# باید IP سرور VPS را برگرداند (68.183.118.171)
curl --socks5-hostname 127.0.0.1:1080 https://ifconfig.me
```

### قطع کردن

```bash
launchctl stop ai.baleobala.proxy-client
```

یا اگر foreground اجرا شده: `Ctrl-C`

هر دو حالت Wi-Fi proxy سیستم را بلافاصله restore می‌کنند (SIGTERM → Python cleanup).

### وضعیت

```bash
bash scripts/install-launchagent-client.sh --status
bash scripts/install-launchagent-client.sh --logs
```

---

## معماری

```
Mac (Account B)                    VPS / Ubuntu (Account A)
────────────────────               ────────────────────────
bale-proxy system                  bale-proxy relay
  │  --peer-id 1519372475            │  --answer
  │  --transport dc                  │  --transport dc
  │                                  │
  └──── Bale LiveKit call ───────────┘
         (DataChannel / WebRTC)

  SOCKS5 :1080  <──── tunnel ────>  TCP to internet
  Wi-Fi proxy ON                    exit node = VPS IP
```

- **Account A** (relay): بر روی VPS اجرا می‌شود، تماس ورودی را جواب می‌دهد و ترافیک TCP را به اینترنت forward می‌کند.
- **Account B** (client): روی Mac اجرا می‌شود، به Account A زنگ می‌زند و یک SOCKS5 server روی `127.0.0.1:1080` باز می‌کند و macOS Wi-Fi proxy را فعال می‌کند.
- **Transport**: WebRTC DataChannel (`--transport dc`) با رمزنگاری PSK (`--proxy-secret-file`).

---

## نصب و راه‌اندازی

### پیش‌نیازها

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,bale,desktop]"
```

### نصب LaunchAgent (اجرای خودکار بعد از login)

```bash
BALEOBALA_JWT_FILE=~/.bale_jwt_b \
BALEOBALA_PSK_FILE=~/.baleobala/baleobala-vpn.psk \
BALEOBALA_RELAY_PEER_ID=1519372475 \
bash scripts/install-launchagent-client.sh --install
```

متغیرهای مهم:

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `BALEOBALA_JWT_FILE` | `~/.bale_jwt_b` | JWT حساب client (Mac) |
| `BALEOBALA_PSK_FILE` | `~/.baleobala/baleobala-vpn.psk` | Pre-shared key رمزنگاری |
| `BALEOBALA_RELAY_PEER_ID` | `1519372475` | user_id حساب relay (VPS) |
| `BALEOBALA_LISTEN_PORT` | `1080` | پورت SOCKS5 محلی |
| `BALEOBALA_SERVICE` | `Wi-Fi` | سرویس شبکه macOS |

### راه‌اندازی relay روی VPS (Ubuntu)

```bash
# یک بار اجرا کنید تا systemd service نصب شود
ssh root@<VPS-IP> bash << 'EOF'
cat > /etc/systemd/system/baleobala-relay.service << 'SVC'
[Unit]
Description=baleobala relay proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/baleobala-run/project
ExecStart=/root/baleobala-run/project/.venv/bin/python -u -m baleobala.cli \
    bale-proxy relay \
    --transport dc \
    --bale-jwt-file /root/baleobala-run/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file /root/baleobala-run/baleobala-vpn.psk \
    --ws-ssl-no-verify
Restart=always
RestartSec=10
SVC
systemctl daemon-reload
systemctl enable --now baleobala-relay.service
EOF
```

```bash
# وضعیت relay
ssh root@<VPS-IP> systemctl status baleobala-relay.service
```

---

## دستورات رایج

```bash
# احراز هویت
baleobala auth bale-login --phone +98912xxxxxxx --method browser --headful --save
baleobala auth status

# سلامت‌سنجی
baleobala doctor

# پروکسی دستی (بدون LaunchAgent)
bash scripts/run-proxy-client.sh

# مدیریت LaunchAgent
bash scripts/install-launchagent-client.sh --status
bash scripts/install-launchagent-client.sh --logs
bash scripts/install-launchagent-client.sh --start
bash scripts/install-launchagent-client.sh --stop
bash scripts/install-launchagent-client.sh --uninstall

# تست دودی دو حساب
baleobala vpn live-smoke \
    --caller-jwt-file ~/.bale_jwt_b \
    --callee-jwt-file ~/.bale_jwt_a \
    --callee-peer-id 1519372475
```

---

## دستورات پیشرفته

```bash
# relay سمت سرور (دستی)
baleobala bale-proxy relay \
    --bale-jwt-file ~/.bale_jwt_a \
    --answer --answer-timeout 86400 \
    --proxy-secret-file ~/.baleobala/baleobala-vpn.psk \
    --ws-ssl-no-verify

# client سمت مک (دستی، با system proxy)
baleobala bale-proxy system \
    --bale-jwt-file ~/.bale_jwt_b \
    --peer-id 1519372475 \
    --proxy-secret-file ~/.baleobala/baleobala-vpn.psk \
    --service Wi-Fi \
    --ws-ssl-no-verify

# loopback test
baleobala loopback "hello" "world"
baleobala tunnel-loopback
```

---

## رفع اشکال

| علامت | احتمال | راه‌حل |
|---|---|---|
| `No module named 'cryptography'` | dependency نصب نیست | `pip install cryptography>=42` |
| `WS did not connect within 15s` | شبکه یا Bale WS قطع | `--ws-ssl-no-verify` یا بررسی اتصال |
| `OSError: EINVAL` روی TCP_NODELAY | macOS daemon context | خودکار patch می‌شود (ws_client.py) |
| `BrokenPipeError` در SSL handshake | SIGPIPE بدون handler | wrapper script آن را handle می‌کند |
| `proxy_listening` نمی‌آید | relay جواب نداد | مطمئن شوید relay سمت VPS روشن است |
| Wi-Fi proxy بعد از stop خاموش نشد | SIGKILL به جای SIGTERM | `launchctl stop` (نه `kill -9`) |

---

## مستندات

- [Getting Started](docs/SETUP.md)
- [Install and Bootstrap](docs/INSTALL.md)
- [Windows Development](docs/WINDOWS_DEVELOPMENT.md)
- [Bale Headless Notes](docs/BALE_HEADLESS.md)
- [Native macOS Scaffold](native/macos/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [macOS client + Linux VPS production-test](docs/MACOS_VPS_PRODUCTION_TEST.md)

---

## واژه‌نامه

| کد | اصطلاح VPN | معنی |
|---|---|---|
| `ControlService` / `ProvisioningService` | control plane | مدیریت auth، pairing، و credential |
| `CarrierSession` | carrier / call session | session تماس زنده |
| `Transport` | bearer transport | لایه انتقال بایت (DataChannel، audio، QR) |
| `TransportPool` / `TransportChain` | transport selector | انتخاب و سوئیچ بین transport ها |
| `Tunnel` | tunnel engine | framing، ARQ، reassembly |
| `VpnRunner` | tunnel runner | اتصال TUN به tunnel |
| `TunnelBridge` / `TunnelService` | tunnel service boundary | سطح IPC |
| `VpnBackend` | VPN backend adapter | لایه یکپارچگی platform-specific |
| `CredentialEpoch` | credential lease | پنجره credential کوتاه‌مدت |
| `TUN` | kernel tunnel interface | دستگاه OS برای بسته‌های IP |
