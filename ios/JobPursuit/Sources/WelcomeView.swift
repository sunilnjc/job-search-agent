import SwiftUI

struct WelcomeView: View {
    @EnvironmentObject private var store: AppStore
    @State private var email = ""
    @State private var code = ""
    @State private var codeSent = false
    @State private var nextSend = Date.distantPast
    @State private var consent = false
    @ScaledMetric(relativeTo: .largeTitle) private var headlineSize = 43
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 30) {
                    HStack(spacing: 12) { BrandMark().accessibilityHidden(true); Text("the job pursuit").font(.system(.headline, design: .serif, weight: .semibold)); Spacer(); Chip(text: "EARLY ACCESS") }.padding(.top, 18)
                    VStack(alignment: .leading, spacing: 18) {
                        Eyebrow(text: "Your next chapter")
                        Text("Great careers\ndon't happen\nby chance.").font(.system(size: headlineSize, weight: .semibold, design: .rounded)).tracking(-1.8).fixedSize(horizontal: false, vertical: true).foregroundStyle(Pursuit.ink)
                        Text("Find your fit. Tell your story.\nMake your next move with intention.").font(.body).lineSpacing(5).foregroundStyle(Pursuit.muted)
                    }.padding(.top, 12)
                    VStack(alignment: .leading, spacing: 18) {
                        HStack { Image(systemName: "sparkles").foregroundStyle(Pursuit.mint); Text("A workspace built around you").font(.subheadline.weight(.semibold)).foregroundStyle(.white) }
                        HStack(alignment: .top, spacing: 18) {
                            welcomeFeature("scope", "Better-fit\nopportunities")
                            welcomeFeature("doc.text", "Your story,\nwell told")
                            welcomeFeature("checkmark.seal", "Progress\nyou can trust")
                        }
                    }.padding(22).frame(maxWidth: .infinity, alignment: .leading).background(Pursuit.night, in: RoundedRectangle(cornerRadius: 26))
                    VStack(alignment: .leading, spacing: 15) {
                        Text(codeSent ? "Check your inbox" : "Your next move starts here").font(.title3.weight(.semibold))
                        if codeSent {
                            Text("Enter the email code sent to \(email). It signs you in without a password.").font(.subheadline).foregroundStyle(Pursuit.muted)
                            TextField("Email code", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode).textFieldStyle(.roundedBorder).accessibilityIdentifier("auth.code")
                            PrimaryButton(title: "Enter my workspace", busy: store.isBusy) { Task { await store.perform { try await store.verifyCode(email: email, code: code) } } }.disabled(code.count < 6)
                            Button("Use a different email") { codeSent = false; code = "" }.font(.subheadline)
                            TimelineView(.periodic(from: .now, by: 1)) { context in
                                let remaining = max(0, Int(ceil(nextSend.timeIntervalSince(context.date))))
                                Button(remaining > 0 ? "Resend available in \(remaining)s" : "Resend code") { send() }.font(.caption).disabled(remaining > 0 || store.isBusy)
                            }
                        } else {
                            TextField("Your email address", text: $email).keyboardType(.emailAddress).textContentType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                                .padding(17).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 16)).overlay(RoundedRectangle(cornerRadius: 16).stroke(Pursuit.line))
                                .accessibilityIdentifier("auth.email")
                            Toggle(isOn: $consent) { Text("I understand my profile and documents are stored privately, and sent to the configured AI provider only when I use AI features.").font(.caption).foregroundStyle(Pursuit.muted) }.tint(Pursuit.violet)
                            PrimaryButton(title: "Continue with email", busy: store.isBusy) { send() }.disabled(!email.contains("@") || !consent)
                            HStack { Image(systemName: "lock.shield"); Text("Private by default. Always in your control.") }.font(.caption).foregroundStyle(Pursuit.muted)
                        }
                    }
                    Button { store.enterPreview() } label: { HStack { Text("Explore the design preview"); Image(systemName: "arrow.up.right") }.font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity) }.accessibilityIdentifier("auth.preview")
                    Button("Connection settings") { store.showConfiguration = true }.font(.caption).foregroundStyle(Pursuit.muted).frame(maxWidth: .infinity)
                }.padding(.horizontal, 26).padding(.bottom, 32).frame(maxWidth: 520).frame(maxWidth: .infinity)
            }.pursuitPage().navigationBarHidden(true)
        }
    }
    func send() {
        let normalized = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        Task { await store.perform { try await store.sendCode(email: normalized); email = normalized; codeSent = true; nextSend = Date().addingTimeInterval(60) } }
    }
    func welcomeFeature(_ icon: String, _ title: String) -> some View {
        VStack(alignment: .leading, spacing: 10) { Image(systemName: icon).font(.title3).foregroundStyle(Pursuit.mint); Text(title).font(.caption).foregroundStyle(Pursuit.mutedOnNight).lineSpacing(3) }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ConnectionView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var api = ""
    @State private var supabase = ""
    @State private var key = ""
    var body: some View {
        NavigationStack {
            Form {
                Section { Text("Connect to your isolated mobile API and Supabase project. This screen is for the private beta; production builds use bundled public settings.").font(.subheadline) }
                Section("Mobile API origin") { TextField("https://www.thejobpursuit.com", text: $api).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL) }
                Section {
                    TextField("https://project.supabase.co", text: $supabase).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("Public publishable key", text: $key).textInputAutocapitalization(.never).autocorrectionDisabled()
                } header: { Text("Supabase") } footer: { Text("Use only the browser-safe publishable key. Never enter an AI API key or service-role/secret key here. Changing the connection signs you out.") }
                Section { Button("Save connection") {
                    let config = AppConfiguration(apiBase: api.trimmingCharacters(in: .whitespacesAndNewlines), supabaseUrl: supabase.trimmingCharacters(in: .whitespacesAndNewlines), publishableKey: key.trimmingCharacters(in: .whitespacesAndNewlines))
                    guard config.isReady else { store.error = "Use HTTPS URLs and a Supabase public publishable key."; return }
                    store.setConfiguration(config); dismiss()
                } }
            }.navigationTitle("Connection").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
                .onAppear { api = store.configuration.apiBase; supabase = store.configuration.supabaseUrl; key = store.configuration.publishableKey }
        }
    }
}
