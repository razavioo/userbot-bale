import NetworkExtension
import Foundation

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let queue = DispatchQueue(label: "com.userbot_bale.packet-tunnel")
    private var socketClient: BaleCarrierSocketClient?
    private var readLoopRunning = false
    private var lastErrorMessage = ""
    private let statusVersion = "1"

    override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        do {
            try connectCarrierSocket()
            configureNetwork { [weak self] error in
                guard let self = self else { return }
                if let error = error {
                    self.socketClient = nil
                    completionHandler(error)
                    return
                }
                self.startPacketPump()
                completionHandler(nil)
            }
        } catch {
            completionHandler(error)
        }
    }

    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)? = nil) {
        completionHandler?(handleControlMessage(messageData))
    }

    private func configureNetwork(completionHandler: @escaping (Error?) -> Void) {
        let configuration = tunnelProviderConfiguration()
        let settings = NEPacketTunnelNetworkSettings(
            tunnelRemoteAddress: stringValue("serverAddress", default: "127.0.0.1", configuration: configuration)
        )
        let ipv4Settings = makeIPv4Settings(configuration: configuration)
        let ipv6Settings = makeIPv6Settings(configuration: configuration)
        if let ipv4Settings = ipv4Settings {
            settings.ipv4Settings = ipv4Settings
        }
        if let ipv6Settings = ipv6Settings {
            settings.ipv6Settings = ipv6Settings
        }
        let dnsMode = stringValue("dnsMode", default: "custom", configuration: configuration)
        let configuredDNS = stringArray("customDNSServers", configuration: configuration) ?? stringArray("dnsServers", configuration: configuration)
        if dnsMode != "system", let dns = configuredDNS, !dns.isEmpty {
            let dnsSettings = NEDNSSettings(servers: dns)
            if let search = stringArray("searchDomains", configuration: configuration), !search.isEmpty {
                dnsSettings.matchDomains = search
            }
            settings.dnsSettings = dnsSettings
        }
        if let mtu = intValue("mtu", configuration: configuration) {
            settings.mtu = NSNumber(value: mtu)
        }
        if let overhead = intValue("overheadBytes", configuration: configuration) {
            settings.tunnelOverheadBytes = NSNumber(value: overhead)
        }

        setTunnelNetworkSettings(settings) { error in
            if let error = error {
                self.lastErrorMessage = error.localizedDescription
                print("Failed to set tunnel settings: \(error)")
            }
            completionHandler(error)
        }
    }

    private func connectCarrierSocket() throws {
        guard let socketURL = carrierSocketURL() else {
            throw NSError(domain: "PacketTunnelProvider", code: 1, userInfo: [NSLocalizedDescriptionKey: "missing app group container"])
        }
        socketClient = try BaleCarrierSocketClient(socketURL: socketURL)
        try? socketClient?.sendCommand([
            "type": "status",
            "version": statusVersion,
            "call_established": "yes",
            "data_flow_ok": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "transport_selected": "packet-tunnel",
            "last_error": "",
        ])
    }

    private func carrierSocketURL() -> URL? {
        let configuration = tunnelProviderConfiguration()
        if let rawPath = configuration["carrierSocketPath"] as? String,
           !rawPath.isEmpty {
            if rawPath.hasPrefix("/") {
                return URL(fileURLWithPath: rawPath)
            }
            return BaleAppGroup.sharedContainerURL()?.appendingPathComponent(rawPath, isDirectory: false)
        }
        return BaleAppGroup.carrierSocketURL()
    }

    private func tunnelProviderConfiguration() -> [String: Any] {
        guard let protocolConfiguration = protocolConfiguration as? NETunnelProviderProtocol,
              let configuration = protocolConfiguration.providerConfiguration else {
            return [:]
        }
        return configuration
    }

    private func stringValue(_ key: String, default defaultValue: String, configuration: [String: Any]) -> String {
        if let value = configuration[key] as? String, !value.isEmpty {
            return value
        }
        return defaultValue
    }

    private func stringArray(_ key: String, configuration: [String: Any]) -> [String]? {
        guard let values = configuration[key] as? [String] else {
            return nil
        }
        return values.filter { !$0.isEmpty }
    }

    private func intValue(_ key: String, configuration: [String: Any]) -> Int? {
        if let value = configuration[key] as? Int {
            return value
        }
        if let value = configuration[key] as? NSNumber {
            return value.intValue
        }
        return nil
    }

    private func makeIPv4Settings(configuration: [String: Any]) -> NEIPv4Settings? {
        guard let routes = stringArray("includedIPv4Routes", configuration: configuration),
              !routes.isEmpty else {
            return nil
        }
        let settings = NEIPv4Settings(
            addresses: [stringValue("tunnelIPv4Address", default: "10.77.0.2", configuration: configuration)],
            subnetMasks: [stringValue("tunnelIPv4SubnetMask", default: "255.255.255.0", configuration: configuration)]
        )
        settings.includedRoutes = routes.compactMap { Self.ipv4Route(from: $0) }
        if let excluded = stringArray("excludedRoutes", configuration: configuration) {
            settings.excludedRoutes = excluded.compactMap { Self.ipv4Route(from: $0) }
        }
        return settings
    }

    private func makeIPv6Settings(configuration: [String: Any]) -> NEIPv6Settings? {
        guard let routes = stringArray("includedIPv6Routes", configuration: configuration),
              !routes.isEmpty else {
            return nil
        }
        let settings = NEIPv6Settings(addresses: ["fd00::2"], networkPrefixLengths: [64])
        settings.includedRoutes = routes.compactMap { Self.ipv6Route(from: $0) }
        if let excluded = stringArray("excludedRoutes", configuration: configuration) {
            settings.excludedRoutes = excluded.compactMap { Self.ipv6Route(from: $0) }
        }
        return settings
    }

    private static func ipv4Route(from cidr: String) -> NEIPv4Route? {
        let parts = cidr.split(separator: "/")
        guard parts.count == 2,
              let prefix = Int(parts[1]), prefix >= 0, prefix <= 32 else {
            return nil
        }
        return NEIPv4Route(
            destinationAddress: String(parts[0]),
            subnetMask: subnetMask(from: prefix)
        )
    }

    private static func ipv6Route(from cidr: String) -> NEIPv6Route? {
        let parts = cidr.split(separator: "/")
        guard parts.count == 2,
              let prefix = Int(parts[1]), prefix >= 0, prefix <= 128 else {
            return nil
        }
        return NEIPv6Route(
            destinationAddress: String(parts[0]),
            networkPrefixLength: NSNumber(value: prefix)
        )
    }

    private static func subnetMask(from prefix: Int) -> String {
        let mask = prefix == 0 ? 0 : UInt32.max << (32 - prefix)
        let a = (mask >> 24) & 0xff
        let b = (mask >> 16) & 0xff
        let c = (mask >> 8) & 0xff
        let d = mask & 0xff
        return "\(a).\(b).\(c).\(d)"
    }

    private func startPacketPump() {
        guard let socketClient = socketClient else { return }
        readLoopRunning = true
        queue.async { [weak self] in
            self?.pumpPacketsToCarrier(socketClient: socketClient)
        }
    }

    private func pumpPacketsToCarrier(socketClient: BaleCarrierSocketClient) {
        packetFlow.readPackets { [weak self] packets, _protocols in
            guard let self = self else { return }
            for packet in packets {
                try? socketClient.sendPacket(packet)
            }
            self.writeCarrierPackets(socketClient: socketClient)
            if self.readLoopRunning {
                self.pumpPacketsToCarrier(socketClient: socketClient)
            }
        }
    }

    private func writeCarrierPackets(socketClient: BaleCarrierSocketClient) {
        queue.async { [weak self] in
            guard let self = self else { return }
            while self.readLoopRunning {
                guard let packet = try? socketClient.readPacket() else {
                    break
                }
                guard !packet.isEmpty else {
                    continue
                }
                let proto = Self.protocolNumber(for: packet)
                self.packetFlow.writePackets([packet], withProtocols: [proto])
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        readLoopRunning = false
        socketClient = nil
        completionHandler()
    }

    private func handleControlMessage(_ messageData: Data) -> Data? {
        guard
            let raw = try? JSONSerialization.jsonObject(with: messageData) as? [String: Any],
            let type = raw["type"] as? String
        else {
            return messageData
        }

        if type == "status" {
            let payload: [String: String] = [
                "type": "status",
                "version": statusVersion,
                "state": readLoopRunning ? "running" : "stopped",
                "call_established": socketClient == nil ? "no" : "yes",
                "data_flow_ok": socketClient == nil ? "no" : "yes",
                "route_ready": readLoopRunning ? "yes" : "no",
                "dns_ready": readLoopRunning ? "yes" : "no",
                "transport_selected": "packet-tunnel",
                "last_error": lastErrorMessage,
            ]
            return try? JSONSerialization.data(withJSONObject: payload)
        }

        if type == "ping" {
            return try? JSONSerialization.data(withJSONObject: [
                "type": "pong",
                "version": statusVersion,
            ])
        }

        return messageData
    }

    private static func protocolNumber(for packet: Data) -> NSNumber {
        guard let first = packet.first else {
            return NSNumber(value: AF_INET)
        }
        let version = first >> 4
        if version == 6 {
            return NSNumber(value: AF_INET6)
        }
        return NSNumber(value: AF_INET)
    }
}
