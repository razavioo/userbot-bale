import Foundation
import SwiftUI

struct AuthState: Equatable {
    var loggedIn: Bool = false
    var phone: String = ""
    var accountPath: String = ""
}

struct ProxyState: Equatable {
    var running: Bool = false
    var pid: Int? = nil
    var listenHost: String = "127.0.0.1"
    var listenPort: Int = 1080
    var service: String = "Wi-Fi"
    var relayPeerId: String = ""
    var startedAt: Date? = nil
    /// True iff `networksetup -getsocksfirewallproxy <service>` reports
    /// Enabled: Yes. Distinguishes "subprocess running" from "macOS
    /// system proxy actually applied" — the UI surfaces a warning when
    /// the two disagree.
    var systemProxyActive: Bool = false
    var systemProxyServer: String = ""
    var systemProxyPort: String = ""

    var endpoint: String { "socks5://\(listenHost):\(listenPort)" }
}

struct ProxySettingsView: Equatable {
    var listenPort: Int = 1080
    var service: String = "Wi-Fi"
    var relayPeerId: String = ""
    var availableServices: [String] = []
}

/// Single source of truth for what the UI should be saying right now.
/// Computed from (proxy.running, system_proxy_active, pendingAction)
/// so the connection dial, the action button, and the menu bar item
/// can never disagree (the previous "Disconnected … Cancel" mismatch
/// happened because they read independent flags).
enum ConnectionPhase: Equatable {
    case disconnected           // not running, no command in flight
    case connecting             // start command issued, not yet up
    case connected              // running + macOS proxy verified active
    case unhealthy              // running but networksetup says off
    case disconnecting          // stop command issued, still tearing down

    var isBusy: Bool { self == .connecting || self == .disconnecting }
    var allowsConnect: Bool { self == .disconnected }
    var allowsCancel: Bool { self == .connecting || self == .connected || self == .unhealthy }
}

@MainActor
final class ProxyController: ObservableObject {
    @Published var auth = AuthState()
    @Published var proxy = ProxyState()
    @Published var settings = ProxySettingsView()
    @Published var statusMessage: String = ""
    @Published var transactionHash: String = ""
    @Published private(set) var phase: ConnectionPhase = .disconnected
    /// True while *any* helper command is in flight. Used by SignInView
    /// to disable the OTP buttons during send/verify; proxy lifecycle
    /// uses `phase` instead so the dial / button can never disagree.
    @Published var busy: Bool = false

    /// Last user action whose helper response hasn't returned yet.
    /// Used to colour the phase as connecting vs disconnecting while
    /// the helper is mid-flight (where proxy.running alone is stale).
    private var pendingAction: PendingAction? = nil

    private enum PendingAction { case start, stop }

    private let helper = ProxyHelperClient()
    private var pollTask: Task<Void, Never>?

    deinit {
        pollTask?.cancel()
    }

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                await self?.refresh(silent: true)
            }
        }
    }

    func refresh(silent: Bool = false) {
        Task { await refresh(silent: silent) }
    }

    private func refresh(silent: Bool) async {
        do {
            let result = try await helper.send(command: "status")
            apply(result)
            if !silent { statusMessage = "" }
        } catch {
            if !silent { statusMessage = error.localizedDescription }
        }
        if settings.availableServices.isEmpty {
            await loadServices()
        }
    }

    func loadServices() async {
        if let result = try? await helper.send(command: "listServices") {
            settings.availableServices = (result.data["services"] as? [String]) ?? []
        }
    }

    func startAuth(phone: String) {
        guard !phone.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        runCommand("authStart", payload: ["phone": phone]) { [weak self] result in
            guard let self else { return }
            self.transactionHash = (result.data["transaction_hash"] as? String) ?? ""
        }
    }

    func verifyAuth(code: String) {
        guard !transactionHash.isEmpty else {
            statusMessage = "Request a code first."
            return
        }
        runCommand("authVerify", payload: ["transaction_hash": transactionHash, "code": code]) { [weak self] _ in
            self?.transactionHash = ""
        }
    }

    func logout() {
        runCommand("logout")
    }

    func startProxy() {
        pendingAction = .start
        recomputePhase()
        runCommand("proxyStart")
    }

    func stopProxy() {
        pendingAction = .stop
        recomputePhase()
        runCommand("proxyStop")
    }

    func saveSettings(port: Int, service: String, relayPeerId: String) {
        runCommand("saveSettings", payload: [
            "listen_port": port,
            "network_service": service,
            "relay_peer_id": relayPeerId,
        ])
    }

    func stopProxyAndWait() async {
        if let result = try? await helper.send(command: "proxyStop") {
            apply(result)
        }
    }

    private func runCommand(_ command: String, payload: [String: Any] = [:], onSuccess: ((HelperResult) -> Void)? = nil) {
        Task {
            busy = true
            defer {
                busy = false
                // Always clear pending intent when the helper returns,
                // even on error, so the UI doesn't get stuck in
                // "Connecting…" forever.
                pendingAction = nil
                recomputePhase()
            }
            do {
                let result = try await helper.send(command: command, payload: payload)
                apply(result)
                statusMessage = result.message
                if result.ok { onSuccess?(result) }
            } catch {
                statusMessage = error.localizedDescription
            }
        }
    }

    private func apply(_ result: HelperResult) {
        if let authDict = result.data["auth"] as? [String: Any] {
            auth.loggedIn = (authDict["logged_in"] as? Bool) ?? false
            auth.phone = (authDict["phone"] as? String) ?? ""
            auth.accountPath = (authDict["account_path"] as? String) ?? ""
        }
        if let proxyDict = result.data["proxy"] as? [String: Any] {
            proxy.running = (proxyDict["running"] as? Bool) ?? false
            proxy.pid = proxyDict["pid"] as? Int
            proxy.listenHost = (proxyDict["listen_host"] as? String) ?? proxy.listenHost
            proxy.listenPort = (proxyDict["listen_port"] as? Int) ?? proxy.listenPort
            proxy.service = (proxyDict["service"] as? String) ?? proxy.service
            proxy.relayPeerId = (proxyDict["relay_peer_id"] as? String) ?? proxy.relayPeerId
            proxy.systemProxyActive = (proxyDict["system_proxy_active"] as? Bool) ?? false
            proxy.systemProxyServer = (proxyDict["system_proxy_server"] as? String) ?? ""
            proxy.systemProxyPort = (proxyDict["system_proxy_port"] as? String) ?? ""
            if let ts = proxyDict["started_at"] as? Double {
                proxy.startedAt = Date(timeIntervalSince1970: ts)
            } else {
                proxy.startedAt = nil
            }
        }
        if let settingsDict = result.data["settings"] as? [String: Any] {
            settings.listenPort = (settingsDict["listen_port"] as? Int) ?? settings.listenPort
            settings.service = (settingsDict["network_service"] as? String) ?? settings.service
            settings.relayPeerId = (settingsDict["relay_peer_id"] as? String) ?? settings.relayPeerId
        }
        recomputePhase()
    }

    private func recomputePhase() {
        if let pending = pendingAction {
            // While a helper command is in flight, the user-issued
            // intent wins. proxy.running is the LAST polled snapshot
            // and lags by up to 3 s.
            phase = (pending == .start) ? .connecting : .disconnecting
            return
        }
        if !proxy.running {
            phase = .disconnected
            return
        }
        phase = proxy.systemProxyActive ? .connected : .unhealthy
    }
}
