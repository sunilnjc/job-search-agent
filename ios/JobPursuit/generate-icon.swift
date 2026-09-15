// Reproducible vector-drawn app icon. Run from this directory: swift generate-icon.swift
import AppKit
let size = 1024
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor(red: 0.40, green: 0.25, blue: 0.88, alpha: 1).setFill()
NSBezierPath(rect: NSRect(x: 0, y: 0, width: size, height: size)).fill()
NSColor.white.withAlphaComponent(0.09).setFill()
NSBezierPath(ovalIn: NSRect(x: 570, y: 550, width: 550, height: 550)).fill()
let stem = NSBezierPath()
stem.move(to: NSPoint(x: 274, y: 274)); stem.line(to: NSPoint(x: 724, y: 724))
stem.lineWidth = 92; stem.lineCapStyle = .round
NSColor.white.setStroke(); stem.stroke()
let head = NSBezierPath()
head.move(to: NSPoint(x: 362, y: 724)); head.line(to: NSPoint(x: 724, y: 724)); head.line(to: NSPoint(x: 724, y: 362))
head.lineWidth = 92; head.lineCapStyle = .round; head.lineJoinStyle = .round; head.stroke()
NSColor(red: 0.78, green: 0.94, blue: 0.79, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 230, y: 230, width: 92, height: 92)).fill()
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png"))
print("Generated AppIcon.png")
