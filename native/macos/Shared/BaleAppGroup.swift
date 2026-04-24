import Foundation

enum BaleAppGroup {
    static var identifier: String {
        infoValue("BaleAppGroupIdentifier", fallback: "group.com.baleobala.vpn")
    }

    static var keychainService: String {
        infoValue("BaleKeychainService", fallback: "com.baleobala.vpn")
    }

    static let keychainAccount = "baleobala"
    static let carrierSocketName = "carrier_tunnel.sock"
    static var providerBundleIdentifier: String {
        infoValue("BaleProviderBundleIdentifier", fallback: "com.baleobala.app.packet-tunnel")
    }
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

    private static func infoValue(_ key: String, fallback: String) -> String {
        if let value = Bundle.main.object(forInfoDictionaryKey: key) as? String, !value.isEmpty {
            return value
        }
        return fallback
    }
}
