import Foundation

struct BaleTunnelConfiguration: Codable {
    var displayName: String
    var appGroupIdentifier: String = BaleAppGroup.identifier
    var providerBundleIdentifier: String = BaleAppGroup.providerBundleIdentifier
    var serverAddress: String = "127.0.0.1"
    var includedIPv4Routes: [String] = ["0.0.0.0/0"]
    var includedIPv6Routes: [String] = ["::/0"]
    var excludedRoutes: [String] = ["127.0.0.0/8", "::1/128"]
    var dnsServers: [String] = ["1.1.1.1", "9.9.9.9"]
    var carrierSocketPath: String = BaleAppGroup.carrierSocketName
    var keychainTokenKey: String = "auth.jwt"
    var relayIdentifier: String?
    var packetTunnelHost: String = "127.0.0.1"
    var packetTunnelPort: Int = 1080
}

extension BaleTunnelConfiguration {
    func providerConfiguration() -> [String: Any] {
        [
            "appGroupIdentifier": appGroupIdentifier,
            "carrierSocketPath": carrierSocketPath,
            "keychainTokenKey": keychainTokenKey,
            "relayIdentifier": relayIdentifier ?? "",
            "packetTunnelHost": packetTunnelHost,
            "packetTunnelPort": packetTunnelPort,
        ]
    }
}
