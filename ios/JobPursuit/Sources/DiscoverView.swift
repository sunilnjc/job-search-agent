import SwiftUI

struct DiscoverView: View {
    @EnvironmentObject private var store: AppStore
    @State private var search = ""
    @State private var filter = "All"
    @State private var add = false
    var jobs: [Opportunity] {
        store.workspace.rankedJobs.filter { job in
            (search.isEmpty || "\(job.title) \(job.companyName) \(job.displayLocation)".localizedCaseInsensitiveContains(search)) && (filter != "Strong fit" || (job.score ?? 0) >= 8) && (filter != "Remote" || job.workplaceType == "remote")
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                SheetHeader(eyebrow: "Possibility, curated", title: "Find your fit.", detail: "A shortlist with purpose. Start with a role you love, then understand why it fits.")
                HStack { Image(systemName: "magnifyingglass").foregroundStyle(Pursuit.muted); TextField("Role, company or location", text: $search).accessibilityIdentifier("discover.search") }.padding(16).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 17))
                HStack(spacing: 9) { ForEach(["All", "Strong fit", "Remote"], id: \.self) { item in Button { filter = item } label: { Text(item).font(.subheadline.weight(.medium)).padding(.horizontal, 17).padding(.vertical, 11).foregroundStyle(filter == item ? .white : Pursuit.ink).background(filter == item ? Pursuit.night : Pursuit.card, in: Capsule()) }.buttonStyle(.plain) } }
                HStack { Eyebrow(text: "\(jobs.count) opportunities"); Spacer(); Text("Best match first").font(.caption).foregroundStyle(Pursuit.muted) }
                ForEach(jobs) { job in NavigationLink(value: job) { JobCard(job: job) }.buttonStyle(.plain) }
                if jobs.isEmpty { EmptyPanel(icon: "scope", title: search.isEmpty ? "Your search starts here" : "No matches in your shortlist", detail: search.isEmpty ? "Add a job URL and its description. Your profile gives the match analysis context." : "Try a different keyword or remove a filter.") }
                PrimaryButton(title: "Add an opportunity", icon: "plus") { add = true }.accessibilityIdentifier("discover.add")
            }.padding(22).frame(maxWidth: 700).frame(maxWidth: .infinity)
        }.pursuitPage().navigationTitle("Discover").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { add = true } label: { Image(systemName: "plus") }.accessibilityLabel("Add opportunity") } }
            .navigationDestination(for: Opportunity.self) { JobDetailView(jobId: $0.id) }
            .sheet(isPresented: $add) { AddOpportunityView() }
            .refreshable { await store.perform { try await store.reload() } }
    }
}

struct AddOpportunityView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var title = ""
    @State private var company = ""
    @State private var location = ""
    @State private var description = ""
    var body: some View {
        NavigationStack {
            Form {
                Section { Text("Bring a role from any job board or company careers page. Paste the actual description so the analysis uses the employer's requirements.").font(.subheadline) }
                Section("Opportunity") {
                    TextField("Job URL", text: $url).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Role title", text: $title)
                    TextField("Company", text: $company)
                    TextField("Location", text: $location)
                }
                Section { TextEditor(text: $description).frame(minHeight: 200).accessibilityLabel("Job description") } header: { Text("Job description") } footer: { Text("The same role URL won't create duplicate opportunities. Adding a job does not submit an application.") }
                Section { PrimaryButton(title: "Save opportunity", busy: store.isBusy) {
                    Task { await store.perform { try await store.importJob(url: url, title: title, company: company, location: location, description: description); dismiss() } }
                }.disabled(title.isEmpty || company.isEmpty || URL(string: url)?.host == nil || description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty).listRowBackground(Color.clear).listRowInsets(EdgeInsets()) }
            }.navigationTitle("Add opportunity").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

struct JobDetailView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.openURL) private var openURL
    let jobId: String
    @State private var assistant = false
    @State private var tracker = false
    @State private var prepare = false
    @State private var questions = false
    @State private var eligibility = false
    @State private var edit = false
    @State private var loadingDetail = true
    @State private var detailError: String?
    var job: Opportunity? {
        store.opportunity(id: jobId) ?? (store.workspace.applications.contains { $0.jobId == jobId } ? .trackerPlaceholder(jobId: jobId) : nil)
    }
    private var canUseDetails: Bool { !loadingDetail && detailError == nil && store.opportunity(id: jobId) != nil }
    private var supportsReview: Bool { store.isPreview || store.workspace.capabilities?.supportsEligibilityReview == true }
    var body: some View {
        Group {
            if let job {
                ScrollView {
                    VStack(alignment: .leading, spacing: 25) {
                        detailLoadStatus
                        if let warning = store.operationWarnings[jobId] { Label(warning, systemImage: "exclamationmark.triangle").font(.subheadline).cardSurface() }
                        HStack { Text(job.initials).font(.title2.bold()).frame(width: 64, height: 64).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 21)); Spacer(); Chip(text: job.matchLabel, icon: "scope") }
                        VStack(alignment: .leading, spacing: 12) { Eyebrow(text: job.companyName); Text(job.title).font(.system(.largeTitle, design: .rounded, weight: .bold)).tracking(-0.7); Label(job.displayLocation, systemImage: "mappin.and.ellipse").font(.subheadline).foregroundStyle(Pursuit.muted) }
                        VStack(alignment: .leading, spacing: 13) {
                            Label("The fit, explained", systemImage: "sparkles").font(.headline).foregroundStyle(Pursuit.ink)
                            Text(job.rationale ?? "Analyze this role against your resume and confirmed preferences. Unknown requirements remain open questions, not automatic rejections.").font(.subheadline).lineSpacing(4)
                            Button(job.score == nil ? "Analyze my match →" : "Refresh match analysis →") { Task { await store.perform { questions = try await store.rank(job) } } }.font(.subheadline.weight(.semibold)).disabled(store.isBusy || !canUseDetails)
                            if store.workspace.questions.contains(where: { $0.jobId == job.id }) {
                                Button { questions = true } label: { Label("Review application answers", systemImage: "bubble.left.and.exclamationmark.bubble.right") }.font(.subheadline.weight(.medium))
                            }
                        }.cardSurface()
                        VStack(alignment: .leading, spacing: 10) {
                            Label(job.eligibilityReview?.label ?? "Eligibility not reviewed", systemImage: "info.circle").font(.subheadline.weight(.medium))
                            if let review = job.eligibilityReview { Text(review.reason).font(.subheadline).textSelection(.enabled) }
                            Text(JobEligibilityReview.disclaimer).font(.caption).foregroundStyle(Pursuit.muted).lineSpacing(3)
                            Button(job.eligibilityReview == nil ? "Review eligibility" : "Update eligibility review") { eligibility = true }
                                .font(.subheadline.weight(.semibold)).disabled(!canUseDetails || !supportsReview || store.isBusy)
                                .accessibilityIdentifier("job.reviewEligibility")
                            if !supportsReview { Text("This server does not yet support job-specific reviews.").font(.caption).foregroundStyle(Pursuit.muted) }
                        }.padding(18).background(Pursuit.subtle, in: RoundedRectangle(cornerRadius: 20))
                        SectionTitle(title: "About the opportunity")
                        if canUseDetails {
                            Text(job.description?.isEmpty == false ? job.description! : "No description added yet. Edit this opportunity to add the employer's requirements.").font(.body).lineSpacing(5).textSelection(.enabled)
                                .accessibilityIdentifier("job.description")
                            Button("Edit opportunity") { edit = true }.font(.subheadline.weight(.semibold)).accessibilityIdentifier("job.edit")
                        }
                        HStack(spacing: 12) {
                            Button { assistant = true } label: { Label("Ask AI", systemImage: "sparkles").frame(maxWidth: .infinity).padding(17).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 17)) }.disabled(!canUseDetails)
                            Button { tracker = true } label: { Label("Track", systemImage: "tray").frame(maxWidth: .infinity).padding(17).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 17)) }
                        }.font(.subheadline.weight(.semibold))
                        Button { if store.isPreview { store.error = "This fictional role has no real employer application. Sign in and add an opportunity to open its application form." } else if let url = AppConfiguration.listingURL(job.sourceUrl) { openURL(url) } else { store.error = "This opportunity doesn't have a valid employer link." } } label: { Label("View original listing", systemImage: "arrow.up.right.square").font(.subheadline) }.disabled(store.opportunity(id: jobId) == nil)
                        Text("Opening the listing hands off to your browser. It does not submit the application.").font(.caption).foregroundStyle(Pursuit.muted)
                    }.padding(24).frame(maxWidth: 700).frame(maxWidth: .infinity)
                }.safeAreaInset(edge: .bottom) { PrimaryButton(title: "Prepare my application", icon: "sparkles") { prepare = true }.disabled(!canUseDetails).padding(.horizontal, 22).padding(.vertical, 10).background(.regularMaterial) }
                    .refreshable { await loadDetail() }
                    .sheet(isPresented: $assistant) { AssistantView(job: job) }
                    .sheet(isPresented: $tracker) { ApplicationStatusView(job: job) }
                    .sheet(isPresented: $prepare) { PrepareView(job: job) }
                    .sheet(isPresented: $questions) { QuestionsView(jobId: job.id) }
                    .sheet(isPresented: $eligibility) { EligibilityReviewView(job: job) }
                    .sheet(isPresented: $edit) { EditOpportunityView(job: job) }
            } else {
                VStack(spacing: 20) {
                    detailLoadStatus
                    if !loadingDetail { ContentUnavailableView("Opportunity unavailable", systemImage: "magnifyingglass", description: Text("Return to your shortlist or retry loading this role.")) }
                }.padding(24)
            }
        }.pursuitPage().navigationTitle(job?.companyName ?? "Opportunity").navigationBarTitleDisplayMode(.inline)
            .task(id: "\(jobId):\(store.workspaceRevision)") { await loadDetail() }
    }
    @ViewBuilder private var detailLoadStatus: some View {
        if loadingDetail { ProgressView("Loading opportunity details…").accessibilityIdentifier("job.loading") }
        else if let detailError {
            VStack(alignment: .leading, spacing: 12) {
                Text("Could not load current job details").font(.headline).accessibilityIdentifier("job.detailError")
                Text(detailError).font(.subheadline)
                if let application = store.workspace.applications.first(where: { $0.jobId == jobId }) {
                    Text("Your application is still tracked: \(application.label).").font(.subheadline)
                    if let notes = application.notes, !notes.isEmpty { Text(notes).font(.caption) }
                }
                Button("Retry loading details") { Task { await loadDetail() } }.accessibilityIdentifier("job.retryDetail")
            }.cardSurface()
        }
    }
    private func loadDetail() async {
        loadingDetail = true; detailError = nil
        do { try await store.fetchJobDetails(id: jobId) }
        catch is CancellationError { return }
        catch { if Task.isCancelled { return }; detailError = error.localizedDescription }
        loadingDetail = false
    }
}

struct PrepareView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let job: Opportunity
    @State private var resumeId = ""
    @State private var variant = DocumentFocus.roleAligned.rawValue
    @State private var consent = false
    @State private var questions = false
    @State private var recovery = false
    var body: some View {
        NavigationStack {
            Form {
                Section { Text(job.title).font(.headline); Text(job.companyName).foregroundStyle(Pursuit.muted) }
                Section {
                    Picker("Source resume", selection: $resumeId) { Text("Choose a resume").tag(""); ForEach(store.workspace.resumes) { Text($0.label).tag($0.id) } }
                    Picker("Positioning", selection: $variant) { ForEach(DocumentFocus.allCases, id: \.rawValue) { Text($0.label).tag($0.rawValue) } }
                } header: { Text("Start with your experience") } footer: { Text("Tailoring follows this job's responsibilities and your confirmed experience. Career transition emphasizes transferable skills without inventing a new title, licence or qualification.") }
                Section {
                    Label("Tailored resume · PDF + Word", systemImage: "doc.text")
                    Label("Cover letter · PDF + Word", systemImage: "envelope")
                    Label("A separate, saved document version", systemImage: "clock.arrow.circlepath")
                }
                Section {
                    Toggle("Use AI to tailor my documents", isOn: $consent)
                    Text("This sends your selected resume, confirmed profile facts and the role to the server's configured AI provider. It uses API credits. Review the output for accuracy before applying.").font(.caption).foregroundStyle(Pursuit.muted)
                    PrimaryButton(title: "Create application pack", icon: "sparkles", busy: store.isBusy) { Task { await store.perform {
                        if try await store.prepare(job, resumeId: resumeId, variant: variant) { dismiss() }
                        else { questions = true }
                    } } }.disabled(resumeId.isEmpty || !consent || store.isBusy || (!store.isPreview && store.storageRecovery.blocksPreparation(jobId: job.id)))
                }
                Section("Saved work and recovery") {
                    if store.storageRecovery.blocksPreparation(jobId: job.id), !store.isPreview {
                        Text("Pending saves or an unavailable recovery check block new generation. Recover stored bytes first. For confirmed missing bytes only, recovery offers an explicit, cost-warned new-preparation choice; old operations are kept.").font(.subheadline)
                    }
                    if store.storageRecovery.newPreparationAllowances.values.contains(where: { $0.jobId == job.id }) {
                        Text("You allowed one new preparation attempt. Select your source and confirm AI use above. Paid API credits may be used; the old missing-byte operation remains unresolved.").font(.caption)
                    }
                    Button("Open saved file recovery") { consent = false; recovery = true }
                }
                if store.workspace.resumes.isEmpty { Text("First import a PDF or DOCX in Studio.").foregroundStyle(Pursuit.muted) }
            }.navigationTitle("Application studio").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
                .onAppear { resumeId = store.workspace.resumes.first?.id ?? "" }
                .sheet(isPresented: $questions) { QuestionsView(jobId: job.id) }
                .sheet(isPresented: $recovery) { StorageRecoveryView() }
                .task { await store.refreshRecoveryOperations() }
        }
    }
}
