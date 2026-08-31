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
    private let term: PXTerminalView
    private(set) var started = false

    static func sessionName(project: Project, agent: Agent) -> String {
        let clean = { (s: String) in s.replacingOccurrences(of: ".", with: "_")
                                      .replacingOccurrences(of: ":", with: "_") }
        return "pxa-\(clean(project.name))-\(clean(agent.name))"
    }

    init(project: Project, agent: Agent, host: Host) {
        self.project = project
        self.agent = agent
        self.term = PXTerminalView(frame: .zero)
        super.init(frame: .zero)

        term.translatesAutoresizingMaskIntoConstraints = false
        addSubview(term)
        NSLayoutConstraint.activate([
            term.leadingAnchor.constraint(equalTo: leadingAnchor),
            term.trailingAnchor.constraint(equalTo: trailingAnchor),
            term.topAnchor.constraint(equalTo: topAnchor),
            term.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
        term.host = host
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
        // Delegamos en `px attach`, que hace el attach-or-create Y ADEMAS:
        //  - consume el pin de `px adopt` (si no, una conversacion adoptada se
        //    abre en limpio en vez de con --resume);
        //  - aplica el candado de un solo agente por directorio, incluso contra
        //    sesiones abiertas fuera de px.
        // Se construyo el tmux a pelo aqui una vez y se perdieron las dos cosas
        // sin que nadie se enterara hasta verlo en campo. La logica vive en px.
        let px = (host == .local) ? Host.binary("px") : "px"
        let tmuxCmd = "\(px) attach \(shq(project.name + "/" + agent.name))"
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
