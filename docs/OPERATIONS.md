# baleobala Operations Guide

## Deployment

### Prerequisites

- A Linux VPS (Ubuntu 22.04+ recommended) reachable via SSH
- Python 3.11+, virtualenv, and systemd
- One or more Bale JWT files (see [BALE_HEADLESS.md](./BALE_HEADLESS.md))
- `pyyaml` installed locally (`pip install pyyaml`)

### Topology manifest

All node addresses and roles live in [`deploy/inventory.yaml`](../deploy/inventory.yaml). Edit that file to add, remove, or reconfigure nodes before deploying.

```yaml
coordinator:
  host: <your-server-ip>
  user: root
  coordinator_jwt: account-1.jwt
  ...

relays:
  - name: relay-ir-1
    host: <your-server-ip>
    region: ir
    relay_jwts: [account-2.jwt, account-3.jwt]
    ...
```

### Deploy all nodes

```bash
python3 scripts/deploy.py
```

### Deploy a single node

```bash
python3 scripts/deploy.py --node relay-ir-1
```

### Dry-run (print commands without executing)

```bash
python3 scripts/deploy.py --dry-run
```

### Adding a new relay

1. Obtain a Bale JWT for the new relay account (see [BALE_HEADLESS.md](./BALE_HEADLESS.md)).
2. Copy the JWT file to `/root/.baleobala/accounts/account-N.jwt` on the server.
3. Add the relay entry to `deploy/inventory.yaml`.
4. Enroll it with the coordinator:
   ```bash
   baleobala coordinator enroll --jwt-file /root/.baleobala/accounts/account-N.jwt
   ```
5. Deploy: `python3 scripts/deploy.py --node <relay-name>`
6. Verify: `baleobala coordinator list-relays`

---

## Metrics

Each relay exposes a Prometheus-format metrics endpoint on `127.0.0.1:9201`:

```bash
curl 127.0.0.1:9201/metrics
```

Each coordinator exposes metrics on `127.0.0.1:9202`.

Key counters:

| Metric | Description |
|--------|-------------|
| `baleobala_relay_calls_total{result,reason}` | Inbound calls accepted/rejected |
| `baleobala_relay_sessions_active` | Current active VPN sessions |
| `baleobala_coordinator_assigns_total{result}` | HELLO→ASSIGN outcomes |
| `baleobala_coordinator_relays_online` | Registered healthy relays |
| `baleobala_livekit_session_starts_total{outcome}` | LiveKit session starts |

Key gauges:

| Metric | Description |
|--------|-------------|
| `baleobala_relay_in_use_per_account{account}` | Sessions per JWT slot |
| `baleobala_account_active_age_seconds{account}` | Age of `_account_active` flag; alert if > 30 s |

### Alert thresholds

- `baleobala_account_active_age_seconds > 30` — stale active flag; relay likely needs restart.
- `baleobala_relay_sessions_active == 0` for > 5 min after recent `assigns_total` — relay accepted calls but opened no tunnels.
- `baleobala_coordinator_relays_online == 0` — all relays offline; clients will get no ASSIGN.

---

## Health check

```bash
curl 127.0.0.1:9201/healthz   # relay
curl 127.0.0.1:9202/healthz   # coordinator
```

Returns `{"ok": true, "uptime": ..., "active_sessions": N, "stale_active_flags": [...]}`.

---

## Logs

### Log format

Set `BALEOBALA_LOG_FORMAT=json` in the systemd unit for structured logs. Default is `text` (human-readable).

### Finding a full call trace

Every inbound call gets a `correlation_id` (UUID4) logged at every lifecycle step. To trace one call end-to-end:

```bash
journalctl -u baleobala-relay | grep correlation_id=<uuid>
```

### Common log patterns

| Pattern | Meaning |
|---------|---------|
| `vpn-mesh: up` | Relay process started |
| `vpn-mesh: incoming call` | New Bale call received |
| `vpn-mesh: expect_client sent` | Relay told coordinator it's ready |
| `vpn-mesh: tunnel up` | VPN tunnel established with client |
| `vpn-mesh: session reaped` | Session cleaned up after disconnect |
| `coordinator: assigned` | Coordinator sent ASSIGN to client |

---

## Incident triage

### "Relay accepted calls but no tunnels came up"

1. Check `baleobala_account_active_age_seconds` — if stuck > 30 s, the active flag leaked.
2. `journalctl -u baleobala-relay | grep "stale_active"` — the reaper logs when it force-clears.
3. If persistent, restart the relay: `systemctl restart baleobala-relay`.

### "Clients keep getting no ASSIGN"

1. Check `baleobala_coordinator_relays_online` — if 0, no relay is registered.
2. Check relay systemd status: `systemctl status baleobala-relay`.
3. Check the relay's `ONLINE` heartbeat frequency in coordinator logs: should appear every 30 s.
4. If relay is up but coordinator doesn't see it, check HMAC secret: re-enroll with `baleobala coordinator enroll`.

### "Call drops after ~30 minutes"

LiveKit keepalive is wired into `tunnel up`, `tunnel exit-node`, and `tunnel exit-node-mesh` via `LiveKitKeepalive`. This usually points to:

1. The relay or client process was killed (OOM, systemd timeout).
2. Carrier connectivity interrupted (check `vpn status` and LiveKit connectivity).
3. The Bale session token expired (check auth logs, re-login if needed).

### "TUN permission denied"

```bash
ls /dev/net/tun           # must exist
sudo modprobe tun         # if missing
sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 "$USER"
```

---

## Reconnect policy (per client)

| Client | Policy |
|--------|--------|
| Android | Exponential backoff: base 3 s, max 30 s, resets after 60 s stable uptime |
| macOS | Delegates to `NEPacketTunnelProvider` `.reasserting` state (OS-managed) |
| PyQt / CLI | No auto-reconnect; user must re-run or restart the process |

The Android backoff constants live in `BaleVpnService.kt` (`RECONNECT_BASE_DELAY_MS`, `RECONNECT_MAX_DELAY_MS`, `RECONNECT_STABLE_RESET_MS`).

---

## Credential rotation

Relay credentials are short-lived epochs managed by `src/baleobala/control/provisioning.py`:

- `approve_authorization()` — ensures an active `CredentialEpoch` exists.
- `refresh_credentials()` — rotates immediately.
- Revocation rotates the epoch so previously issued secrets stop working.

Each epoch carries `issued_at`, `refresh_after`, and `expires_at`. Clients should expect renewal; do not hard-code tokens.

---

## Version

The canonical version string is in `VERSION` at the repo root. It is read by:

- Python: `src/baleobala/__init__.py`
- pyproject.toml: `version`
- Android: `versionName` in `app/build.gradle.kts` via `rootProject.file("../../VERSION")`
