import AppKit
import Combine
import Foundation
import NetworkExtension
import UniformTypeIdentifiers

final class BaleAppController: NSObject, ObservableObject {
    @Published private(set) var statusText = "Not installed"
    @Published private(set) var profileText = "No profile saved yet"
    @Published private(set) var connectionText = "Tunnel inactive"
    @Published private(set) var tunnelStatus: NEVPNStatus = .invalid
    @Published private(set) var isProfileReady = false
    @Published private(set) var appState = BaleAppState()
    @Published private(set) var helperText = "App-control helper not connected"
    @Published private(set) var actionMessage = ""
    @Published private(set) var authTransactionHash = ""
    @Published var draft = BaleTunnelConfiguration(displayName: "baleobala")

    private let manager = BaleTunnelManager()
    private let appControl = BaleAppControlClient()
    private var tunnelManager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?

    override init() {
        super.init()
        refreshDerivedAppState()
    }

    deinit {
        if let statusObserver = statusObserver {
            NotificationCenter.default.removeObserver(statusObserver)
        }
    }

    func loadState() {
        manager.loadInstalledManager { [weak self] tunnelManager in
            guard let self = self else { return }
            DispatchQueue.main.async {
                self.attach(manager: tunnelManager)
                self.refreshSummary()
                self.loadAppControlState()
            }
        }
    }

    func loadAppControlState() {
        appControl.status { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let state):
                self.helperText = "App-control helper connected"
                self.appState = state
                self.draft.networkPolicy = state.networkPolicy
                self.refreshDerivedAppState(keepingHelperState: true)
            case .failure(let error):
                self.helperText = error.localizedDescription
                self.refreshDerivedAppState()
            }
        }
    }

    func installCurrentProfile() {
        install(profile: draft)
    }

    func install(profile: BaleTunnelConfiguration) {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self, let tunnelManager = tunnelManager else { return }
            DispatchQueue.main.async {
                self.statusText = "Installing system profile"
                self.connectionText = "Saving settings"
                self.isProfileReady = false
                self.refreshDerivedAppState()
            }
            self.manager.configure(manager: tunnelManager, tunnel: profile)
            self.manager.save(manager: tunnelManager) { error in
                DispatchQueue.main.async {
                    if let error = error {
                        self.statusText = "System profile install failed"
                        self.connectionText = self.friendlyInstallFailureMessage(for: error)
                        self.isProfileReady = false
                        self.refreshDerivedAppState()
                    } else {
                        tunnelManager.loadFromPreferences { [weak self] loadError in
                            DispatchQueue.main.async {
                                guard let self = self else { return }
                                if let loadError = loadError {
                                    self.statusText = "System profile installed"
                                    self.connectionText = loadError.localizedDescription
                                    self.isProfileReady = false
                                    self.refreshDerivedAppState()
                                    return
                                }
                                self.attach(manager: tunnelManager)
                                self.draft = profile
                                self.statusText = "System profile installed"
                                self.profileText = self.profileSummary(for: profile)
                                self.isProfileReady = true
                                self.refreshConnectionText()
                            }
                        }
                    }
                }
            }
        }
    }

    func connect() {
        guard let tunnelManager = tunnelManager, isProfileReady else {
            statusText = "Install and approve the system profile first"
            connectionText = "No ready profile"
            refreshDerivedAppState()
            return
        }
        statusText = "Starting carrier runtime"
        connectionText = "Preparing Bale tunnel"
        refreshDerivedAppState()
        appControl.send(command: "connect", payload: [:]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.actionMessage = response.message
                self.manager.start(manager: tunnelManager) { [weak self] error in
                    DispatchQueue.main.async {
                        guard let self = self else { return }
                        if let error = error {
                            self.statusText = "Tunnel start failed"
                            self.connectionText = self.friendlyStartFailureMessage(for: error)
                            self.appControl.send(command: "disconnect", payload: [:]) { _ in }
                        } else {
                            self.statusText = "Tunnel start requested"
                            self.refreshConnectionText()
                        }
                        self.loadAppControlState()
                    }
                }
            case .failure(let error):
                self.statusText = "Carrier runtime failed"
                self.connectionText = error.localizedDescription
                self.helperText = error.localizedDescription
                self.refreshDerivedAppState()
            }
        }
    }

    func disconnect() {
        guard let tunnelManager = tunnelManager else {
            statusText = "No system tunnel profile is loaded"
            refreshDerivedAppState()
            return
        }
        manager.stop(manager: tunnelManager)
        statusText = "Tunnel stopped"
        refreshConnectionText()
        appControl.send(command: "disconnect", payload: [:]) { [weak self] _ in
            self?.loadAppControlState()
        }
    }

    func primaryAction() {
        if isTunnelRunning {
            disconnect()
        } else if !isProfileReady {
            installCurrentProfile()
        } else {
            connect()
        }
    }

    func pause(minutes: Int) {
        var policy = draft.networkPolicy
        policy.pauseUntil = Date().addingTimeInterval(TimeInterval(max(minutes, 1) * 60)).timeIntervalSince1970
        updateNetworkPolicy(policy)
        if isTunnelRunning {
            disconnect()
        }
    }

    func startAuth(phone: String) {
        appControl.send(command: "startAuth", payload: ["phone": phone]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.actionMessage = response.message
                if let tx = response.data["transactionHash"] as? String {
                    self.authTransactionHash = tx
                    self.helperText = "SMS transaction \(tx)"
                }
            case .failure(let error):
                self.actionMessage = error.localizedDescription
                self.helperText = error.localizedDescription
            }
        }
    }

    func verifyAuth(phone: String, code: String, transactionHash: String) {
        appControl.send(
            command: "verifyAuth",
            payload: ["phone": phone, "code": code, "transactionHash": transactionHash]
        ) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.actionMessage = response.message
                self.loadAppControlState()
            case .failure(let error):
                self.actionMessage = error.localizedDescription
                self.helperText = error.localizedDescription
            }
        }
    }

    func pairRelay(name: String, code: String, role: String) {
        appControl.send(
            command: "pair",
            payload: [
                "name": name,
                "pairCode": code,
                "role": role,
                "backend": "packet-tunnel",
                "transportPreference": draft.networkPolicy.transportPreference
            ]
        ) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.actionMessage = response.message
                self.loadAppControlState()
            case .failure(let error):
                self.actionMessage = error.localizedDescription
                self.helperText = error.localizedDescription
            }
        }
    }

    func syncPairing() {
        let profileID = appState.pairing["profile_id"] ?? ""
        appControl.send(command: "syncPairing", payload: ["profileID": profileID]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.actionMessage = response.message
                self.loadAppControlState()
            case .failure(let error):
                self.actionMessage = error.localizedDescription
                self.helperText = error.localizedDescription
            }
        }
    }

    func exportDiagnostics() {
        actionMessage = "Preparing diagnostics export"
        appControl.send(command: "diagnostics", payload: [:]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let response):
                self.presentDiagnosticsExport(response.data)
            case .failure(let error):
                self.actionMessage = error.localizedDescription
                self.helperText = error.localizedDescription
            }
        }
    }

    func updateDraft(_ mutate: (inout BaleTunnelConfiguration) -> Void) {
        var copy = draft
        mutate(&copy)
        draft = copy
        refreshDerivedAppState()
    }

    func updateNetworkPolicy(_ policy: BaleNetworkPolicy) {
        draft.networkPolicy = policy
        draft.dnsServers = policy.dnsMode == "system" ? [] : policy.customDNSServers
        refreshDerivedAppState()
        appControl.setNetworkPolicy(policy) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let state):
                self.helperText = "Network policy saved"
                self.appState = state
                self.refreshDerivedAppState(keepingHelperState: true)
            case .failure(let error):
                self.helperText = error.localizedDescription
            }
        }
    }

    var canConnect: Bool {
        isProfileReady
    }

    var isTunnelRunning: Bool {
        switch tunnelStatus {
        case .connected, .connecting, .disconnecting, .reasserting:
            return true
        case .invalid, .disconnected:
            return false
        @unknown default:
            return false
        }
    }

    var primaryActionTitle: String {
        if isTunnelRunning {
            return "Disconnect"
        }
        if !isProfileReady {
            return "Install VPN Profile"
        }
        return "Connect"
    }

    var primaryActionSystemImage: String {
        if isTunnelRunning {
            return "stop.fill"
        }
        if !isProfileReady {
            return "square.and.arrow.down"
        }
        return "power"
    }

    var selectedRelayText: String {
        if let relay = appState.relays.first {
            return "\(relay.name) · \(relay.status)"
        }
        if let name = appState.pairing["name"], !name.isEmpty {
            return name
        }
        return "No relay selected"
    }

    var accountText: String {
        let phone = appState.auth["phone"] ?? ""
        if appState.auth["state"] == "configured", !phone.isEmpty, phone != "unknown" {
            return phone.hasPrefix("+") ? phone : "+\(phone)"
        }
        return "Not signed in"
    }

    private func attach(manager tunnelManager: NETunnelProviderManager?) {
        if let statusObserver = statusObserver {
            NotificationCenter.default.removeObserver(statusObserver)
            self.statusObserver = nil
        }
        self.tunnelManager = tunnelManager
        isProfileReady = tunnelManager != nil
        guard let tunnelManager = tunnelManager else {
            refreshDerivedAppState()
            return
        }
        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange,
            object: tunnelManager.connection,
            queue: .main
        ) { [weak self] _ in
            self?.refreshConnectionText()
        }
    }

    private func refreshSummary() {
        guard let tunnelManager = tunnelManager else {
            statusText = "No system profile installed"
            profileText = "No profile saved yet"
            connectionText = "Tunnel inactive"
            tunnelStatus = .invalid
            isProfileReady = false
            refreshDerivedAppState()
            return
        }

        if let protocolConfiguration = tunnelManager.protocolConfiguration as? NETunnelProviderProtocol,
           let providerConfiguration = protocolConfiguration.providerConfiguration,
           let profile = BaleTunnelConfiguration(providerConfiguration: providerConfiguration) {
            draft = profile
            profileText = profileSummary(for: profile)
            isProfileReady = true
        } else {
            profileText = "A system profile exists, but it is missing configuration details."
            isProfileReady = false
        }
        refreshConnectionText()
    }

    private func refreshConnectionText() {
        guard let tunnelManager = tunnelManager else {
            connectionText = "Tunnel inactive"
            statusText = "No system profile installed"
            tunnelStatus = .invalid
            isProfileReady = false
            refreshDerivedAppState()
            return
        }
        let status = tunnelManager.connection.status
        tunnelStatus = status
        switch status {
        case .connected:
            connectionText = "Connected"
            statusText = "Tunnel active"
        case .connecting:
            connectionText = "Connecting"
            statusText = "Starting tunnel"
        case .disconnecting:
            connectionText = "Disconnecting"
            statusText = "Stopping tunnel"
        case .reasserting:
            connectionText = "Reasserting"
            statusText = "Tunnel is re-establishing"
        case .invalid:
            connectionText = "Invalid"
            statusText = "Installed profile is invalid"
        case .disconnected:
            connectionText = "Tunnel inactive"
            statusText = "System profile ready"
            isProfileReady = true
        @unknown default:
            connectionText = "Unknown"
            statusText = "Tunnel status changed"
        }
        refreshDerivedAppState()
    }

    private func refreshDerivedAppState(keepingHelperState: Bool = false) {
        var state = keepingHelperState ? appState : fallbackAppState()
        state.networkPolicy = draft.networkPolicy
        state.connectionState = derivedConnectionState(from: state.connectionState)
        state.headline = derivedHeadline(for: state.connectionState)
        state.detail = derivedDetail(for: state.connectionState)
        state.canDisconnect = isTunnelRunning
        state.canConnect = isProfileReady && !isTunnelRunning
        state.backend["state"] = connectionText
        state.backend["backend"] = state.backend["backend"] ?? "packet-tunnel"
        state.backend["route_ready"] = isTunnelRunning ? "yes" : state.backend["route_ready"]
        state.backend["dns_ready"] = isTunnelRunning ? "yes" : state.backend["dns_ready"]
        state.readiness = localReadiness(merging: state.readiness)
        appState = state
    }

    private func fallbackAppState() -> BaleAppState {
        var state = BaleAppState()
        state.connectionState = derivedConnectionState(from: .firstRun)
        state.headline = derivedHeadline(for: state.connectionState)
        state.detail = derivedDetail(for: state.connectionState)
        state.canConnect = isProfileReady && !isTunnelRunning
        state.canDisconnect = isTunnelRunning
        state.backend = [
            "backend": "packet-tunnel",
            "state": connectionText,
            "transport_selected": draft.networkPolicy.transportPreference,
            "route_ready": isTunnelRunning ? "yes" : "no",
            "dns_ready": isTunnelRunning ? "yes" : "no",
        ]
        state.vpn = [
            "state": isProfileReady ? "configured" : "empty",
            "name": draft.displayName,
            "backend": "packet-tunnel",
        ]
        state.readiness = localReadiness(merging: [])
        state.networkPolicy = draft.networkPolicy
        return state
    }

    private func derivedConnectionState(from helperState: ConnectionState) -> ConnectionState {
        switch tunnelStatus {
        case .connected:
            return .connected
        case .connecting:
            return .connecting
        case .disconnecting:
            return .disconnected
        case .reasserting:
            return .reconnecting
        case .invalid:
            return isProfileReady ? .degraded : .needsProfile
        case .disconnected:
            return isProfileReady ? (helperState == .blocked ? .blocked : .disconnected) : .needsProfile
        @unknown default:
            return .unknown
        }
    }

    private func derivedHeadline(for state: ConnectionState) -> String {
        switch state {
        case .connected:
            return "Secure connection active"
        case .connecting:
            return "Starting secure connection"
        case .reconnecting:
            return "Reconnecting"
        case .degraded:
            return "Connection needs attention"
        case .blocked:
            return appState.connection["title"] ?? "Connection needs attention"
        case .signedOut:
            return "Sign in to start"
        case .expiredSession:
            return "Session expired"
        case .paused:
            return "VPN paused"
        case .needsProfile, .firstRun:
            return "Install the VPN profile"
        case .disconnected:
            return "Ready to connect"
        case .installingProfile:
            return "Installing VPN profile"
        case .unknown:
            return "Connection status unknown"
        }
    }

    private func derivedDetail(for state: ConnectionState) -> String {
        switch state {
        case .connected:
            return "\(selectedRelayText) is protecting traffic through the packet tunnel."
        case .connecting:
            return "macOS is bringing up the packet tunnel."
        case .reconnecting:
            return "The tunnel is re-establishing after a network change."
        case .degraded:
            return connectionText
        case .blocked:
            return appState.connection["message"] ?? "Review readiness before connecting."
        case .signedOut:
            return "Use Bale sign-in and pair a relay before connecting."
        case .expiredSession:
            return "Sign in again to refresh relay provisioning."
        case .paused:
            return "The app will stay disconnected until the pause expires or you reconnect."
        case .needsProfile, .firstRun:
            return "Install and approve the macOS VPN profile once."
        case .disconnected:
            return "The system profile is ready."
        case .installingProfile:
            return "Saving the route, DNS, and privacy policy."
        case .unknown:
            return connectionText
        }
    }

    private func localReadiness(merging helperItems: [ReadinessGate]) -> [ReadinessGate] {
        var items = helperItems
        upsert(
            ReadinessGate(
                key: "system-profile",
                label: "System profile",
                ready: isProfileReady,
                detail: isProfileReady ? "Ready" : "Install and approve the VPN profile."
            ),
            into: &items
        )
        upsert(
            ReadinessGate(
                key: "packet-tunnel",
                label: "Packet tunnel",
                ready: isTunnelRunning,
                detail: connectionText
            ),
            into: &items
        )
        return items
    }

    private func upsert(_ item: ReadinessGate, into items: inout [ReadinessGate]) {
        if let index = items.firstIndex(where: { $0.key == item.key }) {
            items[index] = item
        } else {
            items.append(item)
        }
    }

    private func profileSummary(for profile: BaleTunnelConfiguration) -> String {
        let routeCount = profile.includedIPv4Routes.count + profile.includedIPv6Routes.count
        let dns = profile.dnsServers.isEmpty ? "system" : profile.dnsServers.joined(separator: ", ")
        return [
            profile.displayName,
            "\(routeCount) route rule(s)",
            "DNS: \(dns)",
            "Policy: \(profile.networkPolicy.killSwitchMode)"
        ].joined(separator: " · ")
    }

    private func friendlyInstallFailureMessage(for error: Error) -> String {
        let nsError = error as NSError
        let lowercased = nsError.localizedDescription.lowercased()
        if lowercased.contains("permission denied") || nsError.domain == NSOSStatusErrorDomain {
            return "Approve the VPN profile in System Settings, then try Install again."
        }
        return nsError.localizedDescription
    }

    private func friendlyStartFailureMessage(for error: Error) -> String {
        let nsError = error as NSError
        if nsError.domain == NEVPNErrorDomain, nsError.code == 1 {
            return "Approve the VPN profile in System Settings, then press Start again."
        }
        return nsError.localizedDescription
    }

    private func presentDiagnosticsExport(_ payload: [String: Any]) {
        var export = payload
        export["exportedAt"] = ISO8601DateFormatter().string(from: Date())
        export["format"] = "baleobala-diagnostics-v1"

        let panel = NSSavePanel()
        panel.allowedContentTypes = [.json]
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.nameFieldStringValue = "baleobala-diagnostics-\(Self.exportTimestamp()).json"
        panel.begin { [weak self] response in
            guard let self = self else { return }
            guard response == .OK, let url = panel.url else {
                self.actionMessage = "Diagnostics export cancelled."
                return
            }
            do {
                let data = try JSONSerialization.data(withJSONObject: export, options: [.prettyPrinted, .sortedKeys])
                try data.write(to: url, options: .atomic)
                self.actionMessage = "Diagnostics exported to \(url.lastPathComponent)."
            } catch {
                self.actionMessage = "Diagnostics export failed: \(error.localizedDescription)"
            }
        }
    }

    private static func exportTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: Date())
    }
}
