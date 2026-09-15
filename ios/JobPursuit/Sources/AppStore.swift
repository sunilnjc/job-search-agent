import Foundation
import SwiftUI
import Combine

@MainActor final class AppStore: ObservableObject {
    @Published var workspace = Workspace.empty
    @Published var session: Session?
    @Published var isPreview = false
    @Published var isBusy = false
    @Published var error: String?
    @Published var notice: String?
    @Published var configuration: AppConfiguration
    @Published var chat: [ChatLine] = []
    @Published var showConfiguration = false
    @Published private(set) var jobDetails: [String: Opportunity] = [:]
    @Published private(set) var workspaceRevision = 0
    @Published private(set) var operationWarnings: [String: String] = [:]
    let storageRecovery = StorageRecoveryController()
    private var recoveryObservation: AnyCancellable?
    #if DEBUG
    private let previewRecovery = PreviewRecoveryService()
    #endif
    private var exportedFiles: [URL] = []
    private var operations = 0
    private var refreshTask: Task<Session, Error>?
    private var sessionEpoch = UUID()
    private let isUITesting: Bool
    private static var offlineConfiguration: AppConfiguration {
        AppConfiguration(apiBase: "", supabaseUrl: "", publishableKey: "")
    }
    init(isUITesting: Bool = ProcessInfo.processInfo.arguments.contains("--uitesting")) {
        self.isUITesting = isUITesting
        configuration = isUITesting ? Self.offlineConfiguration : AppConfiguration.load()
        recoveryObservation = storageRecovery.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
    }
    var client: APIClient { APIClient(configuration: isUITesting ? Self.offlineConfiguration : configuration) }
    var isSignedIn: Bool { session != nil || isPreview }

    func start() async {
        if ProcessInfo.processInfo.arguments.contains("--preview") { enterPreview(); return }
        // Tests must never load or delete the real device's saved login.
        if isUITesting { return }
        let saved = SecureSession.load()
        if saved?.authority == configuration.supabaseUrl { session = saved }
        else { SecureSession.clear() }
        if session != nil { await perform { try await self.reload() } }
    }
    func enterPreview() {
        sessionEpoch = UUID(); jobDetails = [:]; workspaceRevision += 1
        operationWarnings = [:]; storageRecovery.reset()
        isPreview = true; workspace = .preview; chat = []; error = nil
        #if DEBUG
        if isUITesting && ProcessInfo.processInfo.arguments.contains("--preview-missing-job") {
            workspace.jobs.removeAll { $0.id == "preview-2" }
        }
        #endif
    }
    func signOut() async {
        let old = session
        let api = client
        clearSession()
        if let old, !isUITesting { _ = try? await api.request("logout", method: "POST", token: old.accessToken, auth: true) }
    }
    private func clearSession() {
        sessionEpoch = UUID()
        refreshTask?.cancel(); refreshTask = nil
        if !isUITesting { SecureSession.clear() }
        session = nil; isPreview = false; workspace = .empty; chat = []; notice = nil
        jobDetails = [:]; workspaceRevision += 1
        operationWarnings = [:]; storageRecovery.reset()
        for file in exportedFiles { try? FileManager.default.removeItem(at: file) }
        exportedFiles = []
    }
    func setConfiguration(_ new: AppConfiguration) {
        clearSession(); configuration = new
        if !isUITesting { new.save() }
    }
    func sendCode(email: String) async throws {
        try requireLive()
        guard configuration.isReady else { showConfiguration = true; throw AppFailure.message("Connect the app to your backend before signing in.") }
        _ = try await client.request("otp", method: "POST", body: ["email": email, "create_user": true], auth: true)
    }
    func verifyCode(email: String, code: String) async throws {
        try requireLive()
        var verified = try await client.decode(Session.self, path: "verify", method: "POST", body: ["email": email, "token": code, "type": "email"], auth: true)
        if verified.expiresAt == nil { verified.expiresAt = Date().timeIntervalSince1970 + (verified.expiresIn ?? 3600) }
        verified.authority = configuration.supabaseUrl
        sessionEpoch = UUID()
        try SecureSession.save(verified); session = verified; isPreview = false
        try await reload()
    }
    func token() async throws -> String {
        try requireLive()
        guard let current = session else { throw AppFailure.message("Sign in to use your private workspace.") }
        if (current.expiresAt ?? 0) - Date().timeIntervalSince1970 > 90 { return current.accessToken }
        if let refreshTask { return try await refreshTask.value.accessToken }
        let api = client
        let epoch = sessionEpoch
        let task = Task<Session, Error> {
            var next = try await api.decode(Session.self, path: "token", method: "POST", body: ["refresh_token": current.refreshToken], auth: true)
            if next.expiresAt == nil { next.expiresAt = Date().timeIntervalSince1970 + (next.expiresIn ?? 3600) }
            next.authority = api.configuration.supabaseUrl
            return next
        }
        refreshTask = task
        defer { refreshTask = nil }
        let next = try await task.value
        guard epoch == sessionEpoch else { throw CancellationError() }
        try SecureSession.save(next); session = next
        return next.accessToken
    }
    func perform(_ action: () async throws -> Void) async {
        operations += 1; isBusy = true
        defer { operations -= 1; isBusy = operations > 0 }
        do { try await action() } catch { self.error = error.localizedDescription }
    }
    func requireLive() throws {
        if isUITesting { throw AppFailure.message("Automated tests use an offline workspace and cannot contact live services.") }
        if isPreview { throw AppFailure.message("This is a design preview with fictional data. Sign in to import documents, use AI, and save real applications.") }
    }
    func reload() async throws {
        if isPreview { return }
        try requireLive()
        let epoch = sessionEpoch
        let value = try await client.decode(Workspace.self, path: "bootstrap", token: token())
        guard epoch == sessionEpoch else { throw CancellationError() }
        workspace = value
        // A fresh bootstrap may reflect edits made by another client. Do not
        // retain stale descriptions or eligibility reviews across that boundary.
        jobDetails = [:]; workspaceRevision += 1
        await refreshRecoveryOperations()
    }
    func opportunity(id: String) -> Opportunity? { jobDetails[id] ?? workspace.jobs.first { $0.id == id } }
    func fetchJobDetails(id: String) async throws {
        if isPreview {
            guard opportunity(id: id) != nil else { throw AppFailure.message("Job details are unavailable in this fictional preview. Your tracked application remains visible.") }
            return
        }
        // Older servers sent full jobs in bootstrap and have no detail route.
        if workspace.capabilities?.jobDetailFetch != true, workspace.jobs.contains(where: { $0.id == id }) { return }
        try requireLive()
        let epoch = sessionEpoch
        let revision = workspaceRevision
        let job = try await client.jobDetail(id: id, token: token())
        try Task.checkCancellation()
        guard epoch == sessionEpoch, revision == workspaceRevision else { throw CancellationError() }
        cacheJob(job)
    }
    private func cacheJob(_ job: Opportunity) {
        jobDetails[job.id] = job
        if let index = workspace.jobs.firstIndex(where: { $0.id == job.id }) {
            var summary = job
            if workspace.capabilities?.jobDetailFetch == true { summary.description = nil }
            workspace.jobs[index] = summary
        }
        // Do not insert out-of-page tracker jobs into Discover's loaded page.
    }
    func reviewEligibility(jobId: String, draft: EligibilityReviewDraft) async throws {
        try requireLive()
        guard workspace.capabilities?.supportsEligibilityReview == true else { throw AppFailure.message("This server does not yet support job-specific eligibility reviews.") }
        let epoch = sessionEpoch
        let job = try await client.reviewEligibility(jobId: jobId, draft: draft, token: token())
        guard epoch == sessionEpoch else { throw CancellationError() }
        cacheJob(job); workspaceRevision += 1
        notice = "Eligibility self-report saved. It is not independently verified."
    }
    func updateJob(_ edits: OpportunityEdits) async throws {
        try requireLive()
        let epoch = sessionEpoch
        let job = try await client.updateJob(edits, token: token())
        guard epoch == sessionEpoch else { throw CancellationError() }
        cacheJob(job); workspaceRevision += 1
        notice = "Opportunity updated. Review eligibility and analyze the current requirements again."
    }
    func saveProfile(name: String, location: String, phone: String, facts: String, background: CareerBackground, roles: String, locations: String, sponsorship: Bool, authorization: String) async throws {
        try requireLive()
        let access = try await token()
        func split(_ v: String) -> [String] { v.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty } }
        try await client.saveProfileAndPreferences(
            profile: ["display_name": name, "base_location": location, "phone": phone, "career_text": facts, "career_background": background.requestBody],
            preferences: ["target_titles": split(roles), "preferred_locations": split(locations), "preferred_regions": workspace.preferences?.preferredRegions ?? [], "remote_preference": workspace.preferences?.remotePreference ?? "open", "sponsorship_required": sponsorship, "work_authorization_notes": authorization, "minimum_match_score": workspace.preferences?.minimumMatchScore ?? 7],
            onboardingCompletedAt: workspace.profile?.onboardingCompletedAt, token: access)
        try await reload(); notice = "Profile and preferences saved."
    }
    func importJob(url: String, title: String, company: String, location: String, description: String) async throws {
        try requireLive()
        _ = try await client.request("jobs", method: "POST", token: token(), body: ["source_url": url, "title": title, "company_name": company, "location_text": location, "description": description])
        try await reload(); notice = "Opportunity added to your workspace."
    }
    func upload(url: URL, label: String, roleFocus: String) async throws {
        try requireLive()
        let granted = url.startAccessingSecurityScopedResource()
        defer { if granted { url.stopAccessingSecurityScopedResource() } }
        let values = try url.resourceValues(forKeys: [.fileSizeKey])
        guard (values.fileSize ?? 0) <= 8 * 1024 * 1024 else { throw AppFailure.message("Choose a PDF or DOCX smaller than 8 MB.") }
        let content = try Data(contentsOf: url)
        _ = try await client.uploadFreshResume(FreshResumeUpload(idempotencyKey: UUID(), filename: url.lastPathComponent, content: content,
                                                               label: label.isEmpty ? url.deletingPathExtension().lastPathComponent : label, roleFocus: roleFocus), token: token())
        try await reload(); notice = "Resume stored in your private workspace."
    }
    func rank(_ job: Opportunity) async throws -> Bool {
        try requireLive()
        let epoch = sessionEpoch
        do {
            let result = try await client.decode(Opportunity.self, path: "jobs/\(job.id)/rank", method: "POST", token: token(), body: [:])
            guard epoch == sessionEpoch else { throw CancellationError() }
            var warning = result.savedSyncWarning
            do { try await reload() } catch {
                guard epoch == sessionEpoch else { throw CancellationError() }
                cacheJob(result)
                warning = (warning.map { $0 + " " } ?? "") + "Ranking saved, but the workspace could not refresh. Refresh before requesting another ranking."
            }
            operationWarnings[job.id] = warning
            notice = warning ?? "Match analysis updated."
            return workspace.pendingQuestions.contains { $0.jobId == job.id }
        } catch AppFailure.inputRequired(let message, _) {
            guard epoch == sessionEpoch else { throw CancellationError() }
            try await reload(); notice = message
            return true
        }
    }
    func resumeText(_ resume: ResumeDocument) async throws -> String {
        try requireLive()
        return try await client.decode(ExtractedResume.self, path: "resumes/\(resume.id)/text", token: token()).text
    }
    func confirmImportedFacts(_ text: String) async throws {
        try requireLive()
        let profile = workspace.profile
        let current = profile?.careerText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let confirmed = current.isEmpty ? text : current + "\n\n" + text
        _ = try await client.request("profile", method: "PUT", token: token(), body: ["display_name": profile?.displayName ?? "", "base_location": profile?.baseLocation ?? "", "phone": profile?.phone ?? "", "career_text": confirmed])
        try await reload(); notice = "Imported facts confirmed. AI can now use them as evidence."
    }
    func prepare(_ job: Opportunity, resumeId: String, variant: String) async throws -> Bool {
        try requireLive()
        // Recheck durable intents immediately before a new billable operation,
        // including after relaunch or when a different client saved documents.
        await refreshRecoveryOperations()
        guard !storageRecovery.blocksPreparation(jobId: job.id) else {
            throw AppFailure.message("Check Saved file recovery before preparing again. Pending or unavailable recovery records must not trigger replacement AI generation.")
        }
        let epoch = sessionEpoch
        do {
            try storageRecovery.consumePreparationAllowances(jobId: job.id)
            let result = try await client.decode(PreparedDocuments.self, path: "jobs/\(job.id)/prepare", method: "POST", token: token(), body: ["resume_id": resumeId, "variant": variant])
            guard epoch == sessionEpoch else { throw CancellationError() }
            var warning = result.savedSyncWarning
            do { try await reload() } catch {
                guard epoch == sessionEpoch else { throw CancellationError() }
                retainArtifacts(result.artifacts)
                warning = (warning.map { $0 + " " } ?? "") + "Documents saved, but the workspace could not refresh. Review them in Studio; do not regenerate to refresh the list."
            }
            operationWarnings[job.id] = warning
            let needsInput = !(result.questions ?? []).isEmpty || workspace.pendingQuestions.contains { $0.jobId == job.id }
            notice = warning ?? (needsInput ? "Draft documents are saved. Resolve the open questions before using them." : "Your documents are ready in Studio. Review the facts before using them.")
            return !needsInput
        } catch AppFailure.recoveryRequired(let recovery) {
            guard epoch == sessionEpoch else { throw CancellationError() }
            storageRecovery.record(recovery, kind: .artifact, filename: "Application document", jobId: job.id)
            retainArtifacts(recovery.savedArtifacts ?? [])
            operationWarnings[job.id] = recovery.userMessage
            throw AppFailure.recoveryRequired(recovery)
        } catch AppFailure.inputRequired(let message, _) {
            guard epoch == sessionEpoch else { throw CancellationError() }
            try await reload()
            notice = message
            return false
        }
    }
    private func retainArtifacts(_ artifacts: [Artifact]) {
        for artifact in artifacts {
            if let index = workspace.artifacts.firstIndex(where: { $0.id == artifact.id }) { workspace.artifacts[index] = artifact }
            else { workspace.artifacts.append(artifact) }
        }
    }
    var isRecoveryPreview: Bool {
        #if DEBUG
        return isUITesting && isPreview && ProcessInfo.processInfo.arguments.contains("--preview-recovery")
        #else
        return false
        #endif
    }
    private func recoveryAccess() async throws -> (any StorageRecoveryService, String) {
        #if DEBUG
        if isRecoveryPreview { return (previewRecovery, "offline-fixture") }
        #endif
        try requireLive()
        return (client, try await token())
    }
    func refreshRecoveryOperations() async {
        if isPreview && !isRecoveryPreview { return }
        do {
            let (service, access) = try await recoveryAccess()
            let epoch = sessionEpoch
            await storageRecovery.refresh(.resume, service: service, token: access)
            guard epoch == sessionEpoch else { return }
            await storageRecovery.refresh(.artifact, service: service, token: access)
        } catch { self.error = error.localizedDescription }
    }
    func recoverStoredFile(_ operation: PendingStorageOperation, kind: StorageKind) async throws {
        let (service, access) = try await recoveryAccess()
        let epoch = sessionEpoch
        let file = try await storageRecovery.recover(operation, kind: kind, service: service, token: access)
        guard epoch == sessionEpoch else { throw CancellationError() }
        switch file {
        case .artifact(let artifact): retainArtifacts([artifact])
        case .resume(let resume): retainResume(resume)
        }
        notice = storageRecovery.messages[operation.key(kind)]
    }
    func acknowledgeNewPreparation(_ operation: PendingStorageOperation) throws {
        if !isRecoveryPreview { try requireLive(); guard session != nil else { throw AppFailure.message("Sign in before choosing a new preparation.") } }
        try storageRecovery.acknowledgeNewPreparation(operation)
    }
    func uploadFreshRecoverySource(_ upload: FreshResumeUpload, operation: PendingStorageOperation, kind: StorageKind) async throws {
        let (service, access) = try await recoveryAccess()
        let epoch = sessionEpoch
        let resume = try await storageRecovery.uploadFreshSource(upload, for: operation, kind: kind, service: service, token: access)
        guard epoch == sessionEpoch else { throw CancellationError() }
        retainResume(resume)
        notice = storageRecovery.messages[operation.key(kind)]
    }
    private func retainResume(_ resume: ResumeDocument) {
        if let index = workspace.resumes.firstIndex(where: { $0.id == resume.id }) { workspace.resumes[index] = resume }
        else { workspace.resumes.append(resume) }
    }
    func updateApplication(jobId: String, status: String, notes: String) async throws {
        try requireLive()
        _ = try await client.request("applications", method: "POST", token: token(), body: ["job_id": jobId, "status": status, "notes": notes])
        try await reload(); notice = "Tracker updated."
    }
    func ask(_ message: String, mode: String, jobId: String?) async throws -> Bool {
        try requireLive()
        var body: [String: Any] = ["message": message, "mode": mode, "history": chat.suffix(10).map { ["role": $0.role, "content": $0.content] }]
        if let jobId { body["job_id"] = jobId }
        if let resume = workspace.resumes.first { body["resume_id"] = resume.id }
        let line = ChatLine(role: "user", content: message)
        let epoch = sessionEpoch
        chat.append(line)
        do {
            let reply = try await client.decode(ChatReply.self, path: "chat", method: "POST", token: token(), body: body)
            guard epoch == sessionEpoch else { throw CancellationError() }
            chat.append(ChatLine(role: "assistant", content: reply.reply, evidence: reply.evidence))
            do { try await reload() } catch { notice = "Answer received. Refresh to load any new follow-up questions." }
            return !reply.questions.isEmpty
        } catch AppFailure.inputRequired(let message, _) {
            guard epoch == sessionEpoch else { throw CancellationError() }
            try await reload(); notice = message
            chat.append(ChatLine(role: "assistant", content: message))
            return true
        } catch { chat.removeAll { $0.id == line.id }; throw error }
    }
    func answer(_ question: CandidateQuestion, text: String, remember: Bool) async throws {
        try requireLive()
        _ = try await client.request("questions/\(question.id)/answer", method: "POST", token: token(), body: ["answer": text, "remember": remember])
        try await reload(); notice = remember ? "Answer confirmed and remembered." : "Answer saved for this question."
    }
    func download(id: String, filename: String, isResume: Bool) async throws -> URL {
        try requireLive()
        let data = try await client.request("\(isResume ? "resumes" : "artifacts")/\(id)/download", token: token())
        let safeName = URL(fileURLWithPath: filename).lastPathComponent
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("JobPursuit-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
        let file = folder.appendingPathComponent(safeName)
        try data.write(to: file, options: .completeFileProtection)
        exportedFiles.append(folder)
        return file
    }
}
