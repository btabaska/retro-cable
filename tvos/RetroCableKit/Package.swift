// swift-tools-version: 6.0
// RetroCableKit: everything in the Apple TV app that is not a view. It builds for macOS too, so
// `swift test` (make test-tvos) runs its unit tests on any Mac without a tvOS simulator.
import PackageDescription

let package = Package(
    name: "RetroCableKit",
    platforms: [.tvOS(.v18), .macOS(.v14)],
    products: [
        .library(name: "RetroCableKit", targets: ["RetroCableKit"]),
    ],
    targets: [
        .target(name: "RetroCableKit"),
        .testTarget(
            name: "RetroCableKitTests",
            dependencies: ["RetroCableKit"],
            exclude: ["Fixtures"]
        ),
    ],
    swiftLanguageModes: [.v6]
)
