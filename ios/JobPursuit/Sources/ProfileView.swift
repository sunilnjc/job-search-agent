import SwiftUI

struct ProfileView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var phone = ""
    @State private var location = ""
    @State private var facts = ""
    @State private var background = CareerBackground()
    @State private var roles = ""
    @State private var locations = ""
    @State private var sponsorship = false
    @State private var authorization = ""
    @State private var signOut = false
    @State private var editingQualification: CareerQualification?
    @State private var loadedInitialProfile = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 16) { BrandMark(size: 52); VStack(alignment: .leading, spacing: 5) { Text("Your source of truth").font(.headline); Text("Better inputs. More honest applications.").font(.caption).foregroundStyle(Pursuit.muted) } }.padding(.vertical, 10)
                }
                Section("The essentials") {
                    TextField("Full name", text: $name).textContentType(.name)
                    TextField("Current city and country", text: $location)
                    TextField("Phone (with country code)", text: $phone).keyboardType(.phonePad).textContentType(.telephoneNumber)
                }
                CareerBackgroundFields(background: $background) { editingQualification = $0 }
                Section {
                    TextEditor(text: $facts).frame(minHeight: 200).accessibilityLabel("Confirmed career facts")
                } header: { Text("Confirmed career facts") } footer: { Text("Add paid work, placements, volunteering, responsibilities, outcomes, languages and portfolio links. Include dates and facts you can support. No software background is required.") }
                Section {
                    TextField("Target roles, separated by commas", text: $roles, axis: .vertical)
                    TextField("Preferred locations, separated by commas", text: $locations, axis: .vertical)
                    Toggle("I need employer sponsorship", isOn: $sponsorship)
                    TextField("Country-specific work rights and availability", text: $authorization, axis: .vertical).lineLimit(3...6)
                } header: { Text("What comes next") } footer: { Text("Work authorization depends on the country. A willingness to relocate is not permission to work there.") }
                Section { PrimaryButton(title: "Save my profile", icon: "checkmark", busy: store.isBusy) {
                    Task { await store.perform { try await store.saveProfile(name: name, location: location, phone: phone, facts: facts, background: background, roles: roles, locations: locations, sponsorship: sponsorship, authorization: authorization); dismiss() } }
                }.disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || background.profession.count > 120)
                    if background.profession.count > 120 { Text("Keep the profession or field within 120 characters.").font(.caption).foregroundStyle(.red) }
                    Text("A saved name and at least one target role complete your shared setup across native and web. You can save an incomplete profile and finish it later.").font(.caption).foregroundStyle(Pursuit.muted)
                }
                Section("Privacy and account") {
                    Label("Documents are private to your account", systemImage: "lock.shield")
                    Text("AI features send relevant profile and resume content to the configured provider when requested. Generated documents should be reviewed. This private beta does not automatically submit employer forms.").font(.caption).foregroundStyle(Pursuit.muted)
                    Button("Connection settings") { dismiss(); DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { store.showConfiguration = true } }
                    Button(store.isPreview ? "Exit preview" : "Sign out", role: .destructive) { signOut = true }
                }
            }.navigationTitle("Your profile").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
                .confirmationDialog("Sign out of this workspace?", isPresented: $signOut, titleVisibility: .visible) { Button("Sign out", role: .destructive) { Task { await store.signOut(); dismiss() } } }
                .onAppear {
                    guard !loadedInitialProfile else { return }
                    loadedInitialProfile = true
                    let p = store.workspace.profile
                    name = p?.displayName ?? ""; phone = p?.phone ?? ""; location = p?.baseLocation ?? ""; facts = p?.careerText ?? ""
                    background = p?.careerBackground ?? CareerBackground()
                    let prefs = store.workspace.preferences
                    roles = (prefs?.targetTitles ?? []).joined(separator: ", "); locations = (prefs?.preferredLocations ?? []).joined(separator: ", "); sponsorship = prefs?.sponsorshipRequired ?? false; authorization = prefs?.workAuthorizationNotes ?? ""
                }
        }
        // Present from the stable profile container, not a recycled Form section.
        // Keep the profile draft intact while the nested editor is presented.
        .sheet(item: $editingQualification) { qualification in
            QualificationEditor(qualification: qualification) { updated in
                if let index = background.qualifications.firstIndex(where: { $0.id == updated.id }) {
                    background.qualifications[index] = updated
                } else { background.qualifications.append(updated) }
            }
        }
    }
}
