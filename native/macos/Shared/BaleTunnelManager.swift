import Foundation
import NetworkExtension

final class BaleTunnelManager {
    private let store = BaleKeychainStore()

    func loadInstalledManager(completion: @escaping (NETunnelProviderManager?) -> Void) {
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
            if let error = error {
                print("Failed to load tunnel managers: \(error)")
                completion(nil)
                return
            }
            completion(
                managers?.first(where: { manager in
                    guard let protocolConfiguration = manager.protocolConfiguration as? NETunnelProviderProtocol else {
                        return false
                    }
                    return protocolConfiguration.providerBundleIdentifier == BaleAppGroup.providerBundleIdentifier
                })
            )
        }
    }

    func loadOrCreateManager(completion: @escaping (NETunnelProviderManager?) -> Void) {
        loadInstalledManager { manager in
            if let manager = manager {
                completion(manager)
                return
            }
            completion(NETunnelProviderManager())
        }
    }

    func configure(manager: NETunnelProviderManager, tunnel: BaleTunnelConfiguration) {
        let protocolConfiguration = NETunnelProviderProtocol()
        protocolConfiguration.providerBundleIdentifier = tunnel.providerBundleIdentifier
        protocolConfiguration.serverAddress = tunnel.serverAddress
        protocolConfiguration.providerConfiguration = tunnel.providerConfiguration()
        manager.protocolConfiguration = protocolConfiguration
        manager.localizedDescription = tunnel.displayName
        manager.isEnabled = true
        manager.isOnDemandEnabled = false
    }

    func save(manager: NETunnelProviderManager, completion: @escaping (Error?) -> Void) {
        manager.saveToPreferences { error in
            completion(error)
        }
    }

    func start(manager: NETunnelProviderManager, completion: @escaping (Error?) -> Void) {
        do {
            try manager.connection.startVPNTunnel()
            completion(nil)
        } catch {
            completion(error)
        }
    }

    func stop(manager: NETunnelProviderManager) {
        manager.connection.stopVPNTunnel()
    }

    func saveJWT(_ jwt: String) {
        try? store.save(jwt, key: "auth.jwt")
    }

    func loadJWT() -> String? {
        store.load(key: "auth.jwt")
    }
}
