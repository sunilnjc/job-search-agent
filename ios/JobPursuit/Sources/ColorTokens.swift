// Opaque sRGB tokens shared by SwiftUI and the offline contrast checks.
// Keep text neutral; brand colour is reserved for filled primary actions.
struct PursuitColorScheme {
    let ink: UInt32
    let muted: UInt32
    let paper: UInt32
    let card: UInt32
    let subtle: UInt32
}

enum PursuitColorTokens {
    static let light = PursuitColorScheme(
        ink: 0x232426, muted: 0x575B61,
        paper: 0xF4F0E8, card: 0xFFFFFF, subtle: 0xECECED
    )
    static let dark = PursuitColorScheme(
        ink: 0xF4F4F5, muted: 0xBFC2C8,
        paper: 0x111218, card: 0x1C1D22, subtle: 0x26272D
    )
    static let brand: UInt32 = 0x6640E0
    static let night: UInt32 = 0x233238
    static let onNight: UInt32 = 0xFFFFFF
    static let mutedOnNight: UInt32 = 0xD1D5DE
    static let mint: UInt32 = 0xA8B5A2
}
