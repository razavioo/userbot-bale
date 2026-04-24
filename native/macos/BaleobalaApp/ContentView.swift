import AppKit
import SwiftUI

private enum AppSection: String, CaseIterable, Identifiable {
    case overview
    case relays
    case privacy
    case settings
    case diagnostics

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "Overview"
        case .relays: return "Relays"
        case .privacy: return "Privacy"
        case .settings: return "Settings"
        case .diagnostics: return "Diagnostics"
        }
    }

    var icon: String {
        switch self {
        case .overview: return "shield"
        case .relays: return "point.3.connected.trianglepath.dotted"
        case .privacy: return "lock"
        case .settings: return "gearshape"
        case .diagnostics: return "waveform.path.ecg"
        }
    }
}

struct ContentView: View {
    @ObservedObject var controller: BaleAppController
    @State private var selection: AppSection = .overview
    @State private var phone = ""
    @State private var smsCode = ""
    @State private var relayName = "home-relay"
    @State private var relayCode = ""
    @State private var relayRole = "client"

    var body: some View {
        NavigationSplitView {
            List(AppSection.allCases, selection: $selection) { section in
                Label(section.title, systemImage: section.icon)
                    .tag(section)
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 260)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    switch selection {
                    case .overview:
                        overview
                    case .relays:
                        relays
                    case .privacy:
                        privacy
                    case .settings:
                        settings
                    case .diagnostics:
                        diagnostics
                    }
                }
                .padding(24)
                .frame(maxWidth: 980, alignment: .leading)
            }
            .navigationTitle(selection.title)
            .toolbar {
                ToolbarItemGroup {
                    Button {
                        controller.loadState()
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    Button {
                        controller.installCurrentProfile()
                    } label: {
                        Label("Install Profile", systemImage: "square.and.arrow.down")
                    }
                }
            }
        }
        .frame(minWidth: 920, minHeight: 640)
        .onAppear {
            controller.loadState()
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: controller.isTunnelRunning ? "lock.shield.fill" : "lock.open")
                .font(.system(size: 28, weight: .semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(controller.isTunnelRunning ? .green : .secondary)
                .frame(width: 40, height: 40)
            VStack(alignment: .leading, spacing: 3) {
                Text("baleobala")
                    .font(.system(size: 26, weight: .semibold))
                Text("Native macOS VPN")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            StatusBadge(state: controller.appState.connectionState, text: controller.connectionText)
        }
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                HStack(alignment: .center, spacing: 20) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(controller.appState.headline)
                            .font(.system(size: 32, weight: .semibold))
                        Text(controller.appState.detail)
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 10) {
                            Button {
                                controller.primaryAction()
                            } label: {
                                Label(controller.primaryActionTitle, systemImage: controller.primaryActionSystemImage)
                                    .frame(minWidth: 210)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)

                            Menu {
                                Button("Pause 15 Minutes") { controller.pause(minutes: 15) }
                                Button("Pause 1 Hour") { controller.pause(minutes: 60) }
                                Button("Refresh Status") { controller.loadState() }
                            } label: {
                                Label("More", systemImage: "ellipsis.circle")
                            }
                            .controlSize(.large)
                        }
                    }
                    Spacer()
                    ConnectionMeter(state: controller.appState.connectionState)
                }
            }

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                MetricTile(title: "Account", value: controller.accountText, systemImage: "person.crop.circle")
                MetricTile(title: "Relay", value: controller.selectedRelayText, systemImage: "network")
                MetricTile(title: "Transport", value: controller.appState.backend["transport_selected"] ?? controller.draft.networkPolicy.transportPreference, systemImage: "antenna.radiowaves.left.and.right")
            }

            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionTitle("Readiness")
                    readinessGrid
                }
            }
        }
    }

    private var relays: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("Bale Sign-In")
                    Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                        GridRow {
                            Text("Phone")
                                .foregroundStyle(.secondary)
                            TextField("+98 912 345 6789", text: $phone)
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("SMS code")
                                .foregroundStyle(.secondary)
                            TextField("12345", text: $smsCode)
                                .textFieldStyle(.roundedBorder)
                        }
                    }
                    HStack {
                        Button {
                            controller.startAuth(phone: phone)
                        } label: {
                            Label("Send Code", systemImage: "paperplane")
                        }
                        Button {
                            controller.verifyAuth(phone: phone, code: smsCode, transactionHash: controller.authTransactionHash)
                        } label: {
                            Label("Verify", systemImage: "checkmark.seal")
                        }
                        .disabled(controller.authTransactionHash.isEmpty)
                    }
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("Relay Pairing")
                    Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                        GridRow {
                            Text("Relay name")
                                .foregroundStyle(.secondary)
                            TextField("home-relay", text: $relayName)
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("Pairing code")
                                .foregroundStyle(.secondary)
                            TextField("Optional invite or pairing code", text: $relayCode)
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("Role")
                                .foregroundStyle(.secondary)
                            Picker("Role", selection: $relayRole) {
                                Text("Use relay").tag("client")
                                Text("Share connection").tag("relay")
                            }
                            .pickerStyle(.segmented)
                        }
                    }
                    HStack {
                        Button {
                            controller.pairRelay(name: relayName, code: relayCode, role: relayRole)
                        } label: {
                            Label("Save Pairing", systemImage: "link")
                        }
                        Button {
                            controller.syncPairing()
                        } label: {
                            Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                        }
                    }
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionTitle("Saved Relays")
                    if controller.appState.relays.isEmpty {
                        EmptyStateRow(title: "No relay is ready", detail: "Create or join a relay pairing to continue.")
                    } else {
                        ForEach(controller.appState.relays) { relay in
                            InfoRow(title: relay.name, value: "\(relay.role) - \(relay.status)")
                        }
                    }
                }
            }
        }
    }

    private var privacy: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("Network Protection")
                    Picker("Kill switch", selection: policyBinding(\.killSwitchMode)) {
                        Text("Off").tag("off")
                        Text("On").tag("on")
                        Text("Lockdown").tag("lockdown")
                    }
                    .pickerStyle(.segmented)
                    Toggle("Auto-connect", isOn: policyBinding(\.autoConnect))
                    Toggle("Allow LAN traffic", isOn: policyBinding(\.allowLAN))
                    UnavailableToggle(
                        title: "Launch at login",
                        isOn: controller.draft.networkPolicy.launchAtLogin,
                        reason: "Requires the signed app helper."
                    )
                    UnavailableToggle(
                        title: "Proxy fallback",
                        isOn: controller.draft.networkPolicy.fallbackProxyEnabled,
                        reason: "Requires the signed helper to enforce fallback routing."
                    )
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("DNS")
                    Picker("DNS mode", selection: policyBinding(\.dnsMode)) {
                        Text("System").tag("system")
                        Text("Custom").tag("custom")
                    }
                    TextField("1.1.1.1, 9.9.9.9", text: policyArrayBinding(\.customDNSServers))
                        .textFieldStyle(.roundedBorder)
                        .disabled(controller.draft.networkPolicy.dnsMode == "system")
                    InfoRow(title: "DNS blocklists", value: "Disabled until the resolver/blocklist helper is bundled.")
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionTitle("Wi-Fi Rules")
                    InfoRow(title: "Trusted networks", value: "Disabled until the signed network monitor helper is bundled.")
                    InfoRow(title: "Untrusted networks", value: "Default action: connect.")
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("Routing")
                    Picker("Transport", selection: policyBinding(\.transportPreference)) {
                        Text("Auto").tag("auto")
                        Text("DataChannel").tag("dc")
                        Text("Audio").tag("audio")
                        Text("RPC").tag("rpc")
                    }
                    .pickerStyle(.segmented)
                    Picker("Split tunneling", selection: policyBinding(\.splitTunnelMode)) {
                        Text("Off").tag("off")
                        Text("Exclude apps").tag("exclude")
                    }
                    .disabled(true)
                    TextField("Split tunnel exclusions", text: policyArrayBinding(\.splitTunnelExclusions))
                        .textFieldStyle(.roundedBorder)
                        .disabled(true)
                    InfoRow(title: "Split tunneling", value: "Requires the signed helper process to enforce app exclusions.")
                }
            }
        }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("System Profile")
                    Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                        GridRow {
                            Text("Profile name")
                                .foregroundStyle(.secondary)
                            TextField("baleobala", text: draftBinding(\.displayName))
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("Server address")
                                .foregroundStyle(.secondary)
                            TextField("127.0.0.1", text: draftBinding(\.serverAddress))
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("DNS servers")
                                .foregroundStyle(.secondary)
                            TextField("1.1.1.1, 9.9.9.9", text: multiValueBinding(\.dnsServers))
                                .textFieldStyle(.roundedBorder)
                        }
                        GridRow {
                            Text("Packet MTU")
                                .foregroundStyle(.secondary)
                            TextField("1400", value: intBinding(\.mtu), format: .number)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 180)
                        }
                    }
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 14) {
                    SectionTitle("Advanced Identifiers")
                    TextField("Packet tunnel bundle identifier", text: draftBinding(\.providerBundleIdentifier))
                        .textFieldStyle(.roundedBorder)
                    TextField("App group identifier", text: draftBinding(\.appGroupIdentifier))
                        .textFieldStyle(.roundedBorder)
                }
            }
        }
    }

    private var diagnostics: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionTitle("Connection Timeline")
                    InfoRow(title: "App-control bridge", value: controller.helperText)
                    InfoRow(title: "Last action", value: controller.actionMessage.isEmpty ? "None" : controller.actionMessage)
                    InfoRow(title: "Profile", value: controller.profileText)
                    InfoRow(title: "Tunnel", value: controller.connectionText)
                }
            }

            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionTitle("Path Health")
                    readinessGrid
                    Divider()
                    InfoRow(title: "Route", value: controller.appState.backend["route_ready"] ?? "no")
                    InfoRow(title: "DNS", value: controller.appState.backend["dns_ready"] ?? "no")
                    InfoRow(title: "Call", value: controller.appState.backend["call_established"] ?? "no")
                    InfoRow(title: "Data flow", value: controller.appState.backend["data_flow_ok"] ?? "no")
                    InfoRow(title: "Last error", value: controller.appState.backend["last_error"] ?? "")
                }
            }

            HStack {
                Button {
                    controller.loadState()
                } label: {
                    Label("Refresh Diagnostics", systemImage: "arrow.clockwise")
                }
                Button {
                    controller.installCurrentProfile()
                } label: {
                    Label("Reinstall Profile", systemImage: "wrench.and.screwdriver")
                }
            }
        }
    }

    private var readinessGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
            ForEach(controller.appState.readiness) { item in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: item.ready ? "checkmark.circle.fill" : "exclamationmark.circle")
                        .foregroundStyle(item.ready ? .green : .orange)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(item.label)
                            .font(.headline)
                        Text(item.detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Spacer()
                }
                .padding(10)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(.quaternary, lineWidth: 1)
                )
            }
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
                let values = commaSeparatedValues(newValue)
                controller.updateDraft { $0[keyPath: keyPath] = values }
            }
        )
    }

    private func policyBinding<Value>(_ keyPath: WritableKeyPath<BaleNetworkPolicy, Value>) -> Binding<Value> {
        Binding(
            get: { controller.draft.networkPolicy[keyPath: keyPath] },
            set: { newValue in
                var copy = controller.draft.networkPolicy
                copy[keyPath: keyPath] = newValue
                controller.updateNetworkPolicy(copy)
            }
        )
    }

    private func policyArrayBinding(_ keyPath: WritableKeyPath<BaleNetworkPolicy, [String]>) -> Binding<String> {
        Binding(
            get: { controller.draft.networkPolicy[keyPath: keyPath].joined(separator: ", ") },
            set: { newValue in
                var copy = controller.draft.networkPolicy
                copy[keyPath: keyPath] = commaSeparatedValues(newValue)
                controller.updateNetworkPolicy(copy)
            }
        )
    }

    private func commaSeparatedValues(_ value: String) -> [String] {
        value
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}

struct MenuBarStatusView: View {
    @ObservedObject var controller: BaleAppController

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(controller.appState.headline)
                .font(.headline)
            Text(controller.connectionText)
                .foregroundStyle(.secondary)
            Divider()
            Button(controller.primaryActionTitle) {
                controller.primaryAction()
            }
            Button("Pause 15 Minutes") {
                controller.pause(minutes: 15)
            }
            Button("Refresh") {
                controller.loadState()
            }
            Divider()
            Button("Open Baleobala") {
                NSApp.activate(ignoringOtherApps: true)
            }
            Button("Quit Baleobala") {
                NSApp.terminate(nil)
            }
        }
        .padding(4)
        .onAppear {
            controller.loadState()
        }
    }
}

private struct Panel<Content: View>: View {
    private let content: () -> Content

    init(@ViewBuilder content: @escaping () -> Content) {
        self.content = content
    }

    var body: some View {
        content()
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(.quaternary, lineWidth: 1)
            )
    }
}

private struct SectionTitle: View {
    var text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.headline)
    }
}

private struct StatusBadge: View {
    var state: ConnectionState
    var text: String

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(text)
                .font(.callout.weight(.medium))
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 10)
        .background(.thinMaterial, in: Capsule())
    }

    private var color: Color {
        switch state {
        case .connected:
            return .green
        case .connecting, .reconnecting:
            return .blue
        case .blocked, .degraded, .expiredSession:
            return .orange
        default:
            return .secondary
        }
    }
}

private struct ConnectionMeter: View {
    var state: ConnectionState

    var body: some View {
        ZStack {
            Circle()
                .stroke(.quaternary, lineWidth: 12)
                .frame(width: 132, height: 132)
            Circle()
                .trim(from: 0, to: progress)
                .stroke(color, style: StrokeStyle(lineWidth: 12, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .frame(width: 132, height: 132)
            Image(systemName: icon)
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(color)
        }
        .frame(width: 150, height: 150)
    }

    private var progress: CGFloat {
        switch state {
        case .connected:
            return 1.0
        case .connecting, .reconnecting:
            return 0.68
        case .degraded, .blocked:
            return 0.42
        default:
            return 0.22
        }
    }

    private var color: Color {
        switch state {
        case .connected:
            return .green
        case .connecting, .reconnecting:
            return .blue
        case .degraded, .blocked:
            return .orange
        default:
            return .gray
        }
    }

    private var icon: String {
        switch state {
        case .connected:
            return "lock.fill"
        case .connecting, .reconnecting:
            return "arrow.triangle.2.circlepath"
        case .degraded, .blocked:
            return "exclamationmark.triangle.fill"
        default:
            return "power"
        }
    }
}

private struct MetricTile: View {
    var title: String
    var value: String
    var systemImage: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3)
                .frame(width: 28)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value.isEmpty ? "n/a" : value)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(.quaternary, lineWidth: 1)
        )
    }
}

private struct UnavailableToggle: View {
    var title: String
    var isOn: Bool
    var reason: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(title, isOn: .constant(isOn))
                .disabled(true)
            Text(reason)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct InfoRow: View {
    var title: String
    var value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .foregroundStyle(.secondary)
                .frame(width: 160, alignment: .leading)
            Text(value.isEmpty ? "n/a" : value)
                .textSelection(.enabled)
            Spacer()
        }
    }
}

private struct EmptyStateRow: View {
    var title: String
    var detail: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "tray")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
