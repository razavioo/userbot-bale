import AppKit
import SwiftUI

private enum BrandTheme {
    static let accent = Color(red: 124.0 / 255.0, green: 92.0 / 255.0, blue: 1.0)
    static let accentHover = Color(red: 142.0 / 255.0, green: 115.0 / 255.0, blue: 1.0)
    static let accentPressed = Color(red: 105.0 / 255.0, green: 72.0 / 255.0, blue: 232.0 / 255.0)
    static let accentSoft = Color(red: 124.0 / 255.0, green: 92.0 / 255.0, blue: 1.0).opacity(0.14)
    static let ok = Color(red: 63.0 / 255.0, green: 224.0 / 255.0, blue: 160.0 / 255.0)
    static let warn = Color(red: 1.0, green: 192.0 / 255.0, blue: 102.0 / 255.0)
    static let err = Color(red: 1.0, green: 92.0 / 255.0, blue: 124.0 / 255.0)
    static let info = Color(red: 126.0 / 255.0, green: 194.0 / 255.0, blue: 1.0)

    static let panelRadius: CGFloat = 14
    static let tileRadius: CGFloat = 12
    static let buttonRadius: CGFloat = 10

    static var meterGradient: LinearGradient {
        LinearGradient(
            colors: [accentHover, info],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

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
        .tint(BrandTheme.accent)
        .onAppear {
            controller.loadState()
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(BrandTheme.accentSoft)
                    .frame(width: 48, height: 48)
                Image(systemName: controller.isTunnelRunning ? "lock.shield.fill" : "lock.open")
                    .font(.system(size: 24, weight: .semibold))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(controller.isTunnelRunning ? BrandTheme.ok : BrandTheme.accent)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("baleobala")
                    .font(.system(size: 28, weight: .semibold))
                    .kerning(-0.4)
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
                                    .font(.body.weight(.semibold))
                                    .frame(minWidth: 210)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(BrandTheme.accent)
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
                    InfoRow(title: "Code signing", value: codeSigningText)
                    InfoRow(title: "Carrier socket", value: carrierSocketText)
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
                Button {
                    controller.exportDiagnostics()
                } label: {
                    Label("Export Diagnostics", systemImage: "square.and.arrow.up")
                }
            }
        }
    }

    private var readinessGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            ForEach(controller.appState.readiness) { item in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: item.ready ? "checkmark.circle.fill" : "exclamationmark.circle")
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(item.ready ? BrandTheme.ok : BrandTheme.warn)
                        .font(.title3)
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
                .padding(12)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(item.ready ? BrandTheme.ok.opacity(0.25) : .white.opacity(0.06), lineWidth: 1)
                )
            }
        }
    }

    private var codeSigningText: String {
        let signing = controller.appState.codeSigning
        let state = signing["state"] ?? "unknown"
        let detail = signing["detail"] ?? ""
        return detail.isEmpty ? state : "\(state): \(detail)"
    }

    private var carrierSocketText: String {
        let backend = controller.appState.backend
        let endpoint = backend["endpoint"] ?? "not running"
        let state = backend["carrier_ready"] ?? backend["state"] ?? "unknown"
        return "\(state) · \(endpoint)"
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
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: BrandTheme.panelRadius, style: .continuous)
                    .fill(.regularMaterial)
            )
            .overlay(
                RoundedRectangle(cornerRadius: BrandTheme.panelRadius, style: .continuous)
                    .strokeBorder(.white.opacity(0.08), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.18), radius: 24, x: 0, y: 8)
    }
}

private struct SectionTitle: View {
    var text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(.caption.weight(.bold))
            .kerning(1.2)
            .foregroundStyle(.secondary)
    }
}

private struct StatusBadge: View {
    var state: ConnectionState
    var text: String

    @State private var pulse: Bool = false

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
                .overlay(
                    Circle()
                        .stroke(color.opacity(0.35), lineWidth: 1)
                        .scaleEffect(pulse ? 2.2 : 1.0)
                        .opacity(pulse ? 0 : 0.9)
                )
                .onAppear {
                    guard pulsing else { return }
                    withAnimation(.easeOut(duration: 1.2).repeatForever(autoreverses: false)) {
                        pulse = true
                    }
                }
            Text(text)
                .font(.callout.weight(.semibold))
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 12)
        .background(
            Capsule().fill(color.opacity(0.10))
        )
        .overlay(
            Capsule().strokeBorder(color.opacity(0.35), lineWidth: 1)
        )
    }

    private var pulsing: Bool {
        switch state {
        case .connecting, .reconnecting: return true
        default: return false
        }
    }

    private var color: Color {
        switch state {
        case .connected:
            return BrandTheme.ok
        case .connecting, .reconnecting:
            return BrandTheme.accent
        case .blocked, .degraded, .expiredSession:
            return BrandTheme.warn
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
                .stroke(.white.opacity(0.06), lineWidth: 14)
                .frame(width: 140, height: 140)
            Circle()
                .trim(from: 0, to: progress)
                .stroke(strokeStyle, style: StrokeStyle(lineWidth: 14, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .frame(width: 140, height: 140)
                .shadow(color: glowColor.opacity(0.45), radius: 14, x: 0, y: 0)
                .animation(.easeInOut(duration: 0.4), value: progress)
            Image(systemName: icon)
                .font(.system(size: 38, weight: .semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(glowColor)
        }
        .frame(width: 160, height: 160)
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

    private var strokeStyle: AnyShapeStyle {
        switch state {
        case .connected:
            return AnyShapeStyle(
                LinearGradient(colors: [BrandTheme.ok, BrandTheme.ok.opacity(0.7)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
            )
        case .connecting, .reconnecting:
            return AnyShapeStyle(BrandTheme.meterGradient)
        case .degraded, .blocked:
            return AnyShapeStyle(BrandTheme.warn)
        default:
            return AnyShapeStyle(Color.secondary)
        }
    }

    private var glowColor: Color {
        switch state {
        case .connected:
            return BrandTheme.ok
        case .connecting, .reconnecting:
            return BrandTheme.accent
        case .degraded, .blocked:
            return BrandTheme.warn
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
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(BrandTheme.accentSoft)
                    .frame(width: 34, height: 34)
                Image(systemName: systemImage)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(BrandTheme.accent)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title.uppercased())
                    .font(.caption2.weight(.bold))
                    .kerning(0.8)
                    .foregroundStyle(.secondary)
                Text(value.isEmpty ? "n/a" : value)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: BrandTheme.tileRadius, style: .continuous)
                .fill(.thinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: BrandTheme.tileRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.06), lineWidth: 1)
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
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(BrandTheme.accentSoft)
                    .frame(width: 34, height: 34)
                Image(systemName: "tray")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(BrandTheme.accent)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: BrandTheme.tileRadius, style: .continuous)
                .fill(.thinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: BrandTheme.tileRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.06), lineWidth: 1)
        )
    }
}
