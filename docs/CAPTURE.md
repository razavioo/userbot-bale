# Capturing Bale wire traffic

The headless Bale client's auth and call layers need a small number
of real mitmproxy captures to finalize. The captures anchor the
framing, the auth-key derivation, and the protobuf envelope against
ground truth — shipping without them means writing code that's
probably right, which for a crypto handshake is worse than writing
no code at all.

This doc is the reproducible workflow for producing those captures.
A single successful run yields everything Phases 5–7 need.

## Required captures

1. **Cold start → endpoint bootstrap** (HTTP, trivial).
   Confirms our `endpoints.fetch_endpoints()` matches the client.
2. **Phone auth** — SMS code flow.
   Payload for `RequestStartPhoneAuth`, `RequestValidateCode`,
   `ResponseAuth`.
3. **Outbound voice call** —
   `RequestStartLiveKitCall` + `ResponseCall` (contains LiveKit URL
   and token).
4. **Incoming call notification** — the update stream frame for
   `UpdateCallAction`.

Total on-the-wire volume: < 200 kB. One 5-minute session produces all
four.

## Prerequisites

| Tool           | Purpose                                     |
| -------------- | ------------------------------------------- |
| Waydroid OR a rooted Android phone | Run the Bale APK     |
| `frida-server` 16.x+ on the device  | Bypass TLS pinning  |
| `frida-tools` on the host           | Drive Frida         |
| `mitmproxy` on the host             | Capture + decrypt   |
| The `bale.apk` from this repo root  | The app itself      |

## Step-by-step

### 1. Install mitmproxy + Frida

```bash
pip install mitmproxy frida-tools
```

On the Android target (rooted/Waydroid), install frida-server and run
it as root on port 27042 (default).

### 2. Start mitmproxy

```bash
mitmdump -p 8080 -w captures/bale-$(date +%F-%H%M%S).mitm
```

This listens on `0.0.0.0:8080`. Note the CA certificate it generates
(default `~/.mitmproxy/mitmproxy-ca-cert.pem`) — the Android device
needs to trust it.

### 3. Install mitmproxy's CA on Android

For a rooted device:
```bash
adb push ~/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/
adb shell su -c 'cp /sdcard/mitmproxy-ca-cert.pem /system/etc/security/cacerts/$(openssl x509 -inform PEM -subject_hash_old -in ~/.mitmproxy/mitmproxy-ca-cert.pem | head -1).0'
adb shell su -c 'chmod 644 /system/etc/security/cacerts/*.0 && reboot'
```

For Waydroid: inject into the overlay FS per
https://github.com/waydroid/waydroid/wiki/Advanced-Usage#add-ca-certificates.

### 4. Route Android traffic through mitmproxy

```bash
adb shell settings put global http_proxy <host-ip>:8080
```

### 5. Bypass Bale's TLS pinning with Frida

Nasim's code does an extra pin check above the Android TLS trust
store (classes under `ir.nasim.core.network.sslpinning.*`). Use the
well-maintained universal Android pin-bypass hook:

```bash
# On the host:
frida --codeshare akabe1/frida-multiple-unpinning -U -f ai.bale.messenger
```

If Bale also pins inside its native library `libtmessages.45.so`,
extend the hook with:

```javascript
// Hook the native pin check (address TBD via Ghidra on libtmessages.45.so)
Interceptor.replace(Module.findBaseAddress('libtmessages.45.so').add(0xXXXX),
  new NativeCallback(() => 0, 'int', []));
```

### 6. Drive the captures

With mitmproxy running, Frida attached, and proxy configured:

1. Kill-start the Bale app. Watch mitmproxy for the
   `ep.bale.ai/ep/endpoints-android.json` request.
2. Log out (if logged in) and log back in with a phone number. Note
   the SMS code flow.
3. Place a test call to another Bale account. Let it ring and answer.
4. From the other account, place a call to the captured account.
   Observe the incoming-call update frame.

Stop `mitmdump`. The `.mitm` file now contains everything.

### 7. Extract the bytes for replay tests

```bash
mitmdump -r captures/bale-<ts>.mitm -s scripts/capture/extract_rpcs.py
```

A small script (to be written as part of Phase 4) iterates over the
flows and writes each RPC request/response payload to
`captures/<ts>/<rpc_name>.bin`. Those bins become the corpus for the
replay tests in `tests/test_bale_mtproto.py`.

## What the captures unblock

| Capture                       | Unblocks                                  |
| ----------------------------- | ----------------------------------------- |
| Endpoint bootstrap            | Already verified; confirms pin parsing    |
| RequestStartPhoneAuth         | Phase 5 (framing) + Phase 6 (auth)        |
| RequestValidateCode           | Phase 6 (auth completion, JWT shape)      |
| RequestStartLiveKitCall       | Phase 7 (call init, LiveKit token shape)  |
| UpdateCallAction              | Phase 7 (inbound call handling)           |

Capture once, code for a week. The captures should be kept in
`captures/` (gitignored; contains your own session secrets).

## Safety

- Run captures only against **your own** account. The SMS you
  receive is yours; the JWT you observe is yours; the call you place
  is between accounts you own. Staying inside this envelope keeps the
  work under DMCA §1201(f) / EU Software Directive Art 6 interop
  exceptions.
- Delete captures when the replay corpus is stable — the raw `.mitm`
  file contains your auth key, a secret. Keep only the per-RPC `.bin`
  files in version control (and consider even those to be sensitive).
