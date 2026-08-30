import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var main: MainWindowController!

    func applicationDidFinishLaunching(_ n: Notification) {
        main = MainWindowController()
        buildMenu()
        main.window.makeKeyAndOrderFront(nil)
        openFromCommandLine()
        NSApp.activate(ignoringOtherApps: true)
        Snapshot.armIfRequested(main.window)
    }

    /// `PX --open <proyecto>/<agente>` abre esa pestana al arrancar.
    private func openFromCommandLine() {
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: "--open"), i + 1 < a.count else { return }
        let spec = a[i + 1]
        // el modelo se carga en segundo plano: esperamos a tenerlo
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            self?.main.open(spec: spec)
        }
    }

    private func buildMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Salir de PX", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

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
