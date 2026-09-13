import Foundation

struct HelperResult {
    let ok: Bool
    let message: String
    let data: [String: Any]
}

enum HelperError: Error, LocalizedError {
    case helperMissing
    case launchFailed(String)
    case badResponse(String)

    var errorDescription: String? {
        switch self {
        case .helperMissing: return "Helper script missing from app bundle."
        case .launchFailed(let s): return "Failed to launch helper: \(s)"
        case .badResponse(let s): return "Helper returned invalid response: \(s)"
        }
    }
}

actor ProxyHelperClient {
    private let helperPath: String

    init() {
        if let bundled = Bundle.main.path(forResource: "userbot-bale-proxy-helper", ofType: nil) {
            helperPath = bundled
        } else if let envOverride = ProcessInfo.processInfo.environment["USERBOT_BALE_PROXY_HELPER"] {
            helperPath = envOverride
        } else {
            helperPath = "/Users/emad/IdeaProjects/userbot-bale/native/macos/UserbotBaleProxyApp/Resources/userbot-bale-proxy-helper"
        }
    }

    func send(command: String, payload: [String: Any] = [:]) async throws -> HelperResult {
        guard FileManager.default.isExecutableFile(atPath: helperPath) else {
            throw HelperError.helperMissing
        }
        let request: [String: Any] = ["command": command, "payload": payload]
        let requestData = try JSONSerialization.data(withJSONObject: request)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: helperPath)
        process.arguments = ["--once"]

        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
        } catch {
            throw HelperError.launchFailed(error.localizedDescription)
        }

        try stdin.fileHandleForWriting.write(contentsOf: requestData)
        try stdin.fileHandleForWriting.close()

        let outData = try await readToEnd(stdout.fileHandleForReading)
        _ = try? await readToEnd(stderr.fileHandleForReading)
        process.waitUntilExit()

        guard let json = try? JSONSerialization.jsonObject(with: outData) as? [String: Any] else {
            let raw = String(data: outData, encoding: .utf8) ?? ""
            throw HelperError.badResponse(raw)
        }
        return HelperResult(
            ok: (json["ok"] as? Bool) ?? false,
            message: (json["message"] as? String) ?? "",
            data: (json["data"] as? [String: Any]) ?? [:]
        )
    }

    private func readToEnd(_ handle: FileHandle) async throws -> Data {
        try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global().async {
                let data = handle.readDataToEndOfFile()
                cont.resume(returning: data)
            }
        }
    }
}
