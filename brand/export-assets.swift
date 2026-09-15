// Package the approved raster without changing its design. Run: swift brand/export-assets.swift
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let source = root.appendingPathComponent("brand/unfold-production-v1/icon-master.png")
guard let imageSource = CGImageSourceCreateWithURL(source as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil)
else { fatalError("Missing approved Unfold master") }
// Opaque, correctly sized exports for app stores, browsers, and email clients.
let exports: [(String, Int)] = [
    ("web/public/brand/unfold-mark-v1.png", 256),
    ("web/public/brand/unfold-favicon-v1.png", 48),
    ("web/public/brand/unfold-touch-v1.png", 180),
    ("web/public/brand/unfold-192-v1.png", 192),
    ("web/public/brand/unfold-512-v1.png", 512),
    ("web/public/brand/unfold-email-v1.png", 96),
    ("ios/JobPursuit/Resources/Assets.xcassets/UnfoldMark.imageset/unfold-mark.png", 256),
    ("ios/JobPursuit/Resources/Assets.xcassets/AppIcon.appiconset/UnfoldAppIcon.png", 1024),
]
for (path, size) in exports {
    let output = root.appendingPathComponent(path)
    try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
    let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
        bytesPerRow: size * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    context.interpolationQuality = .high
    let rect = CGRect(x: 0, y: 0, width: size, height: size)
    context.setFillColor(CGColor(red: 244/255, green: 240/255, blue: 232/255, alpha: 1))
    context.fill(rect)
    context.draw(image, in: rect)
    let destination = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, context.makeImage()!, nil)
    guard CGImageDestinationFinalize(destination) else { fatalError("Failed to export \(path)") }
    print("Exported \(size)x\(size): \(path)")
}
