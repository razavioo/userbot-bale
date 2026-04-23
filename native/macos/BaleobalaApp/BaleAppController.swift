import Combine
import Foundation
import NetworkExtension

final class BaleAppController: NSObject, ObservableObject {
    @Published private(set) var statusText = "Not installed"
    @Published private(set) var profileText = "No system tunnel profile installed yet"
    @Published private(set) var connectionText = "Tunnel inactive"
    @Published var draft = BaleTunnelConfiguration(displayName: "baleobala")

    private let manager = BaleTunnelManager()
    private var tunnelManager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?

    override init() {
        super.init()
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
            }
        }
    }

    func installCurrentProfile() {
        install(profile: draft)
    }

    func install(profile: BaleTunnelConfiguration) {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self, let tunnelManager = tunnelManager else { return }
            self.attach(manager: tunnelManager)
            self.manager.configure(manager: tunnelManager, tunnel: profile)
            self.manager.save(manager: tunnelManager) { error in
                DispatchQueue.main.async {
                    if let error = error {
                        self.statusText = "System profile install failed"
                        self.connectionText = error.localizedDescription
                    } else {
                        self.draft = profile
                        self.statusText = "System profile installed"
                        self.profileText = self.profileSummary(for: profile)
                        self.refreshConnectionText()
                    }
                }
            }
        }
    }

    func connect() {
        guard let tunnelManager = tunnelManager else {
            statusText = "Install the system profile first"
            return
        }
        manager.start(manager: tunnelManager) { [weak self] error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let error = error {
                    self.statusText = "Tunnel start failed"
                    self.connectionText = error.localizedDescription
                } else {
                    self.statusText = "Tunnel start requested"
                    self.refreshConnectionText()
                }
            }
        }
    }

    func disconnect() {
        guard let tunnelManager = tunnelManager else {
            statusText = "No system tunnel profile is loaded"
            return
        }
        manager.stop(manager: tunnelManager)
        statusText = "Tunnel stopped"
        refreshConnectionText()
    }

    func updateDraft(_ mutate: (inout BaleTunnelConfiguration) -> Void) {
        var copy = draft
        mutate(&copy)
        draft = copy
    }

    var canConnect: Bool {
        tunnelManager != nil
    }

    private func attach(manager tunnelManager: NETunnelProviderManager?) {
        if let statusObserver = statusObserver {
            NotificationCenter.default.removeObserver(statusObserver)
            self.statusObserver = nil
        }
        self.tunnelManager = tunnelManager
        guard let tunnelManager = tunnelManager else {
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
            profileText = "Create a profile here, install it, then start the tunnel."
            connectionText = "Tunnel inactive"
            return
        }

        if let protocolConfiguration = tunnelManager.protocolConfiguration as? NETunnelProviderProtocol,
           let providerConfiguration = protocolConfiguration.providerConfiguration,
           let profile = BaleTunnelConfiguration(providerConfiguration: providerConfiguration) {
            draft = profile
            profileText = profileSummary(for: profile)
        } else {
            profileText = "A system profile exists, but it is missing configuration details."
        }
        refreshConnectionText()
    }

    private func refreshConnectionText() {
        guard let tunnelManager = tunnelManager else {
            connectionText = "Tunnel inactive"
            statusText = "No system profile installed"
            return
        }
        let status = tunnelManager.connection.status
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
        @unknown default:
            connectionText = "Unknown"
            statusText = "Tunnel status changed"
        }
    }

    private func profileSummary(for profile: BaleTunnelConfiguration) -> String {
        let routeCount = profile.includedIPv4Routes.count + profile.includedIPv6Routes.count
        let dns = profile.dnsServers.joined(separator: ", ")
        return [
            profile.displayName,
            "\(routeCount) route rule(s)",
            "DNS: \(dns)",
            "Group: \(profile.appGroupIdentifier)"
        ].joined(separator: " · ")
    }
}
