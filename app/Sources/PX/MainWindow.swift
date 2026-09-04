import AppKit

/// Ventana principal: barra lateral (proyectos = grupos) + pestanas + terminal.
final class MainWindowController: NSObject {
    let window: NSWindow
    private let host = Host.current
    private var model: Model?

    private let sidebar = NSStackView()
    private let sidebarScroll = NSScrollView()
    private let tabBar = NSStackView()
    private let content = NSView()
    private let statusLabel = NSTextField(labelWithString: "")
    private let titleLabel = NSTextField(labelWithString: "PX")
    private let hiddenLabel = NSTextField(labelWithString: "")
    private let dropView = SidebarDropView()

    private struct Tab {
        let project: Project
        let agent: Agent
        let pane: TerminalPane
        let button: TabButton
    }
    private var tabs: [Tab] = []
    /// Proyectos plegados. Se recuerda entre arranques: plegar y que al volver
    /// este otra vez todo abierto seria inutil.
    private var collapsed: Set<String> = Set(
        UserDefaults.standard.stringArray(forKey: "px.collapsedProjects") ?? [])
    private var activeIndex: Int? = nil
    private var refreshTimer: Timer?

    override init() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        super.init()
        window.title = "PX"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = Theme.ink
        window.appearance = NSAppearance(named: .darkAqua)
        window.center()
        buildLayout()
        reload()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.reload()
        }
    }

    // MARK: - layout

    private func buildLayout() {
        let root = NSView()
        root.wantsLayer = true
        root.layer?.backgroundColor = Theme.ink.cgColor
        window.contentView = root

        // --- barra lateral -------------------------------------------------
        let side = NSView()
        side.wantsLayer = true
        side.layer?.backgroundColor = Theme.sidebarBG.cgColor
        side.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(side)

        let title = titleLabel
        title.font = .systemFont(ofSize: 15, weight: .bold)
        title.textColor = Theme.violet
        title.translatesAutoresizingMaskIntoConstraints = false
        side.addSubview(title)

        statusLabel.font = .systemFont(ofSize: 10)
        statusLabel.textColor = Theme.dim
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        side.addSubview(statusLabel)

        sidebar.orientation = .vertical
        sidebar.alignment = .leading
        sidebar.spacing = 1
        sidebar.edgeInsets = NSEdgeInsets(top: 6, left: 0, bottom: 12, right: 0)
        sidebar.translatesAutoresizingMaskIntoConstraints = false

        sidebarScroll.drawsBackground = false
        sidebarScroll.hasVerticalScroller = true
        sidebarScroll.translatesAutoresizingMaskIntoConstraints = false
        let flipped = dropView
        flipped.translatesAutoresizingMaskIntoConstraints = false
        flipped.addSubview(sidebar)
        sidebarScroll.documentView = flipped
        side.addSubview(sidebarScroll)

        // --- barra de pestanas ---------------------------------------------
        let tabStrip = NSView()
        tabStrip.wantsLayer = true
        tabStrip.layer?.backgroundColor = Theme.tabbarBG.cgColor
        tabStrip.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(tabStrip)

        tabBar.orientation = .horizontal
        tabBar.alignment = .centerY
        tabBar.spacing = 4
        tabBar.edgeInsets = NSEdgeInsets(top: 0, left: 10, bottom: 0, right: 10)
        tabBar.translatesAutoresizingMaskIntoConstraints = false
        tabStrip.addSubview(tabBar)

        // pestanas que existen pero el entorno esconde: se dice, no se ocultan
        // en silencio
        hiddenLabel.font = .systemFont(ofSize: 10)
        hiddenLabel.textColor = Theme.dim
        hiddenLabel.translatesAutoresizingMaskIntoConstraints = false
        tabStrip.addSubview(hiddenLabel)

        content.wantsLayer = true
        content.layer?.backgroundColor = Theme.terminalBG.cgColor
        content.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(content)

        let SIDE: CGFloat = 236, TAB: CGFloat = 38
        NSLayoutConstraint.activate([
            side.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            side.topAnchor.constraint(equalTo: root.topAnchor),
            side.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            side.widthAnchor.constraint(equalToConstant: SIDE),

            title.leadingAnchor.constraint(equalTo: side.leadingAnchor, constant: 16),
            title.topAnchor.constraint(equalTo: side.topAnchor, constant: 30),
            statusLabel.leadingAnchor.constraint(equalTo: side.leadingAnchor, constant: 16),
            statusLabel.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),

            sidebarScroll.leadingAnchor.constraint(equalTo: side.leadingAnchor),
            sidebarScroll.trailingAnchor.constraint(equalTo: side.trailingAnchor),
            sidebarScroll.topAnchor.constraint(equalTo: statusLabel.bottomAnchor, constant: 12),
            sidebarScroll.bottomAnchor.constraint(equalTo: side.bottomAnchor),
            sidebar.leadingAnchor.constraint(equalTo: flipped.leadingAnchor),
            sidebar.trailingAnchor.constraint(equalTo: flipped.trailingAnchor),
            sidebar.topAnchor.constraint(equalTo: flipped.topAnchor),
            flipped.widthAnchor.constraint(equalTo: sidebarScroll.widthAnchor),
            flipped.bottomAnchor.constraint(greaterThanOrEqualTo: sidebar.bottomAnchor),

            tabStrip.leadingAnchor.constraint(equalTo: side.trailingAnchor),
            tabStrip.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            tabStrip.topAnchor.constraint(equalTo: root.topAnchor, constant: 28),
            tabStrip.heightAnchor.constraint(equalToConstant: TAB),
            tabBar.leadingAnchor.constraint(equalTo: tabStrip.leadingAnchor),
            tabBar.trailingAnchor.constraint(lessThanOrEqualTo: tabStrip.trailingAnchor),
            tabBar.centerYAnchor.constraint(equalTo: tabStrip.centerYAnchor),
            hiddenLabel.trailingAnchor.constraint(equalTo: tabStrip.trailingAnchor, constant: -14),
            hiddenLabel.centerYAnchor.constraint(equalTo: tabStrip.centerYAnchor),

            content.leadingAnchor.constraint(equalTo: side.trailingAnchor),
            content.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            content.topAnchor.constraint(equalTo: tabStrip.bottomAnchor),
            content.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
    }

    // MARK: - modelo

    func reload() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let m = Model.load(host: self?.host ?? .local)
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.model = m
                self.rebuildSidebar()
                self.refreshTabBar()
                self.statusLabel.stringValue = m.map {
                    let con = $0.projects.filter { !$0.agents.isEmpty }.count
                    return "v\(PX_VERSION) · \(con) proyectos · tmux en \(self.host.label)"
                } ?? "v\(PX_VERSION) · px no responde en \(self.host.label)"
            }
        }
    }

    private func rebuildSidebar() {
        sidebar.arrangedSubviews.forEach { $0.removeFromSuperview() }
        dropView.headers = []
        dropView.onReorder = { [weak self] name, idx in self?.reorder(name, to: idx) }
        guard let m = model else { return }

        // conmutador de entorno arriba del todo
        if let wss = m.workspaces, !wss.isEmpty {
            let bar = WorkspaceBar(workspaces: wss, current: m.workspace ?? "all") { [weak self] name in
                guard let self = self else { return }
                DispatchQueue.global(qos: .userInitiated).async {
                    Model.setWorkspace(name, host: self.host)
                    DispatchQueue.main.async { self.reload() }
                }
            }
            sidebar.addArrangedSubview(bar)
            bar.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true
            // el color del entorno tine el titulo: saber donde estas de un vistazo
            let cur = wss.first { $0.name == m.workspace }
            titleLabel.textColor = cur?.nsColor ?? Theme.violet
        }

        // lo que queda FUERA del entorno: filtrar no puede ser perder la senal
        if let o = m.others, !o.isEmpty {
            let txt = o.map { "\(AgentState(rawValue: $0.key)?.glyph ?? "·") \($0.value)" }
                       .sorted().joined(separator: "  ")
            let warn = NSTextField(labelWithString: "fuera del entorno:  \(txt)")
            warn.font = .systemFont(ofSize: 9)
            warn.textColor = Theme.amber
            let box = NSView()
            warn.translatesAutoresizingMaskIntoConstraints = false
            box.addSubview(warn)
            NSLayoutConstraint.activate([
                box.heightAnchor.constraint(equalToConstant: 22),
                warn.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 14),
                warn.centerYAnchor.constraint(equalTo: box.centerYAnchor),
            ])
            sidebar.addArrangedSubview(box)
            box.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true
        }

        for p in m.projects where !p.agents.isEmpty {
            let isCollapsed = collapsed.contains(p.name)
            let header = GroupHeader(project: p, collapsed: isCollapsed) { [weak self] in
                self?.toggle(project: p.name)
            }
            sidebar.addArrangedSubview(header)
            header.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true
            dropView.headers.append((name: p.name, view: header))
            if isCollapsed { continue }
            for a in p.agents {
                let row = AgentRow(project: p, agent: a, isOpen: isOpen(p, a)) { [weak self] in
                    self?.openAgent(project: p, agent: a)
                }
                sidebar.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true
            }
        }
    }

    /// Las pestanas tambien obedecen al entorno.
    ///
    /// Ocultar NO es cerrar: el pane y su sesion de tmux siguen vivos, y al
    /// volver al entorno la pestana reaparece donde estaba. Si la activa queda
    /// fuera, se pasa a la primera visible.
    private func refreshTabBar() {
        guard let m = model else { return }
        let visibles = Set(m.projects.map { $0.name })
        tabBar.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for t in tabs where visibles.contains(t.project.name) {
            // Huerfana solo si el PROYECTO esta en el modelo y el agente no:
            // `px json` filtra por entorno, asi que un proyecto ausente puede
            // estar simplemente en el otro entorno — eso no es orfandad.
            if let pj = m.projects.first(where: { $0.name == t.project.name }) {
                t.button.setOrphan(!pj.agents.contains { $0.name == t.agent.name })
            }
            tabBar.addArrangedSubview(t.button)
        }
        let ocultas = tabs.filter { !visibles.contains($0.project.name) }.count
        hiddenLabel.stringValue = ocultas > 0
            ? "\(ocultas) en otro entorno" : ""

        if let i = activeIndex, !visibles.contains(tabs[i].project.name) {
            if let j = tabs.firstIndex(where: { visibles.contains($0.project.name) }) {
                select(j)
            } else {
                content.subviews.forEach { $0.removeFromSuperview() }
                activeIndex = nil
                window.title = "PX"
                tabs.forEach { $0.button.setActive(false) }
            }
        }
    }

    /// Gancho de prueba: mismo camino que soltar, sin raton.
    func testReorder(_ name: String, to idx: Int) { reorder(name, to: idx) }

    /// Reordena arrastrando. Se manda solo lo visible: px lo fusiona con el
    /// orden global y respeta el de los proyectos del otro entorno.
    private func reorder(_ name: String, to idx: Int) {
        guard let m = model else { return }
        var names = m.projects.filter { !$0.agents.isEmpty }.map { $0.name }
        guard let from = names.firstIndex(of: name) else { return }
        names.remove(at: from)
        let to = min(max(idx > from ? idx - 1 : idx, 0), names.count)
        names.insert(name, at: to)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            Model.order(names, host: self.host)
            DispatchQueue.main.async { self.reload() }
        }
    }

    private func toggle(project: String) {
        if collapsed.contains(project) { collapsed.remove(project) }
        else { collapsed.insert(project) }
        UserDefaults.standard.set(Array(collapsed), forKey: "px.collapsedProjects")
        rebuildSidebar()
    }

    /// Al abrir un agente, su proyecto se despliega: si no, la pestana nueva
    /// aparece sin que se vea de donde sale.
    private func expand(project: String) {
        guard collapsed.remove(project) != nil else { return }
        UserDefaults.standard.set(Array(collapsed), forKey: "px.collapsedProjects")
    }

    private func isOpen(_ p: Project, _ a: Agent) -> Bool {
        tabs.contains { $0.project.name == p.name && $0.agent.name == a.name }
    }

    // MARK: - pestanas

    func openAgent(project: Project, agent: Agent) {
        if let i = tabs.firstIndex(where: { $0.project.name == project.name && $0.agent.name == agent.name }) {
            select(i); return
        }
        let pane = TerminalPane(project: project, agent: agent, host: host)
        pane.translatesAutoresizingMaskIntoConstraints = false
        let button = TabButton(project: project, agent: agent)
        let tab = Tab(project: project, agent: agent, pane: pane, button: button)
        button.onSelect = { [weak self] in
            guard let self = self,
                  let i = self.tabs.firstIndex(where: { $0.button === button }) else { return }
            self.select(i)
        }
        button.onClose = { [weak self] in
            guard let self = self,
                  let i = self.tabs.firstIndex(where: { $0.button === button }) else { return }
            self.closeTab(i)
        }
        tabs.append(tab)
        expand(project: project.name)
        tabBar.addArrangedSubview(button)
        select(tabs.count - 1)
        rebuildSidebar()
        refreshTabBar()
    }

    private func select(_ i: Int) {
        guard tabs.indices.contains(i) else { return }
        content.subviews.forEach { $0.removeFromSuperview() }
        let tab = tabs[i]
        content.addSubview(tab.pane)
        NSLayoutConstraint.activate([
            tab.pane.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            tab.pane.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            tab.pane.topAnchor.constraint(equalTo: content.topAnchor),
            tab.pane.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
        activeIndex = i
        for (j, t) in tabs.enumerated() { t.button.setActive(j == i) }
        window.title = "PX — \(tab.project.name) / \(tab.agent.name)"
        tab.pane.focusTerminal()
    }

    /// Cierra la PESTANA, no el agente: la sesion de tmux sigue viva.
    private func closeTab(_ i: Int) {
        guard tabs.indices.contains(i) else { return }
        let t = tabs.remove(at: i)
        t.button.removeFromSuperview()
        t.pane.removeFromSuperview()
        if tabs.isEmpty { activeIndex = nil; window.title = "PX" }
        else { select(min(i, tabs.count - 1)) }
        rebuildSidebar()
    }

    /// Abre por especificacion textual: "proyecto/agente" o solo "agente".
    func open(spec: String) {
        guard let m = model else { return }
        let parts = spec.split(separator: "/", maxSplits: 1).map(String.init)
        for p in m.projects {
            if parts.count == 2 {
                if p.name == parts[0], let a = p.agents.first(where: { $0.name == parts[1] }) {
                    openAgent(project: p, agent: a); return
                }
            } else if let a = p.agents.first(where: { $0.name == parts[0] }) {
                openAgent(project: p, agent: a); return
            }
        }
    }

    func selectTab(index: Int) { select(index) }
    func nextTab() { if let i = activeIndex { select((i + 1) % max(tabs.count, 1)) } }
    func prevTab() { if let i = activeIndex { select((i - 1 + tabs.count) % max(tabs.count, 1)) } }
}


