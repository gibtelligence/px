import AppKit

/// Autorretrato de la ventana.
///
/// WHY: se desarrolla por ssh contra el Studio, donde `screencapture` esta
/// bloqueado por TCC ("Could not switch to audit session") y los AppleEvents
/// caducan. Pero un proceso SI puede rasterizar sus propias vistas sin ningun
/// permiso del sistema: eso permite ver la GUI mientras se construye.
///
///   PX --snapshot /tmp/px.png [--snapshot-delay 2.5]
enum Snapshot {
    static func requestedPath() -> String? {
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: "--snapshot"), i + 1 < a.count else { return nil }
        return a[i + 1]
    }

    static func delay() -> Double {
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: "--snapshot-delay"), i + 1 < a.count,
              let v = Double(a[i + 1]) else { return 2.5 }
        return v
    }

    /// Rasteriza la CAPA, no la vista.
    ///
    /// WHY: `cacheDisplay(in:to:)` sobre una jerarquia layer-backed (el terminal
    /// lo es) devolvia la imagen espejada y con el fondo en blanco — un artefacto
    /// de la captura, no de lo que se ve en pantalla. `CALayer.render(in:)` sobre
    /// un CGContext da lo mismo que pinta el compositor.
    static func capture(_ window: NSWindow, to path: String, flip: Bool = true) {
        guard let view = window.contentView, let layer = view.layer else { return }
        let scale = window.backingScaleFactor
        let w = Int(view.bounds.width * scale), h = Int(view.bounds.height * scale)
        guard w > 0, h > 0,
              let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue)
        else { return }
        if flip {
            ctx.translateBy(x: 0, y: CGFloat(h))
            ctx.scaleBy(x: scale, y: -scale)
        } else {
            ctx.scaleBy(x: scale, y: scale)
        }
        layer.render(in: ctx)
        guard let img = ctx.makeImage() else { return }
        let rep = NSBitmapImageRep(cgImage: img)
        guard let data = rep.representation(using: .png, properties: [:]) else { return }
        try? data.write(to: URL(fileURLWithPath: path))
        FileHandle.standardError.write("snapshot -> \(path)\n".data(using: .utf8)!)
    }

    /// Captura por el COMPOSITOR: la imagen real de la ventana.
    ///
    /// WHY: rasterizar el arbol de capas a mano daba una imagen mixta — el
    /// terminal (layer-backed, con su propia geometria) salia con orientacion
    /// opuesta al resto de la UI. `CGWindowListCreateImage` sobre la propia
    /// ventana devuelve exactamente lo que se ve, sin permisos extra por ser
    /// nuestra.
    static func captureWindowServer(_ window: NSWindow, to path: String) -> Bool {
        let wid = CGWindowID(window.windowNumber)
        guard wid != 0,
              let img = CGWindowListCreateImage(.null, .optionIncludingWindow, wid,
                                                [.boundsIgnoreFraming, .nominalResolution])
        else { return false }
        let rep = NSBitmapImageRep(cgImage: img)
        guard let data = rep.representation(using: .png, properties: [:]) else { return false }
        try? data.write(to: URL(fileURLWithPath: path))
        FileHandle.standardError.write("snapshot(ws) -> \(path) \(img.width)x\(img.height)\n"
                                        .data(using: .utf8)!)
        return true
    }

    /// Captura y cierra, si se pidio por linea de comandos.
    static func armIfRequested(_ window: NSWindow) {
        guard let path = requestedPath() else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay()) {
            if !captureWindowServer(window, to: path) {
                capture(window, to: path, flip: false)   // reserva
            }
            NSApp.terminate(nil)
        }
    }
}
