import SwiftUI

@main
struct UserbotBaleApp: App {
    @StateObject private var controller = BaleAppController()

    var body: some Scene {
        WindowGroup {
            ContentView(controller: controller)
        }
        .windowResizability(.contentMinSize)

        MenuBarExtra("UserbotBale", systemImage: controller.isTunnelRunning ? "lock.shield.fill" : "lock.open") {
            MenuBarStatusView(controller: controller)
        }
    }
}
