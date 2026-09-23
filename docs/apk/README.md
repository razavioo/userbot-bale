# Bale APK reverse-engineering (jadx + Ghidra)

## jadx
- APK: `/home/emad/Downloads/bale.apk`
- Portable jadx: `/tmp/opencode/jadx/bin/jadx`
- Decompiled sources (no resources): `/tmp/opencode/bale-apk/jadx-out/sources`
- This directory:
  - `rpc_inventory.md` — Request* methods by OuterClass (~806 across 68 services)
  - `service_paths.md` — `/bale.*/*` paths from string constants (308 paths)
- Native libs: `/tmp/opencode/bale-apk/apk_extract/lib/{arm64-v8a,armeabi-v7a}`

Regenerate inventory after jadx:

```bash
/tmp/opencode/jadx/bin/jadx -d /tmp/opencode/bale-apk/jadx-out --no-res /home/emad/Downloads/bale.apk
```

## Ghidra
- Install: `~/.local/share/ghidra-dist/ghidra_11.3.2_PUBLIC`
- Headless: `~/.local/bin/ghidra-analyzeHeadless`
- Needs a full JDK (`javac`); set in `support/launch.properties`:
  `JAVA_HOME_OVERRIDE=/tmp/opencode/mamba_jdk` (micromamba openjdk 21)
- Project: `/tmp/opencode/bale-apk/ghidra-proj`

## Ghidra findings (libtmessages.45.so, AARCH64)

- Analyzed with headless Ghidra 11.3.2; export:
  `/tmp/opencode/bale-apk/ghidra-out/tmessages_funcs.txt` (9955 functions).
- Project: `/tmp/opencode/bale-apk/ghidra-proj/bale`.
- Dominant libraries: FFmpeg/libav (`av_*`, `ff_*`), libvpx/VP8/VP9, libjpeg,
  libwebp, Opus; JNI under `Java_ir_nasim_tgwidgets_editor_messenger_*`.
- Crypto symbols here (`av_aes_*`, `av_sha_*`) are media/HLS-oriented, not
  MTProto key exchange. gRPC/auth truth remains the jadx proto path above.
- MCP: `list_rpc_paths` exposes the offline `/bale.*/*` inventory (308 paths).

## Auth facts confirmed from this APK (2026-09-23)

- `RequestValidateCode.is_jwt` is `google.protobuf.BoolValue` (field 3),
  not a bare varint. Bare varint is accepted but dropped → no JWT in
  `ResponseAuth` → broken GetJWTToken 401 path.
- `ResponseAuth`: field 2 user, field 3 config, field 4 jwt (`StringValue`).
- `RequestGetJWTToken` empty body; session via `session_id` / `user_id` headers.
- Auth also has SignUp, LogOut, SignOut, GetAuthSessions, TerminateSession(s),
  RecoverPassword, ValidatePassword, 2FA, ChangePhone, DeleteAccount, etc.

## Live verification

- After the BoolValue fix, gRPC-Web `ValidateCode` returned a JWT
  (length 963) without the GetJWTToken fallback.
- Cookie diagnostics must never print JWT values (`cookie_summaries()`).
