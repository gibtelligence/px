import AppKit

/// Paleta de marca (Gibtelligence: violet #7C5CFF sobre ink #0B0B0F).
enum Theme {
    static func hex(_ h: UInt32, _ a: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat((h >> 16) & 0xff) / 255,
                green: CGFloat((h >> 8) & 0xff) / 255,
                blue: CGFloat(h & 0xff) / 255, alpha: a)
    }
    static let violet     = hex(0x7C5CFF)
    static let ink        = hex(0x0B0B0F)
    static let sidebarBG  = hex(0x121218)
    static let tabbarBG   = hex(0x16161D)
    static let terminalBG = hex(0x0B0B0F)
    static let terminalFG = hex(0xC9C9D2)
    static let fg         = hex(0xC9C9D2)
    static let dim        = hex(0x6B6B7A)
    static let line       = hex(0x24242E)
    static let amber      = hex(0xFFB020)
    static let cyan       = hex(0x59C2C9)

    /// Color estable por proyecto (el "grupo" se reconoce por color).
    ///
    /// RESPALDO: el color canonico lo asigna px (PROJECT_PALETTE en px.py,
    /// misma paleta y mismo hash, pero con sondeo anti-colision y overrides
    /// del .px.conf) y llega en `px json`. Esto solo pinta si el json no trae
    /// color (px viejo).
    static func projectColor(_ name: String) -> NSColor {
        let palette: [NSColor] = [
            hex(0x7C5CFF), hex(0x54C07A), hex(0xFFB020), hex(0x4FC7E8),
            hex(0xE0607E), hex(0xA6CC3C), hex(0xB06CE8), hex(0x2EC4B6),
            hex(0xFF8A4C), hex(0x5E8DFF), hex(0xE5C838), hex(0xE060C8),
            hex(0x8A93A8), hex(0xC08552),
        ]
        var h: UInt32 = 5381
        for b in name.utf8 { h = (h &* 33) &+ UInt32(b) }
        return palette[Int(h % UInt32(palette.count))]
    }

    static func stateColor(_ s: AgentState) -> NSColor {
        switch s {
        case .work: return violet
        case .wait: return amber
        case .idle: return cyan
        case .off, .closed: return dim
        }
    }
}
