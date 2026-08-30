// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "PX",
    platforms: [.macOS(.v14)],
    dependencies: [
        // Motor de terminal (emulador xterm en Swift puro, del autor de Gnome Terminal).
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", from: "1.2.0"),
    ],
    targets: [
        .executableTarget(
            name: "PX",
            dependencies: ["SwiftTerm"],
            path: "Sources/PX"
        )
    ]
)
