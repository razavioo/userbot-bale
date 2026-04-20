import Foundation

struct PacketTunnelPrincipal {
    static func providerConfiguration() -> [String: Any] {
        [
            "appGroupIdentifier": BaleAppGroup.identifier,
            "carrierSocketPath": BaleAppGroup.carrierSocketName,
        ]
    }
}
