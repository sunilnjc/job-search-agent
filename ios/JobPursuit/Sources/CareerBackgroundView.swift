import SwiftUI

struct CareerBackgroundFields: View {
    @Binding var background: CareerBackground
    let editQualification: (CareerQualification) -> Void

    var body: some View {
        Section {
            TextField("Profession or field", text: $background.profession)
                .accessibilityIdentifier("profile.profession")
            Picker("Career stage", selection: $background.experienceLevel) {
                Text("Prefer not to specify").tag("unspecified")
                Text("Student / trainee").tag("student")
                Text("Starting my career").tag("entry")
                Text("Experienced professional").tag("mid")
                Text("Senior / leadership").tag("senior")
                Text("Changing careers").tag("career_change")
            }
        } header: { Text("Your career, your direction") }
        footer: { Text("Healthcare, finance, education, trades, creative work, technology—or any other field. Your job goals, not a fixed category, guide your applications.") }

        Section {
            ForEach(background.qualifications) { qualification in
                Button { editQualification(qualification) } label: {
                    HStack(spacing: 12) {
                        Image(systemName: qualification.kind == "licence" ? "checkmark.shield" : "graduationcap")
                            .foregroundStyle(Pursuit.ink)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(qualification.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                            Text(qualification.statusLabel + (qualification.jurisdiction.isEmpty ? "" : " · " + qualification.jurisdiction))
                                .font(.caption).foregroundStyle(Pursuit.muted)
                            if let expiry = qualification.expiresOn { Text("Expiry: \(expiry)").font(.caption2).foregroundStyle(Pursuit.muted) }
                        }
                        Spacer(); Image(systemName: "chevron.right").font(.caption).foregroundStyle(Pursuit.muted)
                    }.padding(.vertical, 3)
                }.buttonStyle(.plain)
            }.onDelete { background.qualifications.remove(atOffsets: $0) }
            Button { editQualification(CareerQualification()) } label: {
                Label("Add a qualification", systemImage: "plus.circle")
            }.disabled(background.qualifications.count >= 30)
                .accessibilityIdentifier("profile.addQualification")
        } header: { Text("Qualifications & licences") }
        footer: { Text("Add education, certifications or professional licences you can confirm. In-progress and expired credentials stay clearly labelled. We do not verify them with an issuing authority. Changes are stored when you save your profile.") }
    }
}

struct QualificationEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State var qualification: CareerQualification
    let onSave: (CareerQualification) -> Void
    @State private var hasExpiry = false
    @State private var expiryDate = Date()
    @State private var confirmed = false

    private static var dayFormat: DateFormatter {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(secondsFromGMT: 0)
        format.dateFormat = "yyyy-MM-dd"
        return format
    }
    private var isValid: Bool {
        let name = qualification.name.trimmingCharacters(in: .whitespacesAndNewlines)
        return !name.isEmpty && name.count <= 160 && qualification.jurisdiction.count <= 160 && qualification.evidenceNote.count <= 2000
    }
    var body: some View {
        NavigationStack {
            Form {
                Section("What you can confirm") {
                    TextField("Qualification or licence name", text: $qualification.name)
                        .accessibilityIdentifier("qualification.name")
                    Picker("Type", selection: $qualification.kind) {
                        Text("Education").tag("education")
                        Text("Professional licence").tag("licence")
                        Text("Certification").tag("certification")
                        Text("Other qualification").tag("other")
                    }
                    Picker("Status", selection: $qualification.status) {
                        Text("Not confirmed").tag("unknown")
                        Text("Current / completed").tag("current")
                        Text("In progress").tag("in_progress")
                        Text("Expired").tag("expired")
                        Text("Not held").tag("not_held")
                    }
                    TextField("Country or jurisdiction (if applicable)", text: $qualification.jurisdiction)
                    Toggle("Has an expiry date", isOn: $hasExpiry)
                    if hasExpiry { DatePicker("Expires on", selection: $expiryDate, displayedComponents: .date).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!) }
                }
                Section {
                    TextField("Issuing body, institution or relevant details", text: $qualification.evidenceNote, axis: .vertical).lineLimit(3...6)
                } header: { Text("Supporting context (optional)") }
                footer: { Text("Include what is needed to interpret the credential. Do not add passport numbers, document scans, patient details or other sensitive records. A licence in one jurisdiction is not proof of permission to practise elsewhere.") }
                Section {
                    Toggle("These details and their status are accurate", isOn: $confirmed)
                    Button("Use these details") {
                        qualification.name = qualification.name.trimmingCharacters(in: .whitespacesAndNewlines)
                        qualification.expiresOn = hasExpiry ? Self.dayFormat.string(from: expiryDate) : nil
                        onSave(qualification); dismiss()
                    }.disabled(!confirmed || !isValid).accessibilityIdentifier("qualification.confirm")
                } footer: { Text("Saving the profile makes these self-reported facts available for matching and document preparation. Unknown details stay unknown.") }
            }.navigationTitle("Qualification").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                .onAppear {
                    hasExpiry = qualification.expiresOn != nil
                    if let raw = qualification.expiresOn, let parsed = Self.dayFormat.date(from: raw) { expiryDate = parsed }
                }
        }
    }
}
