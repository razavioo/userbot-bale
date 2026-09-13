import SwiftUI
import AppKit

@main
struct UserbotBaleProxyApp: App {
    @StateObject private var controller = ProxyController()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup("UserbotBale Proxy") {
            ContentView(controller: controller)
                .frame(width: 360, height: 680)
                .onAppear {
                    appDelegate.controller = controller
                    controller.refresh()
                    controller.startPolling()
                }
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)

        MenuBarExtra {
            MenuBarView(controller: controller)
        } label: {
            Image(systemName: menuBarIconName(for: controller.phase))
        }
        .menuBarExtraStyle(.window)
    }

    private func menuBarIconName(for phase: ConnectionPhase) -> String {
        switch phase {
        case .connected:     return "lock.shield.fill"
        case .unhealthy:     return "exclamationmark.shield.fill"
        case .connecting, .disconnecting: return "arrow.triangle.2.circlepath"
        case .disconnected:  return "lock.open"
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var controller: ProxyController?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Tear down on quit if there's *any* live state — running, half-up,
        // or in-flight start/stop. Otherwise the user could quit during
        // "Connecting…" and leave a zombie subprocess + half-applied
        // system proxy behind.
        guard let controller else { return .terminateNow }
        let needsTeardown: Bool = {
            switch controller.phase {
            case .disconnected: return false
            default:            return true
            }
        }()
        guard needsTeardown else { return .terminateNow }
        Task {
            await controller.stopProxyAndWait()
            await MainActor.run { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
