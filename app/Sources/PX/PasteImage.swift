import AppKit
import SwiftTerm

/// Terminal con pegado de imagenes.
///
/// WHY: el `paste()` de SwiftTerm lee SOLO `string(forType: .string)`, asi que
/// una captura en el portapapeles se pierde en silencio. Un terminal no puede
/// transportar binario, pero Claude Code sabe leer un fichero por su ruta: al
/// pegar una imagen la guardamos en disco y escribimos la ruta en el prompt.
///
/// Vale igual para ficheros copiados en el Finder (se pega su ruta, sin copiar
/// nada) y no estorba al pegado de texto de siempre.
final class PXTerminalView: LocalProcessTerminalView {

    /// Donde corre el claude de esta pestana. Si es remoto, la imagen hay que
    /// subirla: guardada solo aqui, el agente de alla no podria leerla.
    var host: Host = .local

    static let pasteDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".local/state/px/pastes")

    override func paste(_ sender: Any) {
        let pb = NSPasteboard.general

        // 1. Ficheros del Finder -> su ruta. OJO: solo file URLs; una URL http
        //    copiada como texto tambien se lee como NSURL y romperia el pegado
        //    normal.
        if let urls = pb.readObjects(forClasses: [NSURL.self], options: nil) as? [URL] {
            let files = urls.filter { $0.isFileURL }
            if !files.isEmpty {
                insertPaths(files.map { $0.path })
                return
            }
        }

        // 2. Imagen en bruto (captura de pantalla, copiar desde Vista Previa…)
        if let png = PXTerminalView.pngFromPasteboard(pb),
           let path = PXTerminalView.savePNG(png) {
            insertPaths([remotePath(for: path) ?? path])
            return
        }

        super.paste(sender)
    }

    /// Carpeta que ven LAS DOS maquinas. El NAS esta montado en la misma ruta
    /// en ambas, asi que una imagen escrita aqui es legible por el agente sin
    /// copiarla por la red.
    static let sharedDir = URL(fileURLWithPath: "/Volumes/PERSONAL/.px-pastes")

    /// Ruta que el agente REMOTO puede leer.
    ///
    /// WHY: Claude Code lee el portapapeles de SU maquina. Con PX en el MacBook
    /// y el agente en el Studio, pegar leia el portapapeles del Studio — que no
    /// es el tuyo. Hay que hacer llegar el fichero al otro lado.
    /// Primera opcion el NAS (montado en ambas, sin red de por medio); si no
    /// esta, scp.
    private func remotePath(for local: String) -> String? {
        guard case .ssh(let h) = host else { return nil }

        if FileManager.default.fileExists(atPath: "/Volumes/PERSONAL") {
            let fm = FileManager.default
            try? fm.createDirectory(at: PXTerminalView.sharedDir, withIntermediateDirectories: true)
            let dst = PXTerminalView.sharedDir
                .appendingPathComponent((local as NSString).lastPathComponent)
            try? fm.removeItem(at: dst)
            if (try? fm.copyItem(at: URL(fileURLWithPath: local), to: dst)) != nil {
                return dst.path
            }
        }
        let name = (local as NSString).lastPathComponent
        let rel = ".local/state/px/pastes"
        let mk = Process()
        mk.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        mk.arguments = [h, "mkdir -p \"$HOME/\(rel)\""]
        let cp = Process()
        cp.executableURL = URL(fileURLWithPath: "/usr/bin/scp")
        cp.arguments = ["-q", local, "\(h):\(rel)/"]      // relativo = home remoto
        do {
            try mk.run(); mk.waitUntilExit()
            try cp.run(); cp.waitUntilExit()
            guard cp.terminationStatus == 0 else { return nil }
        } catch { return nil }
        // ruta absoluta en el host remoto, resuelta alli
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        p.arguments = [h, "echo $HOME/\(rel)/\(name)"]
        let pipe = Pipe(); p.standardOutput = pipe
        guard (try? p.run()) != nil else { return nil }
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)
        p.waitUntilExit()
        return out?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func insertPaths(_ paths: [String]) {
        let txt = paths.map { $0.contains(" ") ? "'\($0)'" : $0 }.joined(separator: " ") + " "
        insertText(txt, replacementRange: NSRange(location: 0, length: 0))
    }

    /// PNG del portapapeles, convirtiendo desde TIFF si hace falta (las
    /// capturas de macOS llegan como TIFF).
    static func pngFromPasteboard(_ pb: NSPasteboard) -> Data? {
        if let d = pb.data(forType: .png) { return d }
        guard let tiff = pb.data(forType: .tiff),
              let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .png, properties: [:])
    }

    static func savePNG(_ data: Data) -> String? {
        let fm = FileManager.default
        try? fm.createDirectory(at: pasteDir, withIntermediateDirectories: true)
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        var url = pasteDir.appendingPathComponent("px-\(f.string(from: Date())).png")
        var n = 2
        while fm.fileExists(atPath: url.path) {
            url = pasteDir.appendingPathComponent("px-\(f.string(from: Date()))-\(n).png")
            n += 1
        }
        guard (try? data.write(to: url)) != nil else { return nil }
        prune()
        return url.path
    }

    /// Deja solo las 50 pegadas mas recientes: esto es un buzon, no un archivo.
    /// Limpia los dos sitios (local y NAS) y se lleva tambien los sidecars
    /// `._*` que el NAS crea junto a cada fichero.
    static func prune(keep: Int = 50) {
        for dir in [pasteDir, sharedDir] {
            pruneDir(dir, keep: keep)
        }
    }

    private static func pruneDir(_ dir: URL, keep: Int) {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        // sidecars del NAS: fuera siempre, no cuentan como pegadas
        for it in items where it.lastPathComponent.hasPrefix("._") {
            try? fm.removeItem(at: it)
        }
        let sorted = items.filter { !$0.lastPathComponent.hasPrefix("._") }.sorted {
            let a = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            let b = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            return a > b
        }
        for old in sorted.dropFirst(keep) { try? fm.removeItem(at: old) }
    }
}
