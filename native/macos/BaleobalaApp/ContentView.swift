import SwiftUI

struct ContentView: View {
    @ObservedObject var controller: BaleAppController

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("baleobala")
                .font(.largeTitle.weight(.bold))
            Text(controller.statusText)
                .font(.headline)
            Text(controller.profileText)
                .foregroundStyle(.secondary)
            HStack {
                Button("Load") {
                    controller.loadState()
                }
                Button("Install") {
                    controller.install(profile: BaleTunnelConfiguration(displayName: "baleobala"))
                }
                Button("Connect") {
                    controller.connect()
                }
                Button("Disconnect") {
                    controller.disconnect()
                }
            }
        }
        .padding(24)
        .frame(minWidth: 520, minHeight: 220)
        .onAppear {
            controller.loadState()
        }
    }
}
