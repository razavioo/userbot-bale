import Foundation

struct BaleTunnelConfiguration: Codable {
    var configurationVersion: Int = 1
    var displayName: String
    var appGroupIdentifier: String = BaleAppGroup.identifier
    var providerBundleIdentifier: String = BaleAppGroup.providerBundleIdentifier
    var serverAddress: String = "127.0.0.1"
    var includedIPv4Routes: [String] = ["0.0.0.0/0"]
    var includedIPv6Routes: [String] = ["::/0"]
    var excludedRoutes: [String] = ["127.0.0.0/8", "::1/128"]
    var dnsServers: [String] = ["1.1.1.1", "9.9.9.9"]
    var searchDomains: [String] = []
    var mtu: Int = 1400
    var overheadBytes: Int = 80
    var carrierSocketPath: String = BaleAppGroup.carrierSocketName
    var keychainTokenKey: String = "auth.jwt"
    var relayIdentifier: String?
    var packetTunnelHost: String = "127.0.0.1"
    var packetTunnelPort: Int = 1080
    var networkPolicy: BaleNetworkPolicy = BaleNetworkPolicy()

    init(
        displayName: String,
        configurationVersion: Int = 1,
        appGroupIdentifier: String = BaleAppGroup.identifier,
        providerBundleIdentifier: String = BaleAppGroup.providerBundleIdentifier,
        serverAddress: String = "127.0.0.1",
        includedIPv4Routes: [String] = ["0.0.0.0/0"],
        includedIPv6Routes: [String] = ["::/0"],
        excludedRoutes: [String] = ["127.0.0.0/8", "::1/128"],
        dnsServers: [String] = ["1.1.1.1", "9.9.9.9"],
        searchDomains: [String] = [],
        mtu: Int = 1400,
        overheadBytes: Int = 80,
        carrierSocketPath: String = BaleAppGroup.carrierSocketName,
        keychainTokenKey: String = "auth.jwt",
        relayIdentifier: String? = nil,
        packetTunnelHost: String = "127.0.0.1",
        packetTunnelPort: Int = 1080,
        networkPolicy: BaleNetworkPolicy = BaleNetworkPolicy()
    ) {
        self.configurationVersion = configurationVersion
        self.displayName = displayName
        self.appGroupIdentifier = appGroupIdentifier
        self.providerBundleIdentifier = providerBundleIdentifier
        self.serverAddress = serverAddress
        self.includedIPv4Routes = includedIPv4Routes
        self.includedIPv6Routes = includedIPv6Routes
        self.excludedRoutes = excludedRoutes
        self.dnsServers = dnsServers
        self.searchDomains = searchDomains
        self.mtu = mtu
        self.overheadBytes = overheadBytes
        self.carrierSocketPath = carrierSocketPath
        self.keychainTokenKey = keychainTokenKey
        self.relayIdentifier = relayIdentifier
        self.packetTunnelHost = packetTunnelHost
        self.packetTunnelPort = packetTunnelPort
        self.networkPolicy = networkPolicy
    }

    init?(providerConfiguration: [String: Any]) {
        guard let displayName = providerConfiguration["displayName"] as? String, !displayName.isEmpty else {
            return nil
        }
        self.init(
            displayName: displayName,
            configurationVersion: providerConfiguration["configurationVersion"] as? Int ?? 1,
            appGroupIdentifier: providerConfiguration["appGroupIdentifier"] as? String ?? BaleAppGroup.identifier,
            providerBundleIdentifier: providerConfiguration["providerBundleIdentifier"] as? String ?? BaleAppGroup.providerBundleIdentifier,
            serverAddress: providerConfiguration["serverAddress"] as? String ?? "127.0.0.1",
            includedIPv4Routes: providerConfiguration["includedIPv4Routes"] as? [String] ?? ["0.0.0.0/0"],
            includedIPv6Routes: providerConfiguration["includedIPv6Routes"] as? [String] ?? ["::/0"],
            excludedRoutes: providerConfiguration["excludedRoutes"] as? [String] ?? ["127.0.0.0/8", "::1/128"],
            dnsServers: providerConfiguration["dnsServers"] as? [String] ?? ["1.1.1.1", "9.9.9.9"],
            searchDomains: providerConfiguration["searchDomains"] as? [String] ?? [],
            mtu: providerConfiguration["mtu"] as? Int ?? (providerConfiguration["mtu"] as? NSNumber)?.intValue ?? 1400,
            overheadBytes: providerConfiguration["overheadBytes"] as? Int ?? (providerConfiguration["overheadBytes"] as? NSNumber)?.intValue ?? 80,
            carrierSocketPath: providerConfiguration["carrierSocketPath"] as? String ?? BaleAppGroup.carrierSocketName,
            keychainTokenKey: providerConfiguration["keychainTokenKey"] as? String ?? "auth.jwt",
            relayIdentifier: providerConfiguration["relayIdentifier"] as? String,
            packetTunnelHost: providerConfiguration["packetTunnelHost"] as? String ?? "127.0.0.1",
            packetTunnelPort: providerConfiguration["packetTunnelPort"] as? Int ?? (providerConfiguration["packetTunnelPort"] as? NSNumber)?.intValue ?? 1080,
            networkPolicy: BaleNetworkPolicy(providerConfiguration: providerConfiguration)
        )
    }
}

extension BaleTunnelConfiguration {
    func providerConfiguration() -> [String: Any] {
        [
            "appGroupIdentifier": appGroupIdentifier,
            "configurationVersion": configurationVersion,
            "displayName": displayName,
            "carrierSocketPath": carrierSocketPath,
            "keychainTokenKey": keychainTokenKey,
            "dnsServers": dnsServers,
            "excludedRoutes": excludedRoutes,
            "includedIPv4Routes": includedIPv4Routes,
            "includedIPv6Routes": includedIPv6Routes,
            "mtu": mtu,
            "relayIdentifier": relayIdentifier ?? "",
            "providerBundleIdentifier": providerBundleIdentifier,
            "searchDomains": searchDomains,
            "serverAddress": serverAddress,
            "packetTunnelHost": packetTunnelHost,
            "packetTunnelPort": packetTunnelPort,
            "overheadBytes": overheadBytes,
            "networkPolicyVersion": networkPolicy.policyVersion,
            "killSwitchMode": networkPolicy.killSwitchMode,
            "autoConnect": networkPolicy.autoConnect,
            "launchAtLogin": networkPolicy.launchAtLogin,
            "allowLAN": networkPolicy.allowLAN,
            "dnsMode": networkPolicy.dnsMode,
            "customDNSServers": networkPolicy.customDNSServers,
            "trustedWiFiAction": networkPolicy.trustedWiFiAction,
            "untrustedWiFiAction": networkPolicy.untrustedWiFiAction,
            "trustedWiFiNetworks": networkPolicy.trustedWiFiNetworks,
            "splitTunnelMode": networkPolicy.splitTunnelMode,
            "splitTunnelExclusions": networkPolicy.splitTunnelExclusions,
            "transportPreference": networkPolicy.transportPreference,
            "fallbackProxyEnabled": networkPolicy.fallbackProxyEnabled,
            "pauseUntil": networkPolicy.pauseUntil ?? 0,
        ]
    }
}

extension BaleNetworkPolicy {
    init(providerConfiguration: [String: Any]) {
        self.init()
        policyVersion = providerConfiguration["networkPolicyVersion"] as? Int ?? (providerConfiguration["networkPolicyVersion"] as? NSNumber)?.intValue ?? 1
        killSwitchMode = providerConfiguration["killSwitchMode"] as? String ?? "off"
        autoConnect = providerConfiguration["autoConnect"] as? Bool ?? (providerConfiguration["autoConnect"] as? NSNumber)?.boolValue ?? false
        launchAtLogin = providerConfiguration["launchAtLogin"] as? Bool ?? (providerConfiguration["launchAtLogin"] as? NSNumber)?.boolValue ?? false
        allowLAN = providerConfiguration["allowLAN"] as? Bool ?? (providerConfiguration["allowLAN"] as? NSNumber)?.boolValue ?? true
        dnsMode = providerConfiguration["dnsMode"] as? String ?? "custom"
        customDNSServers = providerConfiguration["customDNSServers"] as? [String] ?? providerConfiguration["dnsServers"] as? [String] ?? ["1.1.1.1", "9.9.9.9"]
        trustedWiFiAction = providerConfiguration["trustedWiFiAction"] as? String ?? "ask"
        untrustedWiFiAction = providerConfiguration["untrustedWiFiAction"] as? String ?? "connect"
        trustedWiFiNetworks = providerConfiguration["trustedWiFiNetworks"] as? [String] ?? []
        splitTunnelMode = providerConfiguration["splitTunnelMode"] as? String ?? "off"
        splitTunnelExclusions = providerConfiguration["splitTunnelExclusions"] as? [String] ?? []
        transportPreference = providerConfiguration["transportPreference"] as? String ?? "auto"
        fallbackProxyEnabled = providerConfiguration["fallbackProxyEnabled"] as? Bool ?? (providerConfiguration["fallbackProxyEnabled"] as? NSNumber)?.boolValue ?? false
        let pause = providerConfiguration["pauseUntil"] as? Double ?? (providerConfiguration["pauseUntil"] as? NSNumber)?.doubleValue ?? 0
        pauseUntil = pause > 0 ? pause : nil
    }
}
