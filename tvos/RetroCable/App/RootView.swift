import RetroCableKit
import SwiftUI

/// Switches between the screens. Logic lives in AppModel (RetroCableKit); views only draw it and
/// forward remote presses.
struct RootView: View {
    let model: AppModel

    var body: some View {
        ZStack {
            Palette.header.ignoresSafeArea()
            switch model.screen {
            case .powerOn:
                PowerOnView(model: model)
            case .noSignal:
                NoSignalView()
            case .live:
                LiveTVView(model: model)
            }
        }
        .preferredColorScheme(.dark)
        .tint(Palette.focus)
    }
}
