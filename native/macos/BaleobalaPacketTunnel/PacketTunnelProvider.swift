import NetworkExtension
import Foundation

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let queue = DispatchQueue(label: "com.baleobala.packet-tunnel")
    private var socketClient: BaleCarrierSocketClient?
    private var readLoopRunning = false

    override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        do {
            try configureNetwork()
            try connectCarrierSocket()
            startPacketPump()
            completionHandler(nil)
        } catch {
            completionHandler(error)
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        socketClient = nil
        completionHandler()
    }

    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)? = nil) {
        completionHandler?(messageData)
    }

    private func configureNetwork() throws {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: BaleAppGroup.identifier)
        let ipv4 = NEIPv4Settings(addresses: ["10.7.0.2"], subnetMasks: ["255.255.255.0"])
        ipv4.includedRoutes = [NEIPv4Route.default()]
        ipv4.excludedRoutes = [
            NEIPv4Route(destinationAddress: "127.0.0.0", subnetMask: "255.0.0.0"),
        ]

        let ipv6 = NEIPv6Settings(addresses: ["fd00::2"], networkPrefixLengths: [64])
        ipv6.includedRoutes = [NEIPv6Route.default()]

        settings.ipv4Settings = ipv4
        settings.ipv6Settings = ipv6
        settings.dnsSettings = NEDNSSettings(servers: ["1.1.1.1", "9.9.9.9"])
        settings.mtu = 1400
        settings.tunnelOverheadBytes = 80

        setTunnelNetworkSettings(settings) { error in
            if let error = error {
                print("Failed to set tunnel settings: \(error)")
            }
        }
    }

    private func connectCarrierSocket() throws {
        guard let socketURL = BaleAppGroup.carrierSocketURL() else {
            throw NSError(domain: "PacketTunnelProvider", code: 1, userInfo: [NSLocalizedDescriptionKey: "missing app group container"])
        }
        socketClient = try BaleCarrierSocketClient(socketURL: socketURL)
    }

    private func startPacketPump() {
        guard let socketClient = socketClient else { return }
        readLoopRunning = true
        queue.async { [weak self] in
            self?.pumpPacketsToCarrier(socketClient: socketClient)
        }
    }

    private func pumpPacketsToCarrier(socketClient: BaleCarrierSocketClient) {
        packetFlow.readPackets { [weak self] packets, protocols in
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
                guard let packet = packet, !packet.isEmpty else {
                    continue
                }
                let proto = Self.protocolNumber(for: packet)
                self.packetFlow.writePackets([packet], withProtocols: [proto])
            }
        }
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
