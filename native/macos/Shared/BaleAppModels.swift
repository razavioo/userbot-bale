import Foundation

enum ConnectionState: String, Codable, CaseIterable {
    case firstRun
    case signedOut
    case expiredSession
    case blocked
    case paused
    case connecting
    case connected
    case reconnecting
    case degraded
    case disconnected
    case installingProfile
    case needsProfile
    case unknown

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: value) ?? .unknown
    }
}

struct BaleNetworkPolicy: Codable, Equatable {
    var policyVersion: Int = 1
    var killSwitchMode: String = "off"
    var autoConnect: Bool = false
    var launchAtLogin: Bool = false
    var allowLAN: Bool = true
    var dnsMode: String = "custom"
    var customDNSServers: [String] = ["1.1.1.1", "9.9.9.9"]
    var trustedWiFiAction: String = "ask"
    var untrustedWiFiAction: String = "connect"
    var trustedWiFiNetworks: [String] = []
    var splitTunnelMode: String = "off"
    var splitTunnelExclusions: [String] = []
    var transportPreference: String = "auto"
    var fallbackProxyEnabled: Bool = false
    var pauseUntil: Double?

    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case killSwitchMode = "kill_switch_mode"
        case autoConnect = "auto_connect"
        case launchAtLogin = "launch_at_login"
        case allowLAN = "allow_lan"
        case dnsMode = "dns_mode"
        case customDNSServers = "custom_dns_servers"
        case trustedWiFiAction = "trusted_wifi_action"
        case untrustedWiFiAction = "untrusted_wifi_action"
        case trustedWiFiNetworks = "trusted_wifi_networks"
        case splitTunnelMode = "split_tunnel_mode"
        case splitTunnelExclusions = "split_tunnel_exclusions"
        case transportPreference = "transport_preference"
        case fallbackProxyEnabled = "fallback_proxy_enabled"
        case pauseUntil = "pause_until"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        policyVersion = try container.decodeIfPresent(Int.self, forKey: .policyVersion) ?? 1
        killSwitchMode = try container.decodeIfPresent(String.self, forKey: .killSwitchMode) ?? "off"
        autoConnect = try container.decodeIfPresent(Bool.self, forKey: .autoConnect) ?? false
        launchAtLogin = try container.decodeIfPresent(Bool.self, forKey: .launchAtLogin) ?? false
        allowLAN = try container.decodeIfPresent(Bool.self, forKey: .allowLAN) ?? true
        dnsMode = try container.decodeIfPresent(String.self, forKey: .dnsMode) ?? "custom"
        customDNSServers = try container.decodeIfPresent([String].self, forKey: .customDNSServers) ?? ["1.1.1.1", "9.9.9.9"]
        trustedWiFiAction = try container.decodeIfPresent(String.self, forKey: .trustedWiFiAction) ?? "ask"
        untrustedWiFiAction = try container.decodeIfPresent(String.self, forKey: .untrustedWiFiAction) ?? "connect"
        trustedWiFiNetworks = try container.decodeIfPresent([String].self, forKey: .trustedWiFiNetworks) ?? []
        splitTunnelMode = try container.decodeIfPresent(String.self, forKey: .splitTunnelMode) ?? "off"
        splitTunnelExclusions = try container.decodeIfPresent([String].self, forKey: .splitTunnelExclusions) ?? []
        transportPreference = try container.decodeIfPresent(String.self, forKey: .transportPreference) ?? "auto"
        fallbackProxyEnabled = try container.decodeIfPresent(Bool.self, forKey: .fallbackProxyEnabled) ?? false
        pauseUntil = try container.decodeIfPresent(Double.self, forKey: .pauseUntil)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(policyVersion, forKey: .policyVersion)
        try container.encode(killSwitchMode, forKey: .killSwitchMode)
        try container.encode(autoConnect, forKey: .autoConnect)
        try container.encode(launchAtLogin, forKey: .launchAtLogin)
        try container.encode(allowLAN, forKey: .allowLAN)
        try container.encode(dnsMode, forKey: .dnsMode)
        try container.encode(customDNSServers, forKey: .customDNSServers)
        try container.encode(trustedWiFiAction, forKey: .trustedWiFiAction)
        try container.encode(untrustedWiFiAction, forKey: .untrustedWiFiAction)
        try container.encode(trustedWiFiNetworks, forKey: .trustedWiFiNetworks)
        try container.encode(splitTunnelMode, forKey: .splitTunnelMode)
        try container.encode(splitTunnelExclusions, forKey: .splitTunnelExclusions)
        try container.encode(transportPreference, forKey: .transportPreference)
        try container.encode(fallbackProxyEnabled, forKey: .fallbackProxyEnabled)
        try container.encodeIfPresent(pauseUntil, forKey: .pauseUntil)
    }
}

struct ReadinessGate: Codable, Equatable, Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var ready: Bool
    var detail: String
}

struct RelaySummary: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var role: String
    var status: String
    var transportPreference: String
    var backendPreference: String
}

struct BaleAppState: Codable, Equatable {
    var schemaVersion: Int = 1
    var generatedAt: Double = 0
    var connectionState: ConnectionState = .firstRun
    var headline: String = "Ready for setup"
    var detail: String = "Create or approve a system VPN profile, then connect."
    var canConnect: Bool = false
    var canDisconnect: Bool = false
    var auth: [String: String] = [:]
    var vpn: [String: String] = [:]
    var pairing: [String: String] = [:]
    var mesh: [String: String] = [:]
    var backend: [String: String] = [:]
    var connection: [String: String] = [:]
    var readiness: [ReadinessGate] = []
    var relays: [RelaySummary] = []
    var networkPolicy: BaleNetworkPolicy = BaleNetworkPolicy()
    var codeSigning: [String: String] = [:]
}

struct AppControlResponse: Codable {
    var ok: Bool
    var message: String
    var data: BaleAppState?
}

struct DiagnosticSnapshot: Codable, Equatable {
    var schemaVersion: Int = 1
    var generatedAt: Double = 0
    var lastError: String = ""
    var tunnelProfilePath: String = ""
    var carrierSocketPath: String = ""
    var carrierSocketExists: Bool = false
    var carrierSocketReachable: Bool = false
    var carrierSocketState: String = ""
    var codeSigning: [String: String] = [:]
    var status: BaleAppState = BaleAppState()
}
