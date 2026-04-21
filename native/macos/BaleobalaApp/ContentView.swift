import SwiftUI

struct ContentView: View {
    @ObservedObject var controller: BaleAppController

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("baleobala")
                    .font(.largeTitle.weight(.bold))
                Text("A real macOS VPN control panel for the Bale packet tunnel.")
                    .font(.headline)
                    .foregroundStyle(.secondary)
            }

            Divider()

            VStack(alignment: .leading, spacing: 12) {
                Text("Tunnel profile")
                    .font(.headline)

                TextField("Profile name", text: draftBinding(\.displayName))
                TextField("Packet tunnel bundle identifier", text: draftBinding(\.providerBundleIdentifier))
                TextField("App group identifier", text: draftBinding(\.appGroupIdentifier))
                TextField("Tunnel server address", text: draftBinding(\.serverAddress))

                HStack(spacing: 12) {
                    VStack(alignment: .leading) {
                        Text("DNS servers")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("1.1.1.1, 9.9.9.9", text: multiValueBinding(\.dnsServers))
                    }
                    VStack(alignment: .leading) {
                        Text("Packet MTU")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("1400", value: intBinding(\.mtu), format: .number)
                            .frame(width: 140)
                    }
                }

                Text("This profile writes the shared route, DNS, and app-group settings that the packet-tunnel extension uses.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("State")
                    .font(.headline)
                Text(controller.statusText)
                Text(controller.profileText)
                    .foregroundStyle(.secondary)
                Text(controller.connectionText)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 12) {
                Button("Refresh") {
                    controller.loadState()
                }
                Button("Install profile") {
                    controller.installCurrentProfile()
                }
                Button("Start tunnel") {
                    controller.connect()
                }
                .disabled(!controller.canConnect)
                Button("Stop tunnel") {
                    controller.disconnect()
                }
                .disabled(!controller.canConnect)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            Text("Refresh reloads the installed tunnel profile from System Settings. Install writes the profile, Connect starts the packet tunnel, and Disconnect stops it.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(minWidth: 640, minHeight: 420)
        .onAppear {
            controller.loadState()
        }
    }

    private func draftBinding(_ keyPath: WritableKeyPath<BaleTunnelConfiguration, String>) -> Binding<String> {
        Binding(
            get: { controller.draft[keyPath: keyPath] },
            set: { newValue in
                controller.updateDraft { $0[keyPath: keyPath] = newValue }
            }
        )
    }

    private func intBinding(_ keyPath: WritableKeyPath<BaleTunnelConfiguration, Int>) -> Binding<Int> {
        Binding(
            get: { controller.draft[keyPath: keyPath] },
            set: { newValue in
                controller.updateDraft { $0[keyPath: keyPath] = newValue }
            }
        )
    }

    private func multiValueBinding(_ keyPath: WritableKeyPath<BaleTunnelConfiguration, [String]>) -> Binding<String> {
        Binding(
            get: { controller.draft[keyPath: keyPath].joined(separator: ", ") },
            set: { newValue in
                let values = newValue
                    .split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty }
                controller.updateDraft { $0[keyPath: keyPath] = values }
            }
        )
    }
}
