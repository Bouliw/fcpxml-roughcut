// Draws Roughcut's icon: clips on a timeline, cut by a playhead, on a warm gradient. Writes an .iconset folder
// for iconutil. Usage: swift make_icon.swift out.iconset
import AppKit

func draw(_ px: Int) -> Data {
    let s = CGFloat(px)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let inset = s * 0.1  // the macOS icon grid: the shape sits inside a margin
    let body = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let shape = NSBezierPath(roundedRect: body, xRadius: body.width * 0.225, yRadius: body.width * 0.225)
    NSGraphicsContext.current?.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
    shadow.shadowBlurRadius = s * 0.025
    shadow.shadowOffset = NSSize(width: 0, height: -s * 0.01)
    shadow.set()
    NSGradient(colors: [NSColor(srgbRed: 1.0, green: 0.55, blue: 0.24, alpha: 1), NSColor(srgbRed: 0.84, green: 0.16, blue: 0.43, alpha: 1)])!
        .draw(in: shape, angle: -65)
    NSGraphicsContext.current?.restoreGraphicsState()
    // three tracks of clips, some cut away
    let w = body.width, x0 = body.minX + w * 0.16, unit = w * 0.68
    let rows: [(CGFloat, [(CGFloat, CGFloat)])] = [
        (0.62, [(0.0, 0.34), (0.40, 0.60), (0.66, 1.0)]),
        (0.45, [(0.0, 0.22), (0.28, 0.78), (0.84, 1.0)]),
        (0.28, [(0.0, 0.52), (0.58, 1.0)]),
    ]
    for (i, (y, clips)) in rows.enumerated() {
        for (a, b) in clips {
            let r = NSRect(x: x0 + unit * a, y: body.minY + w * y - w * 0.055, width: unit * (b - a), height: w * 0.11)
            NSColor.white.withAlphaComponent(i == 1 ? 1.0 : 0.82).setFill()
            NSBezierPath(roundedRect: r, xRadius: w * 0.03, yRadius: w * 0.03).fill()
        }
    }
    // the playhead
    let px0 = x0 + unit * 0.81
    NSColor(srgbRed: 0.2, green: 0.05, blue: 0.2, alpha: 0.85).setFill()
    NSBezierPath(roundedRect: NSRect(x: px0 - w * 0.012, y: body.minY + w * 0.16, width: w * 0.024, height: w * 0.62), xRadius: w * 0.012, yRadius: w * 0.012).fill()
    let head = NSBezierPath()
    head.move(to: NSPoint(x: px0 - w * 0.05, y: body.minY + w * 0.82))
    head.line(to: NSPoint(x: px0 + w * 0.05, y: body.minY + w * 0.82))
    head.line(to: NSPoint(x: px0, y: body.minY + w * 0.75))
    head.close()
    head.fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let out = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    try! draw(size).write(to: out.appendingPathComponent("icon_\(size)x\(size).png"))
    try! draw(size * 2).write(to: out.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
