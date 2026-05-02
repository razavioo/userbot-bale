import SwiftUI
import AppKit

@main
struct BaleobalaProxyApp: App {
    @StateObject private var controller = ProxyController()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup("Baleobala Proxy") {
            ContentView(controller: controller)
                .frame(width: 360, height: 520)
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
            Image(systemName: controller.proxy.running ? "lock.shield.fill" : "lock.open")
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var controller: ProxyController?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let controller, controller.proxy.running else { return .terminateNow }
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
