import Foundation
import SwiftUI

struct AuthState: Equatable {
    var loggedIn: Bool = false
    var phone: String = ""
    var accountPath: String = ""
}

struct ProxyState: Equatable {
    var running: Bool = false
    var pid: Int? = nil
    var listenHost: String = "127.0.0.1"
    var listenPort: Int = 1080
    var service: String = "Wi-Fi"
    var startedAt: Date? = nil

    var endpoint: String { "socks5://\(listenHost):\(listenPort)" }
}

struct ProxySettingsView: Equatable {
    var listenPort: Int = 1080
    var service: String = "Wi-Fi"
    var availableServices: [String] = []
}

@MainActor
final class ProxyController: ObservableObject {
    @Published var auth = AuthState()
    @Published var proxy = ProxyState()
    @Published var settings = ProxySettingsView()
    @Published var statusMessage: String = ""
    @Published var busy: Bool = false
    @Published var transactionHash: String = ""

    private let helper = ProxyHelperClient()
    private var pollTask: Task<Void, Never>?

    deinit {
        pollTask?.cancel()
    }

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                await self?.refresh(silent: true)
            }
        }
    }

    func refresh(silent: Bool = false) {
        Task { await refresh(silent: silent) }
    }

    private func refresh(silent: Bool) async {
        do {
            let result = try await helper.send(command: "status")
            apply(result)
            if !silent { statusMessage = "" }
        } catch {
            if !silent { statusMessage = error.localizedDescription }
        }
        if settings.availableServices.isEmpty {
            await loadServices()
        }
    }

    func loadServices() async {
        if let result = try? await helper.send(command: "listServices") {
            settings.availableServices = (result.data["services"] as? [String]) ?? []
        }
    }

    func startAuth(phone: String) {
        guard !phone.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        runCommand("authStart", payload: ["phone": phone]) { [weak self] result in
            guard let self else { return }
            self.transactionHash = (result.data["transaction_hash"] as? String) ?? ""
        }
    }

    func verifyAuth(code: String) {
        guard !transactionHash.isEmpty else {
            statusMessage = "Request a code first."
            return
        }
        runCommand("authVerify", payload: ["transaction_hash": transactionHash, "code": code]) { [weak self] _ in
            self?.transactionHash = ""
        }
    }

    func logout() {
        runCommand("logout")
    }

    func startProxy() {
        runCommand("proxyStart")
    }

    func stopProxy() {
        runCommand("proxyStop")
    }

    func saveSettings(port: Int, service: String) {
        runCommand("saveSettings", payload: ["listen_port": port, "network_service": service])
    }

    func stopProxyAndWait() async {
        if let result = try? await helper.send(command: "proxyStop") {
            apply(result)
        }
    }

    private func runCommand(_ command: String, payload: [String: Any] = [:], onSuccess: ((HelperResult) -> Void)? = nil) {
        Task {
            busy = true
            defer { busy = false }
            do {
                let result = try await helper.send(command: command, payload: payload)
                apply(result)
                statusMessage = result.message
                if result.ok { onSuccess?(result) }
            } catch {
                statusMessage = error.localizedDescription
            }
        }
    }

    private func apply(_ result: HelperResult) {
        if let authDict = result.data["auth"] as? [String: Any] {
            auth.loggedIn = (authDict["logged_in"] as? Bool) ?? false
            auth.phone = (authDict["phone"] as? String) ?? ""
            auth.accountPath = (authDict["account_path"] as? String) ?? ""
        }
        if let proxyDict = result.data["proxy"] as? [String: Any] {
            proxy.running = (proxyDict["running"] as? Bool) ?? false
            proxy.pid = proxyDict["pid"] as? Int
            proxy.listenHost = (proxyDict["listen_host"] as? String) ?? proxy.listenHost
            proxy.listenPort = (proxyDict["listen_port"] as? Int) ?? proxy.listenPort
            proxy.service = (proxyDict["service"] as? String) ?? proxy.service
            if let ts = proxyDict["started_at"] as? Double {
                proxy.startedAt = Date(timeIntervalSince1970: ts)
            } else {
                proxy.startedAt = nil
            }
        }
        if let settingsDict = result.data["settings"] as? [String: Any] {
            settings.listenPort = (settingsDict["listen_port"] as? Int) ?? settings.listenPort
            settings.service = (settingsDict["network_service"] as? String) ?? settings.service
        }
    }
}
