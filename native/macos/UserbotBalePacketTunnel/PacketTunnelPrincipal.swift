import Foundation

struct PacketTunnelPrincipal {
    static func providerConfiguration() -> [String: Any] {
        BaleTunnelConfiguration(displayName: "userbot-bale").providerConfiguration()
    }
}
