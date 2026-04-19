# Bale reverse-engineering notes

Source material: [bale.apk](../bale.apk) in the repo root, version
captured on 2026-04-19. Decompiled with jadx 1.5.2 into
`re/jadx-out/`.

This note captures what was learned during the Phase 3 scaffolding
work and what remains to do to complete the headless client.

## Architecture at a glance

Bale is **not a Telegram fork**, despite the presence of
`libtmessages.45.so` in the APK (that name was misleading — it's
retained media-native code reused from an older base, but the
actual Java layer is independent of Telegram).

The real stack:

- **Base SDK:** [Nasim](https://github.com/actorapp/actor-platform),
  a fork of the Actor Platform messaging SDK. Package root:
  `ir.nasim.core.*`. Transport is MTProto-style TCP with Bale's own
  auth-key scheme.
- **App-level RPCs:** Bale's own gRPC-ish service surface generated
  from protobuf IDLs, in `ai.bale.proto.*`. Services include `Auth`,
  `Meet` (calls), `Messaging`, `Groups`, `Files`, `Premium`, `Wallet`,
  `BaleBank`, `Advertisement`, etc.
- **Voice/video calls:** [LiveKit](https://livekit.io/)'s
  Android SDK, bundled natively as
  `liblkjingle_peerconnection_so.so` and shimmed through
  `livekit.org.webrtc.*`. Standards-compliant WebRTC — no RE of the
  media layer required.
- **Extra Bale internal layer:** `sources/bale/smart_support_admin/*`
  (admin bot APIs, likely not relevant to user calls).

## What this means for the headless client

The RE work divides cleanly:

| Target                           | Needs RE? | State          |
| -------------------------------- | --------- | -------------- |
| LiveKit media transport          | No        | Done (uses livekit-rtc SDK) |
| gRPC/protobuf IDL for Bale RPCs  | No        | Protobufs are already decompiled in `re/jadx-out/sources/ai/bale/proto/` |
| Nasim-MTProto wire protocol      | Yes       | Pending        |
| Auth-key handshake               | Yes       | Pending        |
| Client identifier (api_key/hash) | Yes       | Pending        |

## Key classes in the decompile

### Calls

```
sources/ai/bale/proto/MeetOuterClass$RequestStartLiveKitCall.java
  fields: peer (OutPeer), rid (int64), video (bool),
          inviteEnable (BooleanValue)

sources/ai/bale/proto/MeetOuterClass$RequestAcceptCall.java
sources/ai/bale/proto/MeetOuterClass$RequestReceiveCall.java
sources/ai/bale/proto/MeetOuterClass$RequestJoinGroupCall.java
sources/ai/bale/proto/MeetOuterClass$RequestLeaveGroupCall.java
sources/ai/bale/proto/MeetOuterClass$RequestGetWssURL.java
sources/ai/bale/proto/MeetOuterClass$ResponseCall.java
sources/ai/bale/proto/MeetOuterClass$ResponseGetWssURL.java
sources/ai/bale/proto/MeetStruct$GroupCall.java
sources/ai/bale/proto/MeetStruct$SipCall.java
sources/ai/bale/proto/MeetOuterClass$UpdateCallStatusChanged.java
```

`RequestStartLiveKitCall` is the call-initiation RPC. The response
(likely `ResponseCall`) carries the LiveKit access token; the
`RequestGetWssURL` RPC likely returns the signalling URL. These
shapes need to be confirmed by reading the response class and
comparing with a mitmproxy capture.

### Auth

```
sources/ai/bale/proto/AuthOuterClass$RequestStartPhoneAuth.java
sources/ai/bale/proto/AuthOuterClass$RequestValidateCode.java
sources/ai/bale/proto/AuthOuterClass$RequestSignUp.java
sources/ai/bale/proto/AuthOuterClass$RequestGetJWTToken.java
sources/ai/bale/proto/AuthOuterClass$ResponseAuth.java
sources/ai/bale/proto/AuthOuterClass$ResponseGetJWTToken.java
sources/ai/bale/proto/AuthOuterClass$ResponseStartPhoneAuth.java
```

Classic phone + SMS + JWT flow. `ResponseAuth` likely carries the user
record, initial session config, and JWT bearer. `RequestGetJWTToken`
is the refresh RPC.

### Transport (Nasim-MTProto)

```
sources/ir/nasim/core/runtime/mtproto/ConnectionEndpoint.java
sources/ir/nasim/core/network/util/ConnectionEndpoints.java
sources/ir/nasim/core/network/sslpinning/*
sources/ir/nasim/core/network/util/TlsHash.java
```

`ConnectionEndpoint` defines one endpoint; `ConnectionEndpoints` is
the list loaded from config. The TLS pinning code (`TlsHash`,
`TlsHashItem`) protects the handshake from MITM — to capture a real
session with mitmproxy, the pin check must be bypassed either by
patching the APK or using Frida at runtime to no-op `TlsHashItem`.

### Live endpoints (verified 2026-04-19)

The app bootstraps from three URLs (hard-coded in the APK):

| URL                                                    | Purpose                              |
| ------------------------------------------------------ | ------------------------------------ |
| http://ep.bale.ai/ep/endpoints-android.json            | RPC server list + TLS pin hashes     |
| http://hash.bale.ai/hashes-android.json                | CDN host pins (video/file cdns)      |
| https://assets.bale.ai/configs.json                    | Client gating + rpcDisabled list     |

The endpoints response at the time of writing:

```
r
tls@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-ssl-c002.bale.ai@2.189.68.117:443#1013
tcp@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-c002.bale.ai@2.189.68.106:443#1014
```

Format is `tls|tcp @<sha256_pin> :// <hostname> @ <ip> : <port> #<id>`.
Parsed by [src/baleobala/bale/endpoints.py](../src/baleobala/bale/endpoints.py);
`BaleApiClient.bootstrap()` returns the list at runtime.

`configs.json` currently disables these RPCs server-side, so a
headless client must not assume they work: `RequestSend`,
`MessageReceived`, `RequestGetUserOrganizationInfo`,
`RequestIsPremiumBatch`, `RequestGetMessagesRepliesInfo`,
`RequestUnsubscribeFromThreadUpdates`, `RequestGetBotStories`.
`RequestStartLiveKitCall` is **not** in the disabled list, so calls
remain accessible.

## Endpoint pin format (resolved 2026-04-19)

Empirical test against `rpc-ssl-c002.bale.ai:443`
(`scripts/smoketest_transport.py`) showed that **none** of the common
TLS hash variants match the 32-byte hex in the endpoints response:

- SHA-256(cert-DER):    `b2bb944a...`  — no match
- SHA-256(SPKI-DER):    `edcc33d9...`  — no match
- SHA-256(RSA-modulus): `d54a5075...`  — no match
- Expected pin:         `6d9ba5c5...`

Conclusion: the pin is **not** a TLS-layer pin. It is the
Actor-Platform-style **server RSA pubkey fingerprint** consumed
during the MTProto auth-key handshake. At the TLS layer Bale
connects with the standard public CA chain (no pinning); trust flows
through the MTProto handshake, which verifies the server holds the
private key matching this fingerprint.

This aligns with upstream Actor Platform where `Endpoint.key` carries
the MTProto server identity. `src/baleobala/bale/mtproto/endpoint.py`
is updated accordingly: TLS is plain, pin flows into the next layer.

## RE milestones to finish the headless client

1. ✅ **Endpoint bootstrap.** Done — see `endpoints.py`. Live-tested
   against bale.ai on 2026-04-19.
2. ✅ **Live TLS endpoint connect.** Done — see
   `src/baleobala/bale/mtproto/endpoint.py`. Live-tested same day.
3. **Extract the client identifier (api_id / device_hash).** Not
   found as a literal in the decompile; likely derived at runtime in
   `ir.nasim.core.modules.*` initialization or passed as a
   session-negotiated value. Needs a mitmproxy capture to confirm.
3. **Capture a real session.** Install the APK on a rooted device or
   Waydroid. Use Frida to bypass `TlsHashItem.matches(...)` (return
   true unconditionally), then route traffic through mitmproxy. Log
   in with a real account; confirm the RPC shapes above match the
   wire.
4. **Implement the MTProto transport in Python.** Port
   `ir.nasim.core.runtime.mtproto.*` — the parts that matter are
   AuthKey negotiation, message-id, and the frame format. Nasim's
   upstream (Actor Platform) is on GitHub and its MTProto variant
   is documented, so this is a porting job, not a full RE.
5. **Wire up `BaleAuth.start_phone_auth` /
   `BaleApiClient.fetch_livekit_credentials`** using the protos
   cataloged above. The JSON-equivalent shapes are:

   ```python
   # Fetch
   req = RequestStartLiveKitCall(
       peer=OutPeer(user_id=..., access_hash=...),
       rid=secrets.randbits(63),  # random call id
       video=False,
       invite_enable=BooleanValue(value=False),
   )
   resp = client.rpc(req)
   return LiveKitCredentials(
       url=resp.livekit_url,
       token=resp.livekit_token,
       room=resp.room_name,
       identity=str(resp.user_id),
   )
   ```
5. **Replace the `--livekit-url` / `--livekit-token` CLI options with
   a `--peer` option** once the credentials fetch works end-to-end.
   Keep the manual-token path around for debugging.

## Legality

The RE scope above is for **interoperability** (running the user's
own tool over their own calls with their own accounts). This is
explicitly allowed under DMCA §1201(f) (US) and the EU Software
Directive Art 6. The scaffolded code and these notes deliberately do
not cover:

- Account automation (creating Bale accounts programmatically).
- Bypassing flood-wait, rate-limits, or spam controls.
- Impersonating other users or joining calls without invitation.
