// swift-tools-version:5.10
// fluidaudio-server: HTTP sidecar exposing FluidAudio's Core ML models
// (Parakeet ASR, pyannote diarization) to the Dalston Python adapters (M104).
//
// Build (release):   swift build -c release
// Run:               .build/release/fluidaudio-server
// Test (no models):  swift test
import PackageDescription

let package = Package(
    name: "fluidaudio-server",
    platforms: [
        // FluidAudio's offline diarizer and Parakeet v3 need macOS 14+;
        // 15 matches dalston-mobile's deployment target.
        // String form: `.v15` needs swift-tools-version 6.0, and 5.10 is kept
        // so the package stays in Swift 5 language mode.
        .macOS("15.0"),
    ],
    dependencies: [
        // Core ML models on the Neural Engine. Pre-1.0, so pinned exactly.
        // Keep in step with dalston-mobile/project.yml.
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.17.7"),
        // HTTP server with built-in multipart decoding. Chosen over
        // Hummingbird because `req.content.decode` handles multipart form
        // uploads without extra packages.
        .package(url: "https://github.com/vapor/vapor.git", from: "4.110.0"),
    ],
    targets: [
        // Everything but `@main`, so the tests can import it.
        .target(
            name: "FluidAudioServerCore",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio"),
                .product(name: "Vapor", package: "vapor"),
            ],
            path: "Sources/FluidAudioServerCore",
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
            ]
        ),
        .executableTarget(
            name: "fluidaudio-server",
            dependencies: [
                .target(name: "FluidAudioServerCore"),
                .product(name: "Vapor", package: "vapor"),
            ],
            path: "Sources/fluidaudio-server",
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
            ]
        ),
        .testTarget(
            name: "FluidAudioServerCoreTests",
            dependencies: [
                .target(name: "FluidAudioServerCore"),
            ],
            path: "Tests/FluidAudioServerCoreTests"
        ),
    ]
)
