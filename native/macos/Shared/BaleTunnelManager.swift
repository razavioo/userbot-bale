import Foundation
import NetworkExtension

final class BaleTunnelManager {
    private let store = BaleKeychainStore()

    func loadOrCreateManager(completion: @escaping (NETunnelProviderManager?) -> Void) {
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
            if let error = error {
                print("Failed to load tunnel managers: \(error)")
                completion(nil)
                return
            }
            if let manager = managers?.first {
                completion(manager)
                return
            }
            let manager = NETunnelProviderManager()
            completion(manager)
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
