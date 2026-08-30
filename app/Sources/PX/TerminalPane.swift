import AppKit
import SwiftTerm

/// Una pestana = una sesion de tmux dedicada a un agente.
///
/// WHY sesion por agente (`pxa-<proyecto>-<agente>`) y no ventana dentro de una
/// sesion por proyecto: dos clientes atados a la MISMA sesion de tmux ven lo
/// mismo (tmux espeja la vista). Con una sesion por agente, cada pestana es
/// independiente de verdad, que es lo que espera una GUI con pestanas.
/// El agrupado por proyecto lo pone la app, no tmux.
///
/// `new-session -A` = engancha si existe, y si no la crea corriendo `claude`.
/// Por eso cerrar la pestana NO mata al agente: sigue vivo en tmux.
final class TerminalPane: NSView {
    let agent: Agent
    let project: Project
    private let term: LocalProcessTerminalView
    private(set) var started = false

    static func sessionName(project: Project, agent: Agent) -> String {
        let clean = { (s: String) in s.replacingOccurrences(of: ".", with: "_")
                                      .replacingOccurrences(of: ":", with: "_") }
        return "pxa-\(clean(project.name))-\(clean(agent.name))"
    }

    init(project: Project, agent: Agent, host: Host) {
        self.project = project
        self.agent = agent
        self.term = LocalProcessTerminalView(frame: .zero)
        super.init(frame: .zero)

        term.translatesAutoresizingMaskIntoConstraints = false
        addSubview(term)
        NSLayoutConstraint.activate([
            term.leadingAnchor.constraint(equalTo: leadingAnchor),
            term.trailingAnchor.constraint(equalTo: trailingAnchor),
            term.topAnchor.constraint(equalTo: topAnchor),
            term.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
        applyTheme()
        start(host: host)
    }

    required init?(coder: NSCoder) { fatalError() }

    private func applyTheme() {
        term.nativeBackgroundColor = Theme.terminalBG
        term.nativeForegroundColor = Theme.terminalFG
        term.caretColor = Theme.violet
    }

    private func start(host: Host) {
        guard !started else { return }
        started = true
        let session = TerminalPane.sessionName(project: project, agent: agent)
        // -A: attach-or-create. El comando solo corre al CREARLA.
        let local = (host == .local)
        let tmux = local ? Host.binary("tmux") : "tmux"
        let claude = local ? Host.binary("claude") : "claude"
        let tmuxCmd = "\(tmux) new-session -A -s \(session) -c \(shq(agent.path)) \(claude)"
        let (exe, args) = host.command([tmuxCmd])
        var env = Terminal.getEnvironmentVariables(termName: "xterm-256color")
        env.append("PX_AGENT=\(agent.name)")
        env.append("PX_PROJECT=\(project.name)")
        term.startProcess(executable: exe, args: args, environment: env)
    }

    private func shq(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    func focusTerminal() { window?.makeFirstResponder(term) }
}
