import SwiftUI

@main
struct BaleobalaApp: App {
    @StateObject private var controller = BaleAppController()

    var body: some Scene {
        WindowGroup {
            ContentView(controller: controller)
        }
    }
}
