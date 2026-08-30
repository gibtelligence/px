import Foundation

/// Donde vive el tmux que envolvemos: en esta maquina, o en otra por ssh.
///
/// La app se usa en las dos maquinas: en el Studio el tmux es local; desde el
/// MacBook, el trabajo sigue viviendo en el Studio, asi que la pestana ejecuta
/// `ssh -t studio tmux attach`. Misma UI, mismo estado, misma sesion.
enum Host: Equatable {
    case local
    case ssh(String)

    static var current: Host {
        // --host studio | --host local  manda sobre todo lo demas
        let a = CommandLine.arguments
        if let i = a.firstIndex(of: "--host"), i + 1 < a.count {
            return a[i + 1] == "local" ? .local : .ssh(a[i + 1])
        }
        if let h = ProcessInfo.processInfo.environment["PX_HOST"], !h.isEmpty { return .ssh(h) }

        // Si el `px` de esta maquina es el REENVIADOR (px-remote), entonces esta
        // maquina es un cliente y el trabajo vive en el Studio: el modelo y las
        // terminales tienen que apuntar al MISMO sitio, o veriamos el estado de
        // alli con sesiones de aqui.
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let link = home + "/.local/bin/px"
        if let dest = try? FileManager.default.destinationOfSymbolicLink(atPath: link),
           dest.hasSuffix("px-remote") {
            return .ssh("studio")
        }
        return FileManager.default.isExecutableFile(atPath: "/opt/homebrew/bin/tmux")
            ? .local : .ssh("studio")
    }

    /// Ruta del binario `px`.
    ///
    /// WHY explicita: la app no arranca desde un shell interactivo, asi que un
    /// `sh -lc "px …"` NO lee ~/.zshrc y no encuentra ~/.local/bin/px (era el
    /// "px no responde" de la primera captura). Igual para tmux.
    static func binary(_ name: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for c in ["\(home)/.local/bin/\(name)", "/opt/homebrew/bin/\(name)",
                  "/usr/local/bin/\(name)"] where FileManager.default.isExecutableFile(atPath: c) {
            return c
        }
        return name
    }

    /// Ejecutable + argumentos para correr un comando en el host.
    func command(_ argv: [String], tty: Bool = true) -> (String, [String]) {
        let line = argv.joined(separator: " ")
        switch self {
        case .local:
            // PATH explicito: homebrew + ~/.local/bin, que es donde viven px,
            // tmux y claude, y que un shell no interactivo no trae.
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            let path = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
            return ("/bin/sh", ["-c", "export PATH=\(path); \(line)"])
        case .ssh(let host):
            // Mismo problema al otro lado: `ssh host "cmd"` tampoco lee .zshrc.
            // $HOME lo expande el shell REMOTO, no este.
            let remote = "export PATH=$HOME/.local/bin:/opt/homebrew/bin:$PATH; " + line
            return ("/usr/bin/ssh", (tty ? ["-t"] : []) + [host, remote])
        }
    }

    var label: String {
        switch self {
        case .local: return "local"
        case .ssh(let h): return h
        }
    }
}

enum AgentState: String, Decodable {
    case work, wait, idle, off, closed

    var glyph: String {
        switch self {
        case .work: return "●"
        case .wait: return "◐"
        case .idle: return "○"
        case .off:  return "·"
        case .closed: return "·"
        }
    }

    var label: String {
        switch self {
        case .work: return "trabajando"
        case .wait: return "espera input"
        case .idle: return "listo"
        case .off:  return "sin agente"
        case .closed: return "cerrado"
        }
    }
}

struct Agent: Decodable {
    let name: String
    let relpath: String
    let path: String
    let brief: Bool
    let open: Bool
    let state: AgentState
    let age: Double?
}

struct Project: Decodable {
    let name: String
    let path: String
    let session: String
    let agents: [Agent]
}

struct Model: Decodable {
    let root: String
    let session_prefix: String
    let projects: [Project]

    /// Pide el modelo a `px json` (el cerebro sigue siendo python: la app no
    /// reimplementa descubrimiento ni estados).
    static func load(host: Host = Host.current) -> Model? {
        let px = (host == .local) ? Host.binary("px") : "px"
        let (exe, args) = host.command([px, "json"], tty: false)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return try? JSONDecoder().decode(Model.self, from: data)
    }
}
