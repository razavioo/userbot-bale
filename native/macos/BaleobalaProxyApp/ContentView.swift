import SwiftUI

struct ContentView: View {
    @ObservedObject var controller: ProxyController
    @State private var showSettings = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(white: 0.10), Color(white: 0.06)],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                titleBar
                Divider().opacity(0.15)

                if !controller.auth.loggedIn {
                    SignInView(controller: controller)
                        .transition(.opacity)
                } else if showSettings {
                    SettingsView(controller: controller, showSettings: $showSettings)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                } else {
                    MainView(controller: controller, showSettings: $showSettings)
                        .transition(.opacity)
                }

                Spacer(minLength: 0)
                statusBar
            }
        }
        .preferredColorScheme(.dark)
    }

    private var titleBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "bolt.shield.fill")
                .foregroundStyle(.tint)
            Text("Baleobala Proxy")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.85))
            Spacer()
            if controller.busy {
                ProgressView().controlSize(.small).scaleEffect(0.7)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 10)
    }

    private var statusBar: some View {
        Group {
            if !controller.statusMessage.isEmpty {
                Text(controller.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.55))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(.white.opacity(0.04))
            }
        }
    }
}

// MARK: - Sign In

struct SignInView: View {
    @ObservedObject var controller: ProxyController
    @State private var phone = ""
    @State private var code = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Sign in to Bale")
                    .font(.title3.bold())
                    .foregroundStyle(.white)
                Text("Enter your phone number to receive an SMS verification code.")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.55))
            }

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("Phone number")
                StyledField(placeholder: "989121234567", text: $phone)
                    .disableAutocorrection(true)
            }

            HStack {
                Button {
                    controller.startAuth(phone: phone)
                } label: {
                    Label("Send code", systemImage: "paperplane.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(phone.isEmpty || controller.busy)
            }

            if !controller.transactionHash.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    FieldLabel("SMS code")
                    StyledField(placeholder: "12345", text: $code)
                }
                Button {
                    controller.verifyAuth(code: code)
                } label: {
                    Label("Verify and sign in", systemImage: "checkmark.seal.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(code.isEmpty || controller.busy)
            }

            Spacer()
        }
        .padding(.horizontal, 22)
        .padding(.top, 14)
    }
}

// MARK: - Main

struct MainView: View {
    @ObservedObject var controller: ProxyController
    @Binding var showSettings: Bool

    var body: some View {
        VStack(spacing: 22) {
            connectionDial
            actionButton
            infoCards
        }
        .padding(.horizontal, 22)
        .padding(.top, 8)
        .overlay(alignment: .topTrailing) {
            Button {
                withAnimation { showSettings = true }
            } label: {
                Image(systemName: "gearshape.fill")
                    .foregroundStyle(.white.opacity(0.7))
            }
            .buttonStyle(.plain)
            .padding(.trailing, 16)
            .padding(.top, -36)
        }
    }

    private var connectionDial: some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.08), lineWidth: 14)
                .frame(width: 180, height: 180)
            Circle()
                .trim(from: 0, to: controller.proxy.running ? 1.0 : 0.18)
                .stroke(
                    LinearGradient(colors: dialColors, startPoint: .top, endPoint: .bottom),
                    style: StrokeStyle(lineWidth: 14, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
                .frame(width: 180, height: 180)
                .animation(.easeInOut(duration: 0.4), value: controller.proxy.running)
            VStack(spacing: 4) {
                Image(systemName: controller.proxy.running ? "bolt.shield.fill" : "shield.slash")
                    .font(.system(size: 38, weight: .semibold))
                    .foregroundStyle(controller.proxy.running ? .green : .white.opacity(0.5))
                Text(controller.proxy.running ? "Protected" : "Disconnected")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.85))
                if controller.proxy.running, let started = controller.proxy.startedAt {
                    Text(elapsed(since: started))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.white.opacity(0.5))
                }
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var dialColors: [Color] {
        controller.proxy.running ? [.green, .teal] : [.gray.opacity(0.5), .gray.opacity(0.3)]
    }

    private var actionButton: some View {
        Button {
            controller.proxy.running ? controller.stopProxy() : controller.startProxy()
        } label: {
            Text(controller.proxy.running ? "Disconnect" : "Connect")
                .font(.headline)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(PrimaryButtonStyle(tint: controller.proxy.running ? .red : .accentColor))
        .disabled(controller.busy)
    }

    private var infoCards: some View {
        VStack(spacing: 8) {
            InfoCard(icon: "person.crop.circle.fill",
                     title: "Bale account",
                     value: controller.auth.phone.isEmpty ? "Signed in" : controller.auth.phone,
                     trailing: AnyView(
                        Button("Sign out") { controller.logout() }
                            .buttonStyle(LinkButtonStyle())
                     ))
            InfoCard(icon: "network",
                     title: "Endpoint",
                     value: controller.proxy.endpoint)
            InfoCard(icon: "wifi",
                     title: "Network service",
                     value: controller.proxy.service)
        }
    }

    private func elapsed(since: Date) -> String {
        let interval = Int(Date().timeIntervalSince(since))
        let h = interval / 3600
        let m = (interval % 3600) / 60
        let s = interval % 60
        return String(format: "%02d:%02d:%02d", h, m, s)
    }
}

// MARK: - Settings

struct SettingsView: View {
    @ObservedObject var controller: ProxyController
    @Binding var showSettings: Bool
    @State private var port: String = ""
    @State private var service: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Button {
                    withAnimation { showSettings = false }
                } label: {
                    Image(systemName: "chevron.left")
                    Text("Back")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.white.opacity(0.7))
                Spacer()
            }

            Text("Settings")
                .font(.title3.bold())
                .foregroundStyle(.white)

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("Listen port")
                StyledField(placeholder: "1080", text: $port)
                    .frame(maxWidth: 140)
            }

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("Network service")
                Picker("", selection: $service) {
                    if !controller.settings.availableServices.contains(service) && !service.isEmpty {
                        Text(service).tag(service)
                    }
                    ForEach(controller.settings.availableServices, id: \.self) { svc in
                        Text(svc).tag(svc)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
            }

            Button {
                let p = Int(port) ?? controller.settings.listenPort
                controller.saveSettings(port: p, service: service)
                withAnimation { showSettings = false }
            } label: {
                Label("Save", systemImage: "tray.and.arrow.down.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())

            Spacer()
        }
        .padding(.horizontal, 22)
        .padding(.top, 14)
        .onAppear {
            port = String(controller.settings.listenPort)
            service = controller.settings.service
        }
    }
}

// MARK: - Menu Bar

struct MenuBarView: View {
    @ObservedObject var controller: ProxyController

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle()
                    .fill(controller.proxy.running ? .green : .gray)
                    .frame(width: 8, height: 8)
                Text(controller.proxy.running ? "Connected" : "Disconnected")
                    .font(.headline)
            }
            if controller.proxy.running {
                Text(controller.proxy.endpoint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Divider()
            if controller.auth.loggedIn {
                Button(controller.proxy.running ? "Disconnect" : "Connect") {
                    controller.proxy.running ? controller.stopProxy() : controller.startProxy()
                }
            } else {
                Text("Sign in to start")
                    .foregroundStyle(.secondary)
            }
            Button("Open Window") {
                NSApp.activate(ignoringOtherApps: true)
                if let win = NSApp.windows.first(where: { $0.title.contains("Baleobala") }) {
                    win.makeKeyAndOrderFront(nil)
                }
            }
            Divider()
            Button("Quit") { NSApp.terminate(nil) }
                .keyboardShortcut("q")
        }
        .padding(12)
        .frame(width: 220)
    }
}

// MARK: - Reusable bits

struct FieldLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .tracking(0.6)
            .foregroundStyle(.white.opacity(0.45))
    }
}

struct StyledField: View {
    let placeholder: String
    @Binding var text: String

    var body: some View {
        TextField(placeholder, text: $text)
            .textFieldStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(Color.white.opacity(0.06))
            .cornerRadius(8)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)
            )
            .foregroundStyle(.white)
    }
}

struct InfoCard: View {
    let icon: String
    let title: String
    let value: String
    var trailing: AnyView? = nil

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .frame(width: 22)
                .foregroundStyle(.white.opacity(0.5))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.5))
                Text(value)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            if let trailing { trailing }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color.white.opacity(0.04))
        .cornerRadius(10)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var tint: Color = .accentColor

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.vertical, 10)
            .padding(.horizontal, 16)
            .background(tint.opacity(configuration.isPressed ? 0.7 : 1.0))
            .foregroundStyle(.white)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}

struct LinkButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.caption.weight(.semibold))
            .foregroundStyle(.tint)
            .opacity(configuration.isPressed ? 0.6 : 1.0)
    }
}
