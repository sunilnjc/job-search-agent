import SwiftUI

struct EligibilityReviewView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let job: Opportunity
    @State private var draft: EligibilityReviewDraft
    init(job: Opportunity) {
        self.job = job
        _draft = State(initialValue: EligibilityReviewDraft(
            status: EligibilityChoice(rawValue: job.eligibilityReview?.status ?? "unknown") ?? .unknown,
            reason: job.eligibilityReview?.reason ?? "", confirmed: false))
    }
    var body: some View {
        NavigationStack {
            Form {
                Section { Text(job.title).font(.headline); Text(job.companyName).foregroundStyle(Pursuit.muted) }
                Section {
                    Text(JobEligibilityReview.disclaimer).accessibilityIdentifier("eligibility.disclaimer")
                    Text("Review the current requirements and your country-specific work rights. Choose Not sure yet when facts are missing. Saving does not submit an application.").font(.caption).foregroundStyle(Pursuit.muted)
                } header: { Text("Your assessment, not verification") }
                Section("Eligibility for this opportunity") {
                    Picker("My assessment", selection: $draft.status) {
                        ForEach(EligibilityChoice.allCases, id: \.self) { Text($0.label).tag($0) }
                    }.accessibilityIdentifier("eligibility.status")
                    TextField("Reason or unresolved questions", text: $draft.reason, axis: .vertical).lineLimit(3...8).accessibilityIdentifier("eligibility.reason")
                    Text("\(draft.trimmedReason.count) / 2,000 characters").font(.caption).foregroundStyle(Pursuit.muted)
                }
                Section {
                    Toggle("I confirm this is my own assessment for this job", isOn: $draft.confirmed).accessibilityIdentifier("eligibility.confirm")
                    PrimaryButton(title: "Save self-report", icon: "checkmark", busy: store.isBusy) {
                        Task { await store.perform { try await store.reviewEligibility(jobId: job.id, draft: draft); dismiss() } }
                    }.disabled(!draft.isValid || store.isBusy).accessibilityIdentifier("eligibility.save")
                }
            }.navigationTitle("Eligibility review").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

struct EditOpportunityView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var edits: OpportunityEdits
    init(job: Opportunity) { _edits = State(initialValue: OpportunityEdits(job: job)) }
    var body: some View {
        NavigationStack {
            Form {
                Section("Opportunity") {
                    TextField("Role title", text: $edits.title)
                    TextField("Company", text: $edits.company)
                    TextField("Location", text: $edits.location)
                }
                Section {
                    TextEditor(text: $edits.description).frame(minHeight: 200).accessibilityLabel("Job description")
                } header: { Text("Employer's job description") } footer: {
                    Text("Paste the actual requirements. Titles and company names allow 160 characters, location 300, and description 80,000. Changed requirements reset match analysis and eligibility review, but do not remove your tracked application.")
                }
                Section {
                    PrimaryButton(title: "Save opportunity changes", icon: "checkmark", busy: store.isBusy) {
                        Task { await store.perform { try await store.updateJob(edits); dismiss() } }
                    }.disabled(!edits.isValid || store.isBusy)
                }
            }.navigationTitle("Edit opportunity").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}
