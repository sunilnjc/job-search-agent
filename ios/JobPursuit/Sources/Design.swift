import SwiftUI

enum Pursuit {
    static let ink = adaptive(\.ink)
    static let muted = adaptive(\.muted)
    static let paper = adaptive(\.paper)
    static let card = adaptive(\.card)
    static let subtle = adaptive(\.subtle)
    static let violet = Color(uiColor: uiColor(PursuitColorTokens.brand))
    static let mint = Color(uiColor: uiColor(PursuitColorTokens.mint))
    static let night = Color(uiColor: uiColor(PursuitColorTokens.night))
    static let onNight = Color(uiColor: uiColor(PursuitColorTokens.onNight))
    static let mutedOnNight = Color(uiColor: uiColor(PursuitColorTokens.mutedOnNight))
    static let line = Color.primary.opacity(0.08)

    private static func adaptive(_ key: KeyPath<PursuitColorScheme, UInt32>) -> Color {
        Color(uiColor: UIColor { traits in
            let scheme = traits.userInterfaceStyle == .dark ? PursuitColorTokens.dark : PursuitColorTokens.light
            // Stronger supporting text when the user requests increased contrast.
            let hex = traits.accessibilityContrast == .high && key == \.muted ? scheme.ink : scheme[keyPath: key]
            return uiColor(hex)
        })
    }

    private static func uiColor(_ hex: UInt32) -> UIColor {
        UIColor(red: CGFloat((hex >> 16) & 255) / 255,
                green: CGFloat((hex >> 8) & 255) / 255,
                blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
}

struct PrimaryButton: View {
    let title: String
    var icon: String = "arrow.right"
    var busy = false
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                if busy { ProgressView().tint(.white) }
                Text(title).font(.system(.body, design: .rounded, weight: .semibold))
                Spacer(minLength: 0)
                Image(systemName: icon).font(.body.weight(.semibold))
            }
            .padding(.horizontal, 21).padding(.vertical, 18)
            .foregroundStyle(.white).background(Pursuit.violet, in: RoundedRectangle(cornerRadius: 18))
        }.buttonStyle(.plain).disabled(busy)
    }
}

struct Eyebrow: View {
    let text: String
    var color: Color = Pursuit.muted
    var body: some View { Text(text.uppercased()).font(.system(size: 10, weight: .bold, design: .monospaced)).tracking(2.0).foregroundStyle(color) }
}

struct BrandMark: View {
    var size: CGFloat = 36
    var body: some View {
        Image("UnfoldMark").resizable().interpolation(.high).scaledToFit()
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.22))
            .accessibilityLabel("The Job Pursuit")
    }
}

struct SectionTitle: View {
    let title: String
    var subtitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(.title3, design: .rounded, weight: .bold)).foregroundStyle(Pursuit.ink)
            if let subtitle { Text(subtitle).font(.subheadline).foregroundStyle(Pursuit.muted) }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct Chip: View {
    let text: String
    var icon: String? = nil
    var color: Color = Pursuit.muted
    var body: some View {
        HStack(spacing: 4) {
            if let icon { Image(systemName: icon) }
            Text(text)
        }.font(.caption.weight(.medium)).padding(.horizontal, 10).padding(.vertical, 7)
            .foregroundStyle(color).background(Pursuit.subtle, in: Capsule())
    }
}

struct EmptyPanel: View {
    let icon: String
    let title: String
    let detail: String
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: icon).font(.system(size: 30, weight: .light)).foregroundStyle(Pursuit.muted).padding(20).background(Pursuit.subtle, in: Circle())
            Text(title).font(.headline).foregroundStyle(Pursuit.ink)
            Text(detail).font(.subheadline).foregroundStyle(Pursuit.muted).multilineTextAlignment(.center).lineSpacing(3)
        }.padding(30).frame(maxWidth: .infinity).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 24))
    }
}

struct JobCard: View {
    let job: Opportunity
    var compact = false
    var body: some View {
        VStack(alignment: .leading, spacing: 19) {
            HStack(alignment: .top) {
                Text(job.initials).font(.system(.headline, design: .rounded, weight: .bold))
                    .frame(width: 45, height: 45).foregroundStyle(Pursuit.ink)
                    .background(Pursuit.paper, in: RoundedRectangle(cornerRadius: 14))
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.companyName).font(.subheadline.weight(.semibold)).foregroundStyle(Pursuit.ink)
                    Text(job.displayLocation).font(.caption).foregroundStyle(Pursuit.muted)
                }.padding(.top, 3)
                Spacer(minLength: 4)
                Image(systemName: "arrow.up.right").foregroundStyle(Pursuit.muted).font(.caption.weight(.bold)).padding(.top, 4)
            }
            Text(job.title).font(.system(.title3, design: .rounded, weight: .semibold)).foregroundStyle(Pursuit.ink).lineSpacing(2)
            HStack {
                Chip(text: job.matchLabel, icon: job.score == nil ? "sparkle" : "checkmark.seal.fill")
                Spacer()
                if job.eligibilityStatus != "eligible" { Text("Eligibility to verify").font(.caption2).foregroundStyle(Pursuit.muted) }
            }
            if !compact, let rationale = job.rationale, !rationale.isEmpty {
                Divider().overlay(Pursuit.line)
                Text(rationale).font(.subheadline).foregroundStyle(Pursuit.muted).lineSpacing(3).lineLimit(3)
            }
        }.padding(21).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).stroke(Pursuit.line, lineWidth: 0.7))
            .contentShape(RoundedRectangle(cornerRadius: 24))
    }
}

struct SheetHeader: View {
    let eyebrow: String
    let title: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Eyebrow(text: eyebrow)
            Text(title).font(.system(.largeTitle, design: .rounded, weight: .bold)).foregroundStyle(Pursuit.ink)
            Text(detail).font(.subheadline).foregroundStyle(Pursuit.muted).lineSpacing(4)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension View {
    func pursuitPage() -> some View { self.foregroundStyle(Pursuit.ink).background(Pursuit.paper).toolbarBackground(Pursuit.paper, for: .navigationBar) }
    func cardSurface() -> some View { self.padding(20).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 24)) }
}
