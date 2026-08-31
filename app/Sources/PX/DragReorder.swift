import AppKit

extension NSPasteboard.PasteboardType {
    static let pxProject = NSPasteboard.PasteboardType("com.gibtelligence.px.project")
}

/// Contenedor de la barra lateral que acepta soltar proyectos para reordenar.
///
/// Dibuja una linea donde caeria el proyecto: soltar a ciegas en una lista es
/// justo lo que hace que reordenar canse.
final class SidebarDropView: NSView {
    override var isFlipped: Bool { true }

    /// Cabeceras de proyecto, en orden de pantalla. Lo rellena el controlador.
    var headers: [(name: String, view: NSView)] = []
    var onReorder: ((_ name: String, _ toIndex: Int) -> Void)?

    private var lineY: CGFloat? = nil

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.pxProject])
    }
    required init?(coder: NSCoder) { fatalError() }

    private func insertionIndex(for point: NSPoint) -> Int {
        // el hueco es "antes de la cabecera cuyo centro queda por debajo"
        for (i, h) in headers.enumerated() {
            let f = h.view.convert(h.view.bounds, to: self)
            if point.y < f.midY { return i }
        }
        return headers.count
    }

    private func y(forIndex i: Int) -> CGFloat {
        guard !headers.isEmpty else { return 0 }
        if i < headers.count {
            let h = headers[i]
            return h.view.convert(h.view.bounds, to: self).minY
        }
        let last = headers[headers.count - 1]
        return last.view.convert(last.view.bounds, to: self).maxY
    }

    override func draggingEntered(_ s: NSDraggingInfo) -> NSDragOperation { update(s) }
    override func draggingUpdated(_ s: NSDraggingInfo) -> NSDragOperation { update(s) }

    private func update(_ s: NSDraggingInfo) -> NSDragOperation {
        let p = convert(s.draggingLocation, from: nil)
        lineY = y(forIndex: insertionIndex(for: p))
        needsDisplay = true
        return .move
    }

    override func draggingExited(_ s: NSDraggingInfo?) { clear() }
    override func draggingEnded(_ s: NSDraggingInfo) { clear() }
    private func clear() { lineY = nil; needsDisplay = true }

    override func performDragOperation(_ s: NSDraggingInfo) -> Bool {
        guard let name = s.draggingPasteboard.string(forType: .pxProject) else { return false }
        let p = convert(s.draggingLocation, from: nil)
        let idx = insertionIndex(for: p)
        clear()
        onReorder?(name, idx)
        return true
    }

    override func draw(_ dirty: NSRect) {
        super.draw(dirty)
        guard let y = lineY else { return }
        Theme.violet.setFill()
        NSRect(x: 8, y: y - 1, width: bounds.width - 16, height: 2).fill()
    }
}
