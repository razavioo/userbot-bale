import SwiftUI

@main
struct BaleobalaApp: App {
    @StateObject private var controller = BaleAppController()

    var body: some Scene {
        WindowGroup {
            ContentView(controller: controller)
        }
        .windowResizability(.contentMinSize)

        MenuBarExtra("Baleobala", systemImage: controller.isTunnelRunning ? "lock.shield.fill" : "lock.open") {
            MenuBarStatusView(controller: controller)
        }
    }
}
