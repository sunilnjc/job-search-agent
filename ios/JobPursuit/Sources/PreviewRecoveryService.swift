#if DEBUG
import Foundation

/// Opt-in UI fixtures only. No URLSession, credentials, defaults or disk writes.
/// Relaunch presents pending server-shaped data again, exercising list hydration.
@MainActor final class PreviewRecoveryService: StorageRecoveryService {
    private var resumes = [
        PendingStorageOperation(id: "10000000-0000-4000-8000-000000000001", state: "upload_pending", filename: "Stored-source.pdf"),
        PendingStorageOperation(id: "10000000-0000-4000-8000-000000000002", state: "upload_pending", filename: "Missing-source.pdf"),
        PendingStorageOperation(id: "10000000-0000-4000-8000-000000000003", state: "delete_pending", filename: "Deletion-not-permitted.pdf")
    ]
    private var artifacts = [
        PendingStorageOperation(id: "20000000-0000-4000-8000-000000000001", state: "upload_pending", filename: "Stored-application.pdf", jobId: "preview-1"),
        PendingStorageOperation(id: "20000000-0000-4000-8000-000000000002", state: "upload_pending", filename: "Missing-application.pdf", jobId: "preview-2")
    ]
    func pendingOperations(_ kind: StorageKind, token: String) async throws -> [PendingStorageOperation] { kind == .resume ? resumes : artifacts }
    func recoverStoredBytes(_ kind: StorageKind, operation: PendingStorageOperation, token: String) async throws -> RecoveredStoredFile {
        guard operation.canRecoverStoredBytes else { throw AppFailure.message("No deletion is permitted in this fixture.") }
        if operation.filename.hasPrefix("Missing-") {
            throw AppFailure.recoveryRequired(StorageRecoveryRequired(code: "\(kind.rawValue)_upload_bytes_required", operationId: operation.id, message: "Stored source bytes are missing."))
        }
        if kind == .resume {
            resumes.removeAll { $0.id == operation.id }
            return .resume(ResumeDocument(id: operation.id, label: "Recovered preview source", originalFilename: operation.filename))
        }
        artifacts.removeAll { $0.id == operation.id }
        return .artifact(Artifact(id: operation.id, jobId: operation.jobId, kind: "resume", filename: operation.filename))
    }
    func uploadFreshResume(_ upload: FreshResumeUpload, token: String) async throws -> ResumeDocument {
        ResumeDocument(id: upload.idempotencyKey.uuidString, label: upload.label, originalFilename: upload.filename)
    }
}
#endif
