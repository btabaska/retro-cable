import RetroCableKit
import SwiftUI

@main
struct RetroCableApp: App {
    @State private var model = AppModel(
        api: APIClient(baseURL: ServerConfig.current()),
        settings: Settings()
    )

    var body: some Scene {
        WindowGroup {
            RootView(model: model)
        }
    }
}
