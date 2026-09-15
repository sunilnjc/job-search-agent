import Foundation

// Compile with the shipping ColorTokens.swift. No network, UIKit or credentials.
@main enum ColorContrastChecks {
    static func luminance(_ hex: UInt32) -> Double {
        let rgb = [16, 8, 0].map { shift -> Double in
            let component = Double((hex >> shift) & 255) / 255
            return component <= 0.04045 ? component / 12.92 : pow((component + 0.055) / 1.055, 2.4)
        }
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722
    }

    static func main() {
        var count = 0
        var minimum = Double.infinity
        func check(_ foreground: UInt32, _ background: UInt32, _ name: String) {
            let a = luminance(foreground), b = luminance(background)
            let ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            precondition(ratio >= 4.5, "Insufficient text contrast: \(name) = \(ratio)")
            minimum = min(minimum, ratio)
            count += 1
        }
        for (name, scheme, systemSurfaces) in [
            ("light", PursuitColorTokens.light, [UInt32(0xFFFFFF), 0xF2F2F7]),
            ("dark", PursuitColorTokens.dark, [UInt32(0x000000), 0x1C1C1E, 0x2C2C2E])
        ] {
            for surface in [scheme.paper, scheme.card, scheme.subtle] + systemSurfaces {
                check(scheme.ink, surface, "\(name) primary")
                check(scheme.muted, surface, "\(name) supporting")
            }
        }
        check(PursuitColorTokens.onNight, PursuitColorTokens.brand, "primary button")
        check(PursuitColorTokens.onNight, PursuitColorTokens.night, "hero heading")
        check(PursuitColorTokens.mutedOnNight, PursuitColorTokens.night, "hero supporting text")
        check(PursuitColorTokens.night, PursuitColorTokens.mint, "hero button")
        print("PASS: \(count) shipping text/surface pairs meet 4.5:1; lowest \(String(format: "%.2f", minimum)):1. Rendering/system materials require separate visual QA.")
    }
}
