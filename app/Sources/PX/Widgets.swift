import AppKit

/// Cabecera de grupo en la barra lateral = un proyecto. Se pliega al pulsarla.
///
/// Al plegar NO se pierde la senal: la cabecera muestra los glifos de estado de
/// los agentes abiertos, que es justo lo que uno no quiere dejar de ver.
final class GroupHeader: NSView, NSDraggingSource {
    private let onToggle: () -> Void
    let projectName: String
    private var mouseDownAt: NSPoint = .zero
    private var dragging = false

    init(project: Project, collapsed: Bool, onToggle: @escaping () -> Void) {
        self.onToggle = onToggle
        self.projectName = project.name
        super.init(frame: .zero)

        let arrow = NSTextField(labelWithString: collapsed ? "▸" : "▾")
        arrow.font = .systemFont(ofSize: 10)
        arrow.textColor = Theme.fg.withAlphaComponent(0.55)   // tiene que verse: es el mando
        let dot = NSTextField(labelWithString: "▌")
        dot.font = .systemFont(ofSize: 13)
        dot.textColor = project.nsColor
        let name = NSTextField(labelWithString: project.name.uppercased())
        name.font = .systemFont(ofSize: 10, weight: .semibold)
        name.textColor = Theme.dim

        // Plegado: resumen de estados. Desplegado: cuantos agentes hay.
        let abiertos = project.agents.filter { $0.state != .closed }
        let right = NSTextField(labelWithString: "")
        if collapsed && !abiertos.isEmpty {
            let att = NSMutableAttributedString()
            for a in abiertos.prefix(8) {
                att.append(NSAttributedString(
                    string: a.state.glyph + " ",
                    attributes: [.foregroundColor: Theme.stateColor(a.state),
                                 .font: NSFont.systemFont(ofSize: 10)]))
            }
            right.attributedStringValue = att
        } else {
            right.stringValue = "\(project.agents.count)"
            right.font = .systemFont(ofSize: 10)
            right.textColor = Theme.line
        }

        for v in [arrow, dot, name, right] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 30),
            arrow.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 8),
            arrow.centerYAnchor.constraint(equalTo: centerYAnchor),
            dot.leadingAnchor.constraint(equalTo: arrow.trailingAnchor, constant: 2),
            dot.centerYAnchor.constraint(equalTo: centerYAnchor),
            name.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 4),
            name.centerYAnchor.constraint(equalTo: centerYAnchor),
            right.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            right.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    // Pulsar pliega; arrastrar reordena. Se distingue por el umbral de
    // movimiento, y el pliegue se decide al SOLTAR: si no, arrastrar plegaria
    // el grupo de paso.
    override func mouseDown(with event: NSEvent) {
        mouseDownAt = event.locationInWindow
        dragging = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard !dragging else { return }
        let d = hypot(event.locationInWindow.x - mouseDownAt.x,
                      event.locationInWindow.y - mouseDownAt.y)
        guard d > 4 else { return }
        dragging = true

        let item = NSPasteboardItem()
        item.setString(projectName, forType: .pxProject)
        let di = NSDraggingItem(pasteboardWriter: item)
        di.setDraggingFrame(bounds, contents: snapshot())
        beginDraggingSession(with: [di], event: event, source: self)
    }

    override func mouseUp(with event: NSEvent) {
        if !dragging { onToggle() }
        dragging = false
    }

    private func snapshot() -> NSImage {
        let img = NSImage(size: bounds.size)
        if let rep = bitmapImageRepForCachingDisplay(in: bounds) {
            cacheDisplay(in: bounds, to: rep)
            img.addRepresentation(rep)
        }
        return img
    }

    func draggingSession(_ s: NSDraggingSession,
                         sourceOperationMaskFor ctx: NSDraggingContext) -> NSDragOperation {
        .move
    }

    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }
}

/// Fila de agente: glifo de estado + nombre + ruta relativa.
final class AgentRow: NSView {
    private let onClick: () -> Void
    private let bg = CALayer()

    init(project: Project, agent: Agent, isOpen: Bool, onClick: @escaping () -> Void) {
        self.onClick = onClick
        super.init(frame: .zero)
        wantsLayer = true
        bg.backgroundColor = (isOpen ? Theme.violet.withAlphaComponent(0.10) : .clear).cgColor
        layer?.addSublayer(bg)

        let glyph = NSTextField(labelWithString: agent.state.glyph)
        glyph.font = .systemFont(ofSize: 11)
        glyph.textColor = Theme.stateColor(agent.state)
        let name = NSTextField(labelWithString: agent.name)
        name.font = .systemFont(ofSize: 12, weight: isOpen ? .semibold : .regular)
        name.textColor = agent.state == .closed ? Theme.fg.withAlphaComponent(0.75) : Theme.fg
        let sub = NSTextField(labelWithString: agent.state == .closed
                              ? agent.relpath : agent.state.label)
        sub.font = .systemFont(ofSize: 9)
        sub.textColor = Theme.dim
        sub.lineBreakMode = .byTruncatingMiddle
        let brief = NSTextField(labelWithString: agent.brief ? "brief" : "")
        brief.font = .systemFont(ofSize: 8)
        brief.textColor = Theme.violet.withAlphaComponent(0.8)

        for v in [glyph, name, sub, brief] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 34),
            glyph.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14),
            glyph.centerYAnchor.constraint(equalTo: centerYAnchor),
            name.leadingAnchor.constraint(equalTo: glyph.trailingAnchor, constant: 8),
            name.topAnchor.constraint(equalTo: topAnchor, constant: 4),
            sub.leadingAnchor.constraint(equalTo: name.leadingAnchor),
            sub.topAnchor.constraint(equalTo: name.bottomAnchor, constant: 0),
            sub.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -40),
            brief.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            brief.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    override func layout() {
        super.layout()
        bg.frame = bounds
    }
    override func mouseDown(with event: NSEvent) { onClick() }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .pointingHand)
    }
}

/// Pestana: lleva el color de su proyecto, para que el grupo se lea de un vistazo.
final class TabButton: NSView {
    var onSelect: (() -> Void)?
    var onClose: (() -> Void)?
    private let bg = CALayer()
    private let accent = CALayer()
    private let label = NSTextField(labelWithString: "")
    private let closeBtn = NSTextField(labelWithString: "×")
    private let color: NSColor
    private var active = false

    init(project: Project, agent: Agent) {
        color = project.nsColor
        super.init(frame: .zero)
        wantsLayer = true
        layer?.cornerRadius = 6
        bg.cornerRadius = 6
        layer?.addSublayer(bg)
        accent.backgroundColor = color.cgColor
        accent.cornerRadius = 1.5
        layer?.addSublayer(accent)

        label.stringValue = agent.name
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.textColor = Theme.fg
        closeBtn.font = .systemFont(ofSize: 13)
        closeBtn.textColor = Theme.dim
        for v in [label, closeBtn] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        let tip = "\(project.name) / \(agent.name)\n\(agent.path)"
        toolTip = tip
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 28),
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            label.centerYAnchor.constraint(equalTo: centerYAnchor),
            closeBtn.leadingAnchor.constraint(equalTo: label.trailingAnchor, constant: 8),
            closeBtn.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -8),
            closeBtn.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
        setActive(false)
    }
    required init?(coder: NSCoder) { fatalError() }

    func setActive(_ on: Bool) {
        active = on
        bg.backgroundColor = (on ? Theme.terminalBG : NSColor.clear).cgColor
        label.textColor = on ? Theme.fg : Theme.fg.withAlphaComponent(0.6)
        accent.isHidden = !on
        needsLayout = true
    }

    override func layout() {
        super.layout()
        bg.frame = bounds
        accent.frame = CGRect(x: 10, y: bounds.height - 3, width: bounds.width - 20, height: 3)
    }

    override func mouseDown(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        if closeBtn.frame.insetBy(dx: -6, dy: -6).contains(p) { onClose?() } else { onSelect?() }
    }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }
}


/// Conmutador de entorno: separa el trabajo de empresa del personal.
///
/// Es global (lo guarda `px`, no la app), asi que la CLI y la app siempre
/// coinciden en que entorno estas.
final class WorkspaceBar: NSView {
    init(workspaces: [Workspace], current: String, onPick: @escaping (String) -> Void) {
        super.init(frame: .zero)
        let row = NSStackView()
        row.orientation = .horizontal
        row.spacing = 4
        row.translatesAutoresizingMaskIntoConstraints = false
        addSubview(row)

        var items: [(String, NSColor)] = workspaces.map { ($0.name, $0.nsColor) }
        items.append(("all", Theme.dim))
        for (name, color) in items {
            let on = (name == current)
            let pill = PillButton(title: name, color: color, active: on) { onPick(name) }
            row.addArrangedSubview(pill)
        }
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 30),
            row.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            row.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -8),
            row.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }
}

final class PillButton: NSView {
    private let onClick: () -> Void
    private let bg = CALayer()

    init(title: String, color: NSColor, active: Bool, onClick: @escaping () -> Void) {
        self.onClick = onClick
        super.init(frame: .zero)
        wantsLayer = true
        bg.cornerRadius = 5
        bg.backgroundColor = (active ? color.withAlphaComponent(0.22) : NSColor.clear).cgColor
        bg.borderWidth = active ? 1 : 0
        bg.borderColor = color.withAlphaComponent(0.8).cgColor
        layer?.addSublayer(bg)

        let label = NSTextField(labelWithString: title == "all" ? "todos" : title)
        label.font = .systemFont(ofSize: 10, weight: active ? .semibold : .regular)
        label.textColor = active ? color : Theme.dim
        label.translatesAutoresizingMaskIntoConstraints = false
        addSubview(label)
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 20),
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 8),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -8),
            label.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }
    override func layout() { super.layout(); bg.frame = bounds }
    override func mouseDown(with event: NSEvent) { onClick() }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }
}
