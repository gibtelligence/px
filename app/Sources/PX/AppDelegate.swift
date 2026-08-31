import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var main: MainWindowController!

    func applicationDidFinishLaunching(_ n: Notification) {
        // Prueba del pegado de imagenes sin pulsar teclas: lee el portapapeles,
        // guarda y dice donde. `PX --test-paste`
        if CommandLine.arguments.contains("--test-paste") {
            let pb = NSPasteboard.general
            let tipos = (pb.types ?? []).map { $0.rawValue }.joined(separator: ", ")
            FileHandle.standardError.write("tipos en el portapapeles: \(tipos)\n".data(using: .utf8)!)
            if let png = PXTerminalView.pngFromPasteboard(pb),
               let path = PXTerminalView.savePNG(png) {
                FileHandle.standardError.write("imagen guardada -> \(path)\n".data(using: .utf8)!)
            } else {
                FileHandle.standardError.write("no habia imagen que guardar\n".data(using: .utf8)!)
            }
            NSApp.terminate(nil)
            return
        }

        main = MainWindowController()
        buildMenu()
        // PX --test-reorder <proyecto>:<indice>   (mismo camino que soltar)
        if let i = CommandLine.arguments.firstIndex(of: "--test-reorder"),
           i + 1 < CommandLine.arguments.count {
            let parts = CommandLine.arguments[i + 1].split(separator: ":")
            if parts.count == 2, let idx = Int(parts[1]) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                    self?.main.testReorder(String(parts[0]), to: idx)
                }
            }
        }
        main.window.makeKeyAndOrderFront(nil)
        openFromCommandLine()
        NSApp.activate(ignoringOtherApps: true)
        Snapshot.armIfRequested(main.window)
    }

    /// `PX --open <proyecto>/<agente>` abre esa pestana al arrancar.
    private func openFromCommandLine() {
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: "--open"), i + 1 < a.count else { return }
        let specs = a[i + 1].split(separator: ",").map(String.init)
        // el modelo se carga en segundo plano: esperamos a tenerlo
        for (n, spec) in specs.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2 + Double(n) * 1.5) { [weak self] in
                self?.main.open(spec: spec)
            }
        }
    }

    private func buildMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Salir de PX", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // Menu Edicion: sin el, Cmd+V/Cmd+C no llegan al responder chain y el
        // terminal no recibe ni el pegado de texto.
        let editItem = NSMenuItem()
        bar.addItem(editItem)
        let editMenu = NSMenu(title: "Edición")
        editMenu.addItem(withTitle: "Copiar", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Pegar", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Seleccionar todo", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        let tabItem = NSMenuItem()
        bar.addItem(tabItem)
        let tabMenu = NSMenu(title: "Pestañas")
        tabMenu.addItem(withTitle: "Siguiente", action: #selector(next), keyEquivalent: "]")
        tabMenu.addItem(withTitle: "Anterior", action: #selector(prev), keyEquivalent: "[")
        for i in 1...9 {
            let it = NSMenuItem(title: "Ir a \(i)", action: #selector(goTo(_:)), keyEquivalent: "\(i)")
            it.tag = i - 1
            tabMenu.addItem(it)
        }
        tabItem.submenu = tabMenu
        NSApp.mainMenu = bar
    }

    @objc private func next() { main.nextTab() }
    @objc private func prev() { main.prevTab() }
    @objc private func goTo(_ s: NSMenuItem) { main.selectTab(index: s.tag) }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
}
