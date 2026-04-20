import Combine
import Foundation
import NetworkExtension

final class BaleAppController: ObservableObject {
    @Published private(set) var statusText = "Disconnected"
    @Published private(set) var profileText = "No profile loaded"

    private let manager = BaleTunnelManager()

    func loadState() {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self else { return }
            DispatchQueue.main.async {
                self.statusText = tunnelManager == nil ? "No tunnel manager" : "Tunnel manager ready"
            }
        }
    }

    func install(profile: BaleTunnelConfiguration) {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self, let tunnelManager = tunnelManager else { return }
            self.manager.configure(manager: tunnelManager, tunnel: profile)
            self.manager.save(manager: tunnelManager) { error in
                DispatchQueue.main.async {
                    if let error = error {
                        self.statusText = "Install failed: \(error.localizedDescription)"
                    } else {
                        self.statusText = "Profile installed"
                        self.profileText = profile.displayName
                    }
                }
            }
        }
    }

    func connect() {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self, let tunnelManager = tunnelManager else { return }
            self.manager.start(manager: tunnelManager) { error in
                DispatchQueue.main.async {
                    if let error = error {
                        self.statusText = "Connect failed: \(error.localizedDescription)"
                    } else {
                        self.statusText = "Connected"
                    }
                }
            }
        }
    }

    func disconnect() {
        manager.loadOrCreateManager { [weak self] tunnelManager in
            guard let self = self, let tunnelManager = tunnelManager else { return }
            self.manager.stop(manager: tunnelManager)
            DispatchQueue.main.async {
                self.statusText = "Disconnected"
            }
        }
    }
}
