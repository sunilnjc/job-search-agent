import SwiftUI

struct TrackerView: View {
    @EnvironmentObject private var store: AppStore
    @State private var filter = "Active"
    @State private var questions = false
    var applications: [JobApplication] { store.workspace.applications.filter { filter == "All" || !["closed", "withdrawn", "rejected"].contains($0.status) } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                SheetHeader(eyebrow: "Every step matters", title: "Forward,\none move at a time.", detail: "A clear view of what you've prepared, what needs you, and what actually happened.")
                HStack(spacing: 0) {
                    trackerMetric("PREPARED", count: store.workspace.applications.filter { ["draft", "ready"].contains($0.status) }.count)
                    Divider().frame(height: 48)
                    trackerMetric("SUBMITTED", count: store.workspace.applications.filter { $0.status == "submitted" }.count)
                    Divider().frame(height: 48)
                    trackerMetric("INTERVIEWS", count: store.workspace.applications.filter { $0.status == "interviewing" }.count)
                }.cardSurface()
                if !store.workspace.pendingQuestions.isEmpty {
                    Button { questions = true } label: {
                        HStack { Image(systemName: "bubble.left.and.exclamationmark.bubble.right").font(.title2); VStack(alignment: .leading, spacing: 5) { Text("Needs you").font(.headline); Text("\(store.workspace.pendingQuestions.count) questions to unlock your next step").font(.caption).foregroundStyle(Pursuit.muted) }; Spacer(); Image(systemName: "chevron.right") }.foregroundStyle(Pursuit.ink).cardSurface()
                    }.buttonStyle(.plain)
                }
                Picker("Application filter", selection: $filter) { Text("Active").tag("Active"); Text("All applications").tag("All") }.pickerStyle(.segmented)
                ForEach(applications) { application in
                    let job = store.opportunity(id: application.jobId) ?? .trackerPlaceholder(jobId: application.jobId)
                    NavigationLink(value: application.jobId) {
                        VStack(alignment: .leading, spacing: 16) {
                            HStack { Chip(text: application.status == "submitted" ? "Submitted · reported by you" : application.label, icon: application.status == "submitted" ? "checkmark.circle" : "circle.dotted"); Spacer(); Image(systemName: "arrow.up.right").font(.caption).foregroundStyle(Pursuit.muted) }
                            Text(job.title).font(.system(.title3, design: .rounded, weight: .semibold)).foregroundStyle(Pursuit.ink)
                            Text(job.companyName).font(.subheadline).foregroundStyle(Pursuit.muted)
                            if store.opportunity(id: application.jobId) == nil { Text("Open to load job details. Your application is still saved.").font(.caption).foregroundStyle(Pursuit.muted) }
                            if let notes = application.notes, !notes.isEmpty { Divider(); Text(notes).font(.caption).foregroundStyle(Pursuit.muted).lineLimit(3) }
                        }.cardSurface()
                    }.buttonStyle(.plain).accessibilityIdentifier("tracker.application.\(application.id)")
                }
                if applications.isEmpty { EmptyPanel(icon: "tray", title: "Your progress belongs here", detail: "Open an opportunity and tap Track. A prepared document is not a submitted application—we keep those steps distinct.") }
                Label("No inflated counts. No invisible applications.", systemImage: "checkmark.shield").font(.caption).foregroundStyle(Pursuit.muted).frame(maxWidth: .infinity)
            }.padding(22).frame(maxWidth: 700).frame(maxWidth: .infinity)
        }.pursuitPage().navigationTitle("Tracker").navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: String.self) { JobDetailView(jobId: $0) }
            .sheet(isPresented: $questions) { QuestionsView() }
            .refreshable { await store.perform { try await store.reload() } }
    }
    func trackerMetric(_ title: String, count: Int) -> some View { VStack(spacing: 9) { Text(count, format: .number).font(.system(size: 30, weight: .medium, design: .rounded)); Text(title).font(.system(size: 8, weight: .bold, design: .monospaced)).tracking(0.5).foregroundStyle(Pursuit.muted) }.frame(maxWidth: .infinity) }
}

struct ApplicationStatusView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let job: Opportunity
    @State private var status = "draft"
    @State private var notes = ""
    @State private var confirmed = false
    var body: some View {
        NavigationStack {
            Form {
                Section { Text(job.title).font(.headline); Text(job.companyName).foregroundStyle(Pursuit.muted) }
                Section("Current stage") { Picker("Stage", selection: $status) { ForEach(["draft", "ready", "submitted", "interviewing", "rejected", "withdrawn", "closed"], id: \.self) { Text($0.capitalized).tag($0) } } }
                Section { TextEditor(text: $notes).frame(minHeight: 130) } header: { Text("Evidence and notes") } footer: { Text("For a submission, record the confirmation text or reference. Do not add passwords or account verification codes.") }
                if status == "submitted" { Section { Toggle("I have a submission confirmation", isOn: $confirmed); Text("This records your confirmed submission. It does not send an application to the employer.").font(.caption).foregroundStyle(Pursuit.muted) } }
                Section { PrimaryButton(title: "Update tracker", icon: "checkmark", busy: store.isBusy) { Task { await store.perform { try await store.updateApplication(jobId: job.id, status: status, notes: notes); dismiss() } } }.disabled(status == "submitted" && (!confirmed || notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)) }
            }.navigationTitle("Application progress").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
                .onAppear { if let a = store.workspace.applications.first(where: { $0.jobId == job.id }) { status = a.status; notes = a.notes ?? "" } }
        }
    }
}

struct QuestionsView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    var jobId: String? = nil
    @State private var profile = false
    private var pending: [CandidateQuestion] { store.workspace.pendingQuestions.filter { jobId == nil || $0.jobId == jobId } }
    private var answered: [CandidateQuestion] { store.workspace.questions.filter { $0.status == "answered" && (jobId == nil || $0.jobId == jobId) } }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    SheetHeader(eyebrow: "A human moment", title: "Only you know\nthis part.", detail: "When a fact is missing, we'll ask. Confirm an answer once and choose whether to remember it.")
                    if pending.isEmpty { EmptyPanel(icon: "checkmark.bubble", title: "No unanswered questions", detail: "Saved answers are not proof of eligibility. Review any gaps in the match analysis, update qualification details if needed, then prepare again. No application is submitted automatically.") }
                    ForEach(pending) { question in QuestionCard(question: question) }
                    if !answered.isEmpty {
                        DisclosureGroup("Review or correct saved answers (\(answered.count))") {
                            ForEach(answered) { question in QuestionCard(question: question).padding(.top, 12) }
                        }.font(.subheadline.weight(.medium))
                    }
                    Button { profile = true } label: { Label("Update career facts or qualifications", systemImage: "person.crop.circle.badge.checkmark") }.font(.subheadline)
                    Text("Your answer is used only in its saved context. A licence, work right or employer declaration is never assumed to apply everywhere.").font(.caption).foregroundStyle(Pursuit.muted)
                }.padding(22)
            }.pursuitPage().navigationTitle("Needs you").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
                .sheet(isPresented: $profile) { ProfileView() }
        }
    }
}

struct QuestionCard: View {
    @EnvironmentObject private var store: AppStore
    let question: CandidateQuestion
    @State private var answer = ""
    @State private var remember = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let job = store.workspace.jobs.first(where: { $0.id == question.jobId }) { Eyebrow(text: job.companyName) }
            Text(question.prompt).font(.headline).lineSpacing(3)
            TextField("Your confirmed answer", text: $answer, axis: .vertical).lineLimit(3...7).padding(15).background(Pursuit.paper, in: RoundedRectangle(cornerRadius: 14))
            Toggle("Remember this answer", isOn: $remember).font(.subheadline)
            Text("Only save facts that remain true in this question's context. Company-specific declarations are not universal preferences.").font(.caption).foregroundStyle(Pursuit.muted)
            PrimaryButton(title: question.status == "answered" ? "Update confirmed answer" : "Confirm answer", icon: "checkmark", busy: store.isBusy) { Task { await store.perform { try await store.answer(question, text: answer, remember: remember) } } }.disabled(answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }.cardSurface()
            .onAppear { answer = question.answer ?? ""; remember = question.remember ?? false }
    }
}
