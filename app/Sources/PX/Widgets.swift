import AppKit

/// Cabecera de grupo en la barra lateral = un proyecto.
final class GroupHeader: NSView {
    init(project: Project) {
        super.init(frame: .zero)
        let dot = NSTextField(labelWithString: "▌")
        dot.font = .systemFont(ofSize: 13)
        dot.textColor = Theme.projectColor(project.name)
        let name = NSTextField(labelWithString: project.name.uppercased())
        name.font = .systemFont(ofSize: 10, weight: .semibold)
        name.textColor = Theme.dim
        let count = NSTextField(labelWithString: "\(project.agents.count)")
        count.font = .systemFont(ofSize: 10)
        count.textColor = Theme.line

        for v in [dot, name, count] {
            v.translatesAutoresizingMaskIntoConstraints = false
            addSubview(v)
        }
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 30),
            dot.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 10),
            dot.centerYAnchor.constraint(equalTo: centerYAnchor),
            name.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 4),
            name.centerYAnchor.constraint(equalTo: centerYAnchor),
            count.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            count.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }
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
        color = Theme.projectColor(project.name)
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
