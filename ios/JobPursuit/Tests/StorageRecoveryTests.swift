import XCTest
@testable import JobPursuit

@MainActor private final class MemoryRecoveryService: StorageRecoveryService {
    var rows: [StorageKind: [PendingStorageOperation]] = [:]
    var failRead: Set<StorageKind> = []
    var missingBytes: Set<String> = []
    var reads: [StorageKind] = []
    var recoveries: [String] = []
    var uploads: [FreshResumeUpload] = []
    var onRead: (() -> Void)?
    var onRecover: (() -> Void)?
    var failUpload: StorageRecoveryRequired?
    func pendingOperations(_ kind: StorageKind, token: String) async throws -> [PendingStorageOperation] {
        reads.append(kind); onRead?()
        if failRead.contains(kind) { throw AppFailure.message("Offline injected failure") }
        return rows[kind] ?? []
    }
    func recoverStoredBytes(_ kind: StorageKind, operation: PendingStorageOperation, token: String) async throws -> RecoveredStoredFile {
        recoveries.append(operation.key(kind)); onRecover?()
        if missingBytes.contains(operation.id) {
            throw AppFailure.recoveryRequired(StorageRecoveryRequired(code: "\(kind.rawValue)_upload_bytes_required", operationId: operation.id, message: "Missing source bytes"))
        }
        rows[kind]?.removeAll { $0.id == operation.id }
        if kind == .resume { return .resume(ResumeDocument(id: operation.id, label: "Recovered", originalFilename: operation.filename)) }
        return .artifact(Artifact(id: operation.id, jobId: operation.jobId, kind: "resume", filename: operation.filename))
    }
    func uploadFreshResume(_ upload: FreshResumeUpload, token: String) async throws -> ResumeDocument {
        uploads.append(upload)
        if let failUpload { throw AppFailure.recoveryRequired(failUpload) }
        return ResumeDocument(id: upload.idempotencyKey.uuidString, label: upload.label, originalFilename: upload.filename)
    }
}

final class StorageRecoveryTests: XCTestCase {
    private let resume = PendingStorageOperation(id: "10000000-0000-4000-8000-000000000001", state: "upload_pending", filename: "source.pdf")
    private let artifact = PendingStorageOperation(id: "20000000-0000-4000-8000-000000000001", state: "upload_pending", filename: "tailored.pdf", jobId: "j1")

    @MainActor func testPendingOperationsRestoreIntoNewControllerWithoutLocalPersistence() async {
        let service = MemoryRecoveryService(); service.rows = [.resume: [resume], .artifact: [artifact]]
        for _ in 0..<2 {
            let controller = StorageRecoveryController()
            XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
            await controller.refresh(.resume, service: service, token: "offline")
            await controller.refresh(.artifact, service: service, token: "offline")
            XCTAssertEqual(controller.pendingCount, 2)
            XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
            XCTAssertFalse(controller.blocksPreparation(jobId: "other-job"))
        }
        XCTAssertEqual(service.reads, [.resume, .artifact, .resume, .artifact])
        XCTAssertTrue(service.recoveries.isEmpty); XCTAssertTrue(service.uploads.isEmpty)
    }
    @MainActor func testSuccessfulStoredRecoveryRemovesPendingRecordWithoutUploadOrGeneration() async throws {
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact]
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        let result = try await controller.recover(artifact, kind: .artifact, service: service, token: "offline")
        guard case .artifact(let file) = result else { return XCTFail("Expected recovered artifact") }
        XCTAssertEqual(file.id, artifact.id)
        XCTAssertFalse(controller.blocksPreparation(jobId: "j1"))
        XCTAssertTrue(try XCTUnwrap(controller.messages[artifact.key(.artifact)]).contains("does not confirm a complete application pack"))
        let relaunched = StorageRecoveryController()
        await relaunched.refresh(.artifact, service: service, token: "offline")
        XCTAssertEqual(relaunched.pendingCount, 0)
        XCTAssertEqual(service.recoveries.count, 1); XCTAssertTrue(service.uploads.isEmpty)
    }
    @MainActor func testFailedRefreshRetainsRowsAndBlocksPreparation() async {
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact]
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        service.failRead.insert(.artifact)
        await controller.refresh(.artifact, service: service, token: "offline")
        await controller.refresh(.resume, service: service, token: "offline")
        XCTAssertEqual(controller.list(.artifact).operations, [artifact])
        XCTAssertNotNil(controller.list(.artifact).error)
        XCTAssertTrue(controller.list(.resume).hasLoaded)
        XCTAssertTrue(controller.blocksPreparation(jobId: "unrelated-job"))
    }
    @MainActor func testPendingDeletionNeverCallsRecoveryService() async {
        var deletion = resume; deletion.state = "delete_pending"
        let service = MemoryRecoveryService(); service.rows[.resume] = [deletion]
        let controller = StorageRecoveryController()
        await controller.refresh(.resume, service: service, token: "offline")
        do { _ = try await controller.recover(deletion, kind: .resume, service: service, token: "offline"); XCTFail("Must not delete") }
        catch { XCTAssertTrue(error.localizedDescription.contains("No deletion")) }
        XCTAssertTrue(service.recoveries.isEmpty)
        XCTAssertEqual(controller.pendingCount, 1)
    }
    @MainActor func testMissingBytesAllowExplicitFreshUploadButKeepOldOperation() async throws {
        let service = MemoryRecoveryService(); service.rows[.resume] = [resume]; service.missingBytes.insert(resume.id)
        let controller = StorageRecoveryController()
        let upload = FreshResumeUpload(idempotencyKey: UUID(), filename: "fresh.pdf", content: Data("fixture".utf8), label: "Fresh source", roleFocus: "")
        await controller.refresh(.resume, service: service, token: "offline")
        do { _ = try await controller.uploadFreshSource(upload, for: resume, kind: .resume, service: service, token: "offline"); XCTFail("Must establish missing bytes first") } catch {}
        XCTAssertTrue(service.uploads.isEmpty)
        do { _ = try await controller.recover(resume, kind: .resume, service: service, token: "offline"); XCTFail("Bytes are missing") } catch {}
        XCTAssertEqual(controller.issues[resume.key(.resume)]?.bytesMissing, true)
        let result = try await controller.uploadFreshSource(upload, for: resume, kind: .resume, service: service, token: "offline")
        XCTAssertNotEqual(result.id, resume.id)
        XCTAssertEqual(controller.list(.resume).operations, [resume])
        XCTAssertEqual(service.uploads.map(\.idempotencyKey), [upload.idempotencyKey])
        XCTAssertTrue(try XCTUnwrap(controller.messages[resume.key(.resume)]).contains("old operation remains unresolved"))
    }
    @MainActor func testResetDiscardsLateListAndRecoveryResults() async throws {
        let service = MemoryRecoveryService(); service.rows[.resume] = [resume]
        let controller = StorageRecoveryController()
        service.onRead = { controller.reset() }
        await controller.refresh(.resume, service: service, token: "previous-session")
        XCTAssertFalse(controller.list(.resume).hasLoaded); XCTAssertEqual(controller.pendingCount, 0)
        service.onRead = nil
        await controller.refresh(.resume, service: service, token: "current-session")
        service.onRecover = { controller.reset() }
        do { _ = try await controller.recover(resume, kind: .resume, service: service, token: "previous-session"); XCTFail("Must discard old session result") }
        catch is CancellationError {} catch { XCTFail("Expected cancellation") }
        XCTAssertTrue(controller.messages.isEmpty); XCTAssertTrue(controller.busy.isEmpty)
        XCTAssertEqual(controller.pendingCount, 0)
    }
    @MainActor func testUncertainFreshUploadRetainsBothOperationsAndSameAttemptIdentity() async throws {
        let service = MemoryRecoveryService(); service.rows[.resume] = [resume]; service.missingBytes.insert(resume.id)
        let controller = StorageRecoveryController()
        await controller.refresh(.resume, service: service, token: "offline")
        do { _ = try await controller.recover(resume, kind: .resume, service: service, token: "offline") } catch {}
        let upload = FreshResumeUpload(idempotencyKey: UUID(), filename: "fresh.pdf", content: Data("fixture".utf8), label: "Fresh source", roleFocus: "")
        service.failUpload = StorageRecoveryRequired(code: "resume_recovery_required", operationId: "30000000-0000-4000-8000-000000000001", state: "upload_pending", message: "Uncertain upload")
        for _ in 0..<2 {
            do { _ = try await controller.uploadFreshSource(upload, for: resume, kind: .resume, service: service, token: "offline"); XCTFail("Uncertain upload") } catch {}
        }
        XCTAssertEqual(controller.pendingCount, 2)
        XCTAssertEqual(Set(service.uploads.map(\.idempotencyKey)), [upload.idempotencyKey])
    }
    @MainActor func testResetClearsAllSessionRecoveryState() async {
        let service = MemoryRecoveryService(); service.rows[.resume] = [resume]; service.missingBytes.insert(resume.id)
        let controller = StorageRecoveryController()
        await controller.refresh(.resume, service: service, token: "offline")
        do { _ = try await controller.recover(resume, kind: .resume, service: service, token: "offline") } catch {}
        controller.reset()
        XCTAssertTrue(controller.lists.isEmpty); XCTAssertTrue(controller.issues.isEmpty)
        XCTAssertTrue(controller.messages.isEmpty); XCTAssertTrue(controller.busy.isEmpty)
    }
    @MainActor func testFreshSourceDoesNotAuthorizeAIUntilExplicitMissingOperationAcknowledgment() async throws {
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact]; service.missingBytes.insert(artifact.id)
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        XCTAssertThrowsError(try controller.acknowledgeNewPreparation(artifact))
        do { _ = try await controller.recover(artifact, kind: .artifact, service: service, token: "offline") } catch {}
        let upload = FreshResumeUpload(idempotencyKey: UUID(), filename: "fresh.pdf", content: Data("fixture".utf8), label: "Fresh", roleFocus: "")
        _ = try await controller.uploadFreshSource(upload, for: artifact, kind: .artifact, service: service, token: "offline")
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        XCTAssertThrowsError(try controller.consumePreparationAllowances(jobId: "j1"))
        try controller.acknowledgeNewPreparation(artifact)
        XCTAssertFalse(controller.blocksPreparation(jobId: "j1"))
        XCTAssertEqual(service.recoveries.count, 1); XCTAssertEqual(service.uploads.count, 1)
        XCTAssertEqual(controller.list(.artifact).operations, [artifact])
    }
    @MainActor func testNewPreparationAllowanceIsPerOperationAndConsumedByOneAttempt() async throws {
        var second = artifact; second.id = "20000000-0000-4000-8000-000000000002"
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact, second]; service.missingBytes = [artifact.id, second.id]
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        for op in [artifact, second] { do { _ = try await controller.recover(op, kind: .artifact, service: service, token: "offline") } catch {} }
        try controller.acknowledgeNewPreparation(artifact)
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        try controller.acknowledgeNewPreparation(second)
        await controller.refresh(.artifact, service: service, token: "offline")
        XCTAssertFalse(controller.blocksPreparation(jobId: "j1"))
        try controller.consumePreparationAllowances(jobId: "j1")
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        XCTAssertTrue(controller.newPreparationAllowances.isEmpty)
        XCTAssertEqual(controller.list(.artifact).operations, [artifact, second])
        XCTAssertTrue(service.uploads.isEmpty) // Acknowledgment itself has no service mutation.
    }
    @MainActor func testNewPreparationAllowanceCannotBypassReadFailureChangedOperationOrSessionReset() async throws {
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact]; service.missingBytes.insert(artifact.id)
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        do { _ = try await controller.recover(artifact, kind: .artifact, service: service, token: "offline") } catch {}
        try controller.acknowledgeNewPreparation(artifact)
        service.failRead.insert(.artifact)
        await controller.refresh(.artifact, service: service, token: "offline")
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        XCTAssertThrowsError(try controller.consumePreparationAllowances(jobId: "j1"))
        service.failRead = []
        var changed = artifact; changed.state = "delete_pending"; service.rows[.artifact] = [changed]
        await controller.refresh(.artifact, service: service, token: "offline")
        XCTAssertTrue(controller.newPreparationAllowances.isEmpty)
        XCTAssertThrowsError(try controller.acknowledgeNewPreparation(changed))
        service.rows[.artifact] = [artifact]
        await controller.refresh(.artifact, service: service, token: "offline")
        XCTAssertThrowsError(try controller.acknowledgeNewPreparation(artifact))
        do { _ = try await controller.recover(artifact, kind: .artifact, service: service, token: "offline") } catch {}
        try controller.acknowledgeNewPreparation(artifact)
        controller.reset()
        await controller.refresh(.artifact, service: service, token: "new-session")
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        XCTAssertFalse(controller.canAcknowledgeNewPreparation(artifact))
        XCTAssertTrue(controller.newPreparationAllowances.isEmpty)
    }
    @MainActor func testUncertainPartialSaveNeverAllowsNewPreparationAcknowledgment() async {
        let service = MemoryRecoveryService(); service.rows[.artifact] = [artifact]
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "offline")
        controller.record(StorageRecoveryRequired(code: "artifact_recovery_required", operationId: artifact.id, state: "upload_pending", message: "Save uncertain"), kind: .artifact, filename: artifact.filename, jobId: artifact.jobId)
        XCTAssertTrue(controller.blocksPreparation(jobId: "j1"))
        XCTAssertThrowsError(try controller.acknowledgeNewPreparation(artifact))
        XCTAssertTrue(service.recoveries.isEmpty); XCTAssertTrue(service.uploads.isEmpty)
        controller.record(StorageRecoveryRequired(code: "resume_upload_bytes_required", operationId: artifact.id, message: "Wrong operation kind"), kind: .artifact, filename: artifact.filename, jobId: artifact.jobId)
        XCTAssertThrowsError(try controller.acknowledgeNewPreparation(artifact))
    }
}
