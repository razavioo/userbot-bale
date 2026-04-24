import Foundation

enum BaleAppControlError: Error, LocalizedError {
    case helperUnavailable
    case invalidResponse
    case commandFailed(String)

    var errorDescription: String? {
        switch self {
        case .helperUnavailable:
            return "The app-control helper is not configured."
        case .invalidResponse:
            return "The app-control helper returned an invalid response."
        case .commandFailed(let message):
            return message.isEmpty ? "The app-control command failed." : message
        }
    }
}

struct RawAppControlResponse {
    var ok: Bool
    var message: String
    var data: [String: Any]
}

final class BaleAppControlClient {
    private struct HelperCommand {
        var executable: String
        var arguments: [String]
    }

    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    func status(completion: @escaping (Result<BaleAppState, Error>) -> Void) {
        send(command: "status", payload: [:]) { [decoder] result in
            completion(result.flatMap { response in
                decodeState(response.data, decoder: decoder)
            })
        }
    }

    func setNetworkPolicy(_ policy: BaleNetworkPolicy, completion: @escaping (Result<BaleAppState, Error>) -> Void) {
        guard let payload = try? JSONSerialization.jsonObject(with: encoder.encode(policy)) as? [String: Any] else {
            completion(.failure(BaleAppControlError.invalidResponse))
            return
        }
        send(command: "setNetworkPolicy", payload: ["policy": payload]) { [decoder] result in
            completion(result.flatMap { response in
                decodeState(response.data, decoder: decoder)
            })
        }
    }

    func send(command: String, payload: [String: Any], completion: @escaping (Result<RawAppControlResponse, Error>) -> Void) {
        guard let helper = helperCommand() else {
            completion(.failure(BaleAppControlError.helperUnavailable))
            return
        }

        DispatchQueue.global(qos: .utility).async {
            do {
                let request: [String: Any] = [
                    "command": command,
                    "payload": payload,
                ]
                let data = try JSONSerialization.data(withJSONObject: request, options: [])
                let process = Process()
                process.executableURL = URL(fileURLWithPath: helper.executable)
                process.arguments = helper.arguments

                let input = Pipe()
                let output = Pipe()
                let error = Pipe()
                process.standardInput = input
                process.standardOutput = output
                process.standardError = error
                process.environment = helperEnvironment(from: ProcessInfo.processInfo.environment)

                try process.run()
                input.fileHandleForWriting.write(data)
                input.fileHandleForWriting.closeFile()
                process.waitUntilExit()

                let outputData = output.fileHandleForReading.readDataToEndOfFile()
                let stderr = String(data: error.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                guard !outputData.isEmpty else {
                    throw BaleAppControlError.commandFailed(stderr.trimmingCharacters(in: .whitespacesAndNewlines))
                }
                let object = try JSONSerialization.jsonObject(with: outputData) as? [String: Any]
                guard let object = object else {
                    throw BaleAppControlError.invalidResponse
                }
                let ok = object["ok"] as? Bool ?? false
                let message = object["message"] as? String ?? ""
                let response = RawAppControlResponse(
                    ok: ok,
                    message: message,
                    data: object["data"] as? [String: Any] ?? [:]
                )
                if ok {
                    DispatchQueue.main.async { completion(.success(response)) }
                } else {
                    DispatchQueue.main.async { completion(.failure(BaleAppControlError.commandFailed(message))) }
                }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    private func helperCommand() -> HelperCommand? {
        let env = ProcessInfo.processInfo.environment
        if let helper = env["BALEOBALA_APP_CONTROL_HELPER"], !helper.isEmpty {
            return HelperCommand(executable: helper, arguments: [])
        }
        if let python = env["BALEOBALA_PYTHON"], !python.isEmpty {
            return HelperCommand(executable: python, arguments: ["-m", "baleobala.control.app_control"])
        }
        if let helper = Bundle.main.url(forResource: "baleobala-app-control", withExtension: nil) {
            return HelperCommand(executable: "/bin/sh", arguments: [helper.path])
        }
        return nil
    }
}

private func helperEnvironment(from base: [String: String]) -> [String: String] {
    var env = base
    if let appGroup = Bundle.main.object(forInfoDictionaryKey: "BaleAppGroupIdentifier") as? String, !appGroup.isEmpty {
        env["BALEOBALA_APP_GROUP_IDENTIFIER"] = appGroup
    }
    if let provider = Bundle.main.object(forInfoDictionaryKey: "BaleProviderBundleIdentifier") as? String, !provider.isEmpty {
        env["BALEOBALA_PROVIDER_BUNDLE_ID"] = provider
    }
    return env
}

private func decodeState(_ data: [String: Any], decoder: JSONDecoder) -> Result<BaleAppState, Error> {
    do {
        let encoded = try JSONSerialization.data(withJSONObject: data, options: [])
        return .success(try decoder.decode(BaleAppState.self, from: encoded))
    } catch {
        return .failure(error)
    }
}
