import Foundation
import Combine

enum StorageKind: String, CaseIterable, Hashable {
    case resume, artifact
    var title: String { self == .resume ? "Resumes" : "Application documents" }
}

struct PendingStorageOperation: Decodable, Identifiable, Equatable {
    var id: String
    var state: String
    var filename: String
    var jobId: String?
    var canRecoverStoredBytes: Bool { state == "upload_pending" }
    func key(_ kind: StorageKind) -> String { "\(kind.rawValue):\(id)" }
}

enum RecoveredStoredFile {
    case resume(ResumeDocument)
    case artifact(Artifact)
}

struct FreshResumeUpload {
    // One explicit upload attempt keeps its identity across retries. Never
    // substitute the old operation ID: it isn't the original idempotency key.
    var idempotencyKey: UUID
    var filename: String
    var content: Data
    var label: String
    var roleFocus: String
}

@MainActor protocol StorageRecoveryService {
    func pendingOperations(_ kind: StorageKind, token: String) async throws -> [PendingStorageOperation]
    func recoverStoredBytes(_ kind: StorageKind, operation: PendingStorageOperation, token: String) async throws -> RecoveredStoredFile
    func uploadFreshResume(_ upload: FreshResumeUpload, token: String) async throws -> ResumeDocument
}

struct RecoveryListState {
    var operations: [PendingStorageOperation] = []
    var hasLoaded = false
    var isLoading = false
    var error: String?
}

/// Rehydrates from the authenticated server, not from another session's local
/// cache. Failed reads retain visible rows and fail closed before generation.
@MainActor final class StorageRecoveryController: ObservableObject {
    @Published private(set) var lists: [StorageKind: RecoveryListState] = [:]
    @Published private(set) var issues: [String: StorageRecoveryRequired] = [:]
    @Published private(set) var messages: [String: String] = [:]
    @Published private(set) var busy: Set<String> = []
    @Published private(set) var newPreparationAllowances: [String: PendingStorageOperation] = [:]
    private var epoch = UUID()
    private var requests: [StorageKind: UUID] = [:]

    func reset() {
        epoch = UUID(); requests = [:]; lists = [:]; issues = [:]; messages = [:]; busy = []
        newPreparationAllowances = [:]
    }
    func list(_ kind: StorageKind) -> RecoveryListState { lists[kind] ?? RecoveryListState() }
    var pendingCount: Int { lists.values.reduce(0) { $0 + $1.operations.count } }
    func blocksPreparation(jobId: String) -> Bool {
        let state = list(.artifact)
        return !state.hasLoaded || state.isLoading || state.error != nil || state.operations.contains {
            ($0.jobId == jobId || $0.jobId == nil) && !hasNewPreparationAllowance($0)
        }
    }
    func hasNewPreparationAllowance(_ operation: PendingStorageOperation) -> Bool {
        newPreparationAllowances[operation.key(.artifact)] == operation && hasConfirmedMissingArtifactBytes(operation)
    }
    private func hasConfirmedMissingArtifactBytes(_ operation: PendingStorageOperation) -> Bool {
        let issue = issues[operation.key(.artifact)]
        return issue?.code == "artifact_upload_bytes_required" && issue?.operationId == operation.id
    }
    func canAcknowledgeNewPreparation(_ operation: PendingStorageOperation) -> Bool {
        let state = list(.artifact)
        return state.hasLoaded && !state.isLoading && state.error == nil && state.operations.contains(operation)
            && operation.canRecoverStoredBytes && operation.jobId != nil && !busy.contains(operation.key(.artifact))
            && hasConfirmedMissingArtifactBytes(operation)
    }
    func acknowledgeNewPreparation(_ operation: PendingStorageOperation) throws {
        guard canAcknowledgeNewPreparation(operation) else {
            throw AppFailure.message("First confirm missing artifact bytes for this exact operation. Uncertain saves cannot be bypassed.")
        }
        newPreparationAllowances[operation.key(.artifact)] = operation
        messages[operation.key(.artifact)] = "One new preparation attempt is allowed for this job in this session. AI may consume paid API credits. Nothing has been generated. Return to Prepare, choose your source and confirm AI use. The old operation remains unresolved and retained."
    }
    func consumePreparationAllowances(jobId: String) throws {
        guard !blocksPreparation(jobId: jobId) else {
            throw AppFailure.message("Resolve pending saves or explicitly acknowledge each confirmed missing-byte operation before a new preparation.")
        }
        // One deliberate attempt only. An uncertain/failed POST never grants
        // an automatic retry, and these allowances never survive sign-out/relaunch.
        for (key, operation) in newPreparationAllowances where operation.jobId == jobId {
            messages[key] = "The one-attempt allowance was consumed. The old missing-byte operation is retained. Another new preparation requires a fresh explicit choice; no automatic retry is allowed."
        }
        newPreparationAllowances = newPreparationAllowances.filter { $0.value.jobId != jobId }
    }
    func record(_ recovery: StorageRecoveryRequired, kind: StorageKind, filename: String, jobId: String? = nil) {
        let operation = PendingStorageOperation(id: recovery.operationId, state: recovery.state ?? "upload_pending", filename: filename, jobId: jobId)
        var state = list(kind)
        if !state.operations.contains(where: { $0.id == operation.id }) { state.operations.append(operation) }
        lists[kind] = state; issues[operation.key(kind)] = recovery
        newPreparationAllowances[operation.key(kind)] = nil
    }
    func refresh(_ kind: StorageKind, service: any StorageRecoveryService, token: String) async {
        let savedEpoch = epoch
        let request = UUID(); requests[kind] = request
        var state = list(kind); state.isLoading = true; state.error = nil; lists[kind] = state
        do {
            let operations = try await service.pendingOperations(kind, token: token)
            guard savedEpoch == epoch, requests[kind] == request else { return }
            let previous = list(kind).operations
            let unchanged = Set(operations.filter { row in previous.contains { $0.id == row.id && $0.state == row.state && $0.jobId == row.jobId } }.map { $0.key(kind) })
            lists[kind] = RecoveryListState(operations: operations, hasLoaded: true)
            issues = issues.filter { !$0.key.hasPrefix(kind.rawValue + ":") || unchanged.contains($0.key) }
            if kind == .artifact {
                newPreparationAllowances = newPreparationAllowances.filter { operations.contains($0.value) && $0.value.canRecoverStoredBytes && issues[$0.key]?.bytesMissing == true }
            }
        } catch {
            guard savedEpoch == epoch, requests[kind] == request else { return }
            var failed = list(kind); failed.isLoading = false; failed.error = error.localizedDescription; lists[kind] = failed
        }
    }
    func recover(_ operation: PendingStorageOperation, kind: StorageKind, service: any StorageRecoveryService, token: String) async throws -> RecoveredStoredFile {
        let key = operation.key(kind)
        guard operation.canRecoverStoredBytes, list(kind).operations.contains(operation) else {
            throw AppFailure.message("Only a pending upload can be recovered here. No deletion or other file change was requested.")
        }
        guard !busy.contains(key) else { throw AppFailure.message("Recovery is already in progress.") }
        let savedEpoch = epoch
        busy.insert(key); messages[key] = nil
        newPreparationAllowances[key] = nil
        defer { if savedEpoch == epoch { busy.remove(key) } }
        do {
            let result = try await service.recoverStoredBytes(kind, operation: operation, token: token)
            guard savedEpoch == epoch else { throw CancellationError() }
            requests[kind] = UUID() // Ignore any older in-flight list read.
            var state = list(kind); state.operations.removeAll { $0.id == operation.id }; state.isLoading = false; lists[kind] = state
            issues[key] = nil
            messages[key] = "Recovered \(operation.filename) from stored bytes. No AI was called. Review the saved file; this does not confirm a complete application pack."
            return result
        } catch AppFailure.recoveryRequired(let recovery) {
            guard savedEpoch == epoch else { throw CancellationError() }
            issues[key] = recovery; messages[key] = recovery.userMessage
            throw AppFailure.recoveryRequired(recovery)
        } catch {
            guard savedEpoch == epoch else { throw CancellationError() }
            messages[key] = error.localizedDescription
            throw error
        }
    }
    func uploadFreshSource(_ upload: FreshResumeUpload, for operation: PendingStorageOperation, kind: StorageKind,
                           service: any StorageRecoveryService, token: String) async throws -> ResumeDocument {
        let key = operation.key(kind)
        guard issues[key]?.bytesMissing == true, list(kind).operations.contains(operation), !busy.contains(key) else {
            throw AppFailure.message("First check recovery to confirm whether stored bytes are missing.")
        }
        let savedEpoch = epoch
        busy.insert(key)
        defer { if savedEpoch == epoch { busy.remove(key) } }
        do {
            let resume = try await service.uploadFreshResume(upload, token: token)
            guard savedEpoch == epoch else { throw CancellationError() }
            messages[key] = "Fresh source uploaded as a new private resume. The old operation remains unresolved; no artifact was regenerated, overwritten or deleted."
            return resume
        } catch AppFailure.recoveryRequired(let recovery) {
            guard savedEpoch == epoch else { throw CancellationError() }
            record(recovery, kind: .resume, filename: upload.filename)
            throw AppFailure.recoveryRequired(recovery)
        }
    }
}

extension APIClient: StorageRecoveryService {
    func pendingOperations(_ kind: StorageKind, token: String) async throws -> [PendingStorageOperation] {
        let rows = try await decode([PendingStorageOperation].self, path: "\(kind.rawValue)-operations", token: token)
        // These endpoints cap their unpaginated response at 200. A full page
        // cannot prove that another job has no pending save, so fail closed.
        guard rows.count < 200 else {
            throw AppFailure.message("The recovery list may be incomplete at the service's 200-operation limit. Preparation and recovery writes are paused until the service can return a complete list. No files were changed.")
        }
        guard Set(rows.map(\.id)).count == rows.count,
              rows.allSatisfy({ UUID(uuidString: $0.id) != nil && !$0.filename.isEmpty && $0.filename.count <= 180
                  && (kind != .artifact || $0.jobId.flatMap(UUID.init(uuidString:)) != nil) }) else {
            throw AppFailure.message("The service returned invalid recovery metadata. No files were changed.")
        }
        return rows
    }
    func recoverStoredBytes(_ kind: StorageKind, operation: PendingStorageOperation, token: String) async throws -> RecoveredStoredFile {
        guard operation.canRecoverStoredBytes, UUID(uuidString: operation.id) != nil else {
            throw AppFailure.message("Only pending uploads can be recovered here. No deletion was requested.")
        }
        // Recheck the operation rather than trusting an old screen or retry_path.
        let current = try await pendingOperations(kind, token: token)
        guard current.contains(where: { $0.id == operation.id && $0.canRecoverStoredBytes }) else {
            throw AppFailure.message("This operation changed or is no longer pending. Refresh recovery; no recovery write was sent.")
        }
        if kind == .resume {
            let resume = try await decode(ResumeDocument.self, path: "resumes/\(operation.id)/recover", method: "POST", token: token, body: [:])
            guard resume.id == operation.id else { throw AppFailure.message("Recovery returned a different resume.") }
            return .resume(resume)
        }
        let artifact = try await decode(Artifact.self, path: "artifact-operations/\(operation.id)/recover", method: "POST", token: token, body: [:])
        guard artifact.id == operation.id, artifact.jobId == operation.jobId else { throw AppFailure.message("Recovery returned a different document.") }
        return .artifact(artifact)
    }
    func uploadFreshResume(_ upload: FreshResumeUpload, token: String) async throws -> ResumeDocument {
        guard !upload.content.isEmpty, upload.content.count <= 8 * 1024 * 1024,
              ["pdf", "docx"].contains(URL(fileURLWithPath: upload.filename).pathExtension.lowercased()) else {
            throw AppFailure.message("Choose a PDF or DOCX smaller than 8 MB.")
        }
        let data = try await request("resumes", method: "POST", token: token,
                                     body: ["filename": upload.filename, "content_base64": upload.content.base64EncodedString(), "label": upload.label, "role_focus": upload.roleFocus],
                                     idempotencyKey: upload.idempotencyKey.uuidString)
        return try Self.decoder.decode(ResumeDocument.self, from: data)
    }
}
