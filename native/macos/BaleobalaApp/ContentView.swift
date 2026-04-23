import SwiftUI

struct ContentView: View {
    @ObservedObject var controller: BaleAppController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("baleobala for macOS")
                        .font(.largeTitle.weight(.bold))
                    Text("This companion app installs the system VPN profile and starts or stops the packet tunnel after sign-in and pairing are already handled in the shared Bale app flow.")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("System tunnel status")
                            .font(.headline)
                        Text(controller.statusText)
                            .font(.title3.weight(.semibold))
                        Text(controller.connectionText)
                            .foregroundStyle(.secondary)
                        Text(controller.profileText)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Setup")
                            .font(.headline)
                        TextField("Profile name", text: draftBinding(\.displayName))
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

                        Text("Install writes the route, DNS, and shared runtime settings that macOS uses for the packet tunnel.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                DisclosureGroup("Advanced identifiers") {
                    VStack(alignment: .leading, spacing: 10) {
                        TextField("Packet tunnel bundle identifier", text: draftBinding(\.providerBundleIdentifier))
                        TextField("App group identifier", text: draftBinding(\.appGroupIdentifier))
                        Text("Most people should leave these values alone. Change them only when working on Xcode signing or bundle configuration.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.top, 8)
                }

                HStack(spacing: 12) {
                    Button("Refresh Status") {
                        controller.loadState()
                    }
                    Button("Install System Profile") {
                        controller.installCurrentProfile()
                    }
                    Button("Start Tunnel") {
                        controller.connect()
                    }
                    .disabled(!controller.canConnect)
                    Button("Stop Tunnel") {
                        controller.disconnect()
                    }
                    .disabled(!controller.canConnect)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                Text("Typical flow: sign in and pair in the Bale app, install the system profile here, approve any macOS permission prompts, then start the tunnel.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
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
