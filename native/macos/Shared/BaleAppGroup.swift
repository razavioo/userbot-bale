import Foundation

enum BaleAppGroup {
    static let identifier = "group.com.baleobala.vpn"
    static let keychainService = "com.baleobala.vpn"
    static let keychainAccount = "baleobala"
    static let carrierSocketName = "carrier_tunnel.sock"
    static let providerBundleIdentifier = "com.baleobala.packet-tunnel"
    static let tunnelDescription = "baleobala packet tunnel"

    static func sharedContainerURL() -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier)
    }

    static func carrierSocketURL() -> URL? {
        sharedContainerURL()?.appendingPathComponent(carrierSocketName, isDirectory: false)
    }
}
