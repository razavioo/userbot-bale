# Desktop VPN Delivery Plan

## Summary

The product target is a desktop-first VPN experience for Linux and macOS that uses Bale as the carrier but hides that complexity from the user.

The first release should feel like a normal VPN:

- install the app,
- sign in with Bale,
- pair a relay once,
- connect,
- and route all device traffic through the tunnel.

The current repo already has the tunnel, proxy, and carrier split. The Linux TUN backend is now wired into the control plane. The remaining work is to finish the auth/pairing layer, keep the macOS packet-tunnel path as the default system-VPN backend, preserve the proxy bridge as fallback/debug mode, and package the result for easy install.

The repo now also has a first-pass product control plane:

- `auth` stores a Bale session locally,
- `pair` manages relay pairing records,
- `relay` saves relay runtime settings,
- and `vpn` orchestrates the current managed proxy-backed path.

That control plane is the bridge between the current proxy tunnel and the future native Linux/macOS VPN backends.

This document now serves as the execution plan for the remaining test and development work around the Linux netns harness, the real Bale-backed proxy/tunnel flows, and the final product-level verdicts.

On macOS, the packet-tunnel backend is now the primary system-VPN path. The native extension consumes the shared tunnel profile, including the route and DNS plan, so the app target and packet-tunnel target stay aligned. The fallback proxy path still exists for debugging and for systems where the native path is not available.

If a paired relay already exists locally, `vpn up --backend proxy` will select it automatically so the first-run flow stays simple. The macOS release also gets a LaunchAgent install path so the VPN can come back on login without extra steps.

## Implementation Goals

- Keep carrier logic, tunnel logic, and VPN logic separate.
- Treat Bale as the media/control carrier only.
- Add a desktop app as the primary UI and keep CLI commands for advanced users and automation.
- Support two release modes:
  - `VPN` as the main full-device experience.
  - `Proxy` as the fallback/debug transport.
- Make relay setup durable with saved pairing and secure credential storage.
- Remove manual JWT capture from the end-user flow.
- Turn the current generic netns harness into a real acceptance runner that can decide pass/fail for a Bale session without human interpretation.
- Standardize readiness markers, smoke payloads, artifact bundles, and failure classes so CI and release validation use the same truth source.

## Execution Phases

### Phase 1: Product Acceptance Runner
- Add a higher-level `vpn netns-session` runner that executes the real `proxy-pair` and `tunnel-pair` flows end-to-end.
- Accept server/client JWTs, `peer_id`, proxy secret or PSK, transport preference, timeout budget, and artifact path as explicit inputs.
- Produce a final verdict JSON with at least:
  - `call_established`
  - `transport_selected`
  - `data_flow_ok`
  - `teardown_clean`
  - `failure_class`
  - `artifact_bundle`
- Keep `vpn netns-scenario` as the scenario-shape command, but ensure it exposes the real readiness markers and smoke definition that the runner uses.

### Phase 2: Real Readiness Markers
- Replace `echo ready` style readiness with stable log/runtime markers.
- Standardize the minimum set of markers:
  - `call_established`
  - `transport_selected=<name>`
  - `proxy_listening=<host:port>`
  - `tunnel_up=<tun_name>`
  - `teardown_done`
- Make readiness status derivable from the same markers used by the session runner.
- Add stable failure modes for marker extraction and runtime setup, including `auth_expired`, `call_timeout`, and `transport_timeout`.

### Phase 3: Real Payload Flow
- Upgrade proxy smoke from “listener open” to “TCP CONNECT and echo success through proxy”.
- Use a real echo service in the server namespace, or a third namespace when that keeps the test deterministic.
- For tunnel-pair, send real payload through the tunnel after TUN is up.
- Verify at least one of ICMP or UDP payload traversal for the tunnel path.
- When full-device mode is enabled, add DNS and TCP probes plus route verification.
- Include `proxy_port_open`, `packet_flow_ok`, and `dns_failed` style outcomes in the report surface where relevant.

### Phase 4: Linux Realistic Path
- `vpn netns-session --kind tunnel-pair` now owns the Linux full-device path by default.
- The runner is responsible for:
  - TUN create and attach in the correct namespace
  - route programming
  - per-namespace DNS setup
  - NAT and host-route exemptions
- Manual scripts stay as debug references only; they are no longer the normal orchestration path.
- Infrastructure failures should continue to classify deterministically, including `missing_iproute`, `missing_tun`, `missing_iptables`, `permission_denied`, `route_program_failed`, `dns_config_failed`, `nat_setup_failed`, `host_route_failed`, and `teardown_leak`.

### Phase 5: Artifact Bundling and Triage
- Bundle every run with:
  - server log
  - client log
  - selected transport
  - scenario input
  - netns command transcript
  - route and DNS snapshot
  - smoke transcript
  - final verdict JSON
- Add a deterministic analyzer that classifies runs into:
  - `product_bug`
  - `infra_flake`
  - `carrier_instability`
- Keep AI summarization downstream of the bundle, not as part of runtime truth.

### Phase 6: CI and Release Validation
- Keep daily CI focused on:
  - in-memory tests
  - harness unit tests
  - analyzer tests
  - netns runner tests with fakes or dry-run setup
- Run a Linux nightly acceptance gate on a privileged runner:
  - real `proxy-pair`
  - then real `tunnel-pair`
  - upload both artifact bundles
  - fail unless both bundles classify as `accepted_flow`
- Preserve a small two-device smoke test for release confirmation only.

## Public Interfaces

### CLI

- `vpn netns-session` becomes the product acceptance runner.
- `vpn netns-session` should return verdict fields for:
  - `call_established`
  - `transport_selected`
  - `data_flow_ok`
  - `teardown_clean`
  - `failure_class`
  - `artifact_bundle`
- `vpn netns-scenario` should return the readiness markers and smoke definition for the selected scenario, not only command templates.

### Runtime Status Contract

The readiness/status model should include:

- `call_established`
- `transport_selected`
- `carrier_latency_ms` or a stable placeholder field
- `data_flow_ok`
- `teardown_clean`
- `failure_class`

These fields must be derivable from the runner and its artifact bundle, not only from backend state.

### Acceptance Truth

- Linux nightly `vpn netns-session` is the primary automated trust gate.
- The bundle verdict is the product source of truth.
- Human or AI summaries are downstream of the bundle and must never replace runtime markers or verdict fields.
- The exact marker protocol is a public compatibility surface:
  - `call_established`
  - `transport_selected=<name>`
  - `proxy_listening=<host:port>`
  - `tunnel_up=<tun_name>`
  - `teardown_done`

## Test Strategy

- Acceptance path 1: `proxy-pair`
  - two namespaces
  - real Bale call
  - real relay/client processes
  - transport selection recorded
  - SOCKS or CONNECT path carries payload successfully
  - teardown clean
- Acceptance path 2: `tunnel-pair`
  - two namespaces with a real TUN
  - real Bale call
  - transport selection recorded
  - tunnel up marker
  - real ICMP/UDP/TCP payload crosses the tunnel
  - DNS and route checks when full-device mode is active
- Failure-path regression
  - expired JWT
  - incorrect `peer_id`
  - answer timeout
  - datachannel unavailable with fallback
  - proxy listener up but payload failure
  - tunnel up marker but packet flow failure
  - teardown failure or leak
  - missing `iproute`, missing TUN, insufficient privileges
- Analyzer tests
  - incomplete bundle -> `infra_flake`
  - call ok but payload fail -> `product_bug`
  - missing call credentials or unstable negotiation -> `carrier_instability`

## Release Validation Notes

- Nightly Linux acceptance is the primary automated gate.
- Two-device smoke remains a release-confirmation step only.
- `vpn analyze-bundle` and `vpn verdict` must consume the same bundle schema without special-case fallback logic.

## Assumptions

- The generic harness work is effectively done; the remaining work is product-specific validation, not more framework design.
- The immediate priority order is `proxy-pair` real flow, real readiness markers, `tunnel-pair` real flow, artifact analyzer, then two-device validation.
- If the CLI/runtime needs stable readiness log lines, that is part of the remaining missing work and should be implemented rather than deferred.
- The end state is a Linux run that can produce a product-level pass/fail verdict for both call setup and data transfer without human judgment.
