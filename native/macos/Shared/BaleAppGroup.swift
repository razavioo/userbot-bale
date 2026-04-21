import Foundation

enum BaleAppGroup {
    static let identifier = "group.com.baleobala.vpn"
    static let keychainService = "com.baleobala.vpn"
    static let keychainAccount = "baleobala"
    static let carrierSocketName = "carrier_tunnel.sock"
    static let providerBundleIdentifier = "com.baleobala.app.packet-tunnel"
    static let tunnelDescription = "baleobala packet tunnel"

    static func sharedContainerURL() -> URL? {
        if let containerURL = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier) {
            return containerURL
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Group Containers", isDirectory: true)
            .appendingPathComponent(identifier, isDirectory: true)
    }

    static func carrierSocketURL() -> URL? {
        sharedContainerURL()?.appendingPathComponent(carrierSocketName, isDirectory: false)
    }
}
