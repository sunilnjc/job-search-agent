import SwiftUI
import UniformTypeIdentifiers

private struct FreshSourceSelection: Identifiable {
    var id = UUID()
    let operation: PendingStorageOperation
    let kind: StorageKind
}

struct StorageRecoveryView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var freshSource: FreshSourceSelection?
    @State private var actionFailure: String?
    @State private var newPreparation: PendingStorageOperation?
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Recover files already stored by an interrupted save. This does not call AI, regenerate documents, or delete files.")
                    if store.isRecoveryPreview { Text("OFFLINE UI FIXTURE · No network or real uploads").font(.caption).accessibilityIdentifier("recovery.offline") }
                    Button("Refresh pending operations") { Task { await store.refreshRecoveryOperations() } }.accessibilityIdentifier("recovery.refresh")
                    if let actionFailure { Text(actionFailure).font(.caption).foregroundStyle(.red) }
                }
                ForEach(StorageKind.allCases, id: \.self) { kind in
                    Section(kind.title) {
                        let state = store.storageRecovery.list(kind)
                        if state.isLoading { ProgressView("Checking saved operations…") }
                        if let error = state.error { Text("Recovery check failed: \(error)").foregroundStyle(.red) }
                        if state.hasLoaded && !state.isLoading && state.error == nil && state.operations.isEmpty { Text("No pending \(kind.rawValue) operations.").foregroundStyle(Pursuit.muted) }
                        ForEach(state.operations) { operation in
                            VStack(alignment: .leading, spacing: 12) {
                                Text(operation.filename).font(.headline)
                                Text(operation.state.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(Pursuit.muted)
                                Text("Reference: \(operation.id)").font(.caption2).textSelection(.enabled)
                                if operation.canRecoverStoredBytes {
                                    Button("Recover stored bytes — no AI") { Task {
                                        actionFailure = nil
                                        await store.perform {
                                            do { try await store.recoverStoredFile(operation, kind: kind) }
                                            catch {
                                                // A root-level alert dismisses this sheet on iOS.
                                                // Keep the recovery result and its next action here.
                                                if store.storageRecovery.messages[operation.key(kind)] == nil { actionFailure = error.localizedDescription }
                                            }
                                        }
                                    } }.buttonStyle(.borderless)
                                        .disabled(store.storageRecovery.busy.contains(operation.key(kind))).accessibilityIdentifier("recovery.recover.\(operation.id)")
                                } else {
                                    Text("This is not a pending upload. Native recovery will not finish a deletion or change these files.").font(.caption)
                                        .accessibilityIdentifier("recovery.noDeletion.\(operation.id)")
                                }
                                if store.storageRecovery.issues[operation.key(kind)]?.bytesMissing == true {
                                    Text("Missing bytes cannot be recovered. You may choose a fresh source file as a new private resume. The old operation will remain unresolved.").font(.caption)
                                    Button("Upload fresh source as new resume") { freshSource = FreshSourceSelection(operation: operation, kind: kind) }
                                        .buttonStyle(.borderless)
                                        .accessibilityIdentifier("recovery.fresh.\(operation.id)")
                                    if kind == .artifact {
                                        if store.storageRecovery.hasNewPreparationAllowance(operation) {
                                            Text("One new preparation allowed this session — AI has not started.").font(.caption)
                                                .accessibilityIdentifier("recovery.newPreparationAllowed.\(operation.id)")
                                        } else {
                                            Button("Choose a new AI preparation…") { newPreparation = operation }.buttonStyle(.borderless)
                                                .disabled(!store.storageRecovery.canAcknowledgeNewPreparation(operation))
                                                .accessibilityIdentifier("recovery.allowNew.\(operation.id)")
                                        }
                                    }
                                }
                                if let message = store.storageRecovery.messages[operation.key(kind)] { Text(message).font(.caption).accessibilityIdentifier("recovery.message.\(operation.id)") }
                            }.padding(.vertical, 8)
                        }
                    }
                }
                if !store.storageRecovery.messages.isEmpty {
                    Section("Recovery results") {
                        ForEach(store.storageRecovery.messages.keys.sorted(), id: \.self) { key in
                            Text(store.storageRecovery.messages[key] ?? "").font(.caption)
                        }
                    }
                }
            }.navigationTitle("Saved file recovery").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
                .task { await store.refreshRecoveryOperations() }
        }.sheet(item: $freshSource) { selection in
            FreshRecoverySourceView(operation: selection.operation, kind: selection.kind)
        }
        .confirmationDialog("Allow one new AI preparation?", isPresented: Binding(get: { newPreparation != nil }, set: { if !$0 { newPreparation = nil } }), titleVisibility: .visible, presenting: newPreparation) { operation in
            Button("Allow one new preparation") {
                do { try store.acknowledgeNewPreparation(operation) } catch { actionFailure = error.localizedDescription }
                newPreparation = nil
            }
            Button("Cancel", role: .cancel) { newPreparation = nil }
        } message: { operation in
            Text("\(operation.filename) has missing bytes. A NEW preparation may consume paid AI/API credits. This allows one attempt for this job in this session; it does not start AI. Choose a source and confirm AI use in Prepare. Existing documents and the unresolved operation will be retained.")
        }
    }
}

struct FreshRecoverySourceView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let operation: PendingStorageOperation
    let kind: StorageKind
    @State private var importer = false
    @State private var fileURL: URL?
    @State private var syntheticSource = false
    @State private var uploadIdentity = UUID()
    @State private var confirmed = false
    @State private var failure: String?
    @State private var attemptedUpload: FreshResumeUpload?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Upload a fresh source as a new resume. This will not repair the missing artifact, regenerate a document, replace the old operation, or delete anything.")
                    Text("Old operation: \(operation.filename)").font(.caption)
                }
                Section("Choose a source") {
                    if let fileURL { Text(fileURL.lastPathComponent) }
                    if syntheticSource { Text("Synthetic-QA-source.pdf") }
                    if attemptedUpload == nil {
                        Button("Choose PDF or DOCX") { importer = true }.accessibilityIdentifier("recovery.chooseFile")
                        if store.isRecoveryPreview {
                            Button("Use synthetic QA source") { syntheticSource = true; fileURL = nil }.accessibilityIdentifier("recovery.syntheticSource")
                        }
                    }
                    Text("PDF or DOCX, up to 8 MB. No AI is used for this upload.").font(.caption)
                }
                Section {
                    Toggle("I understand this creates a new resume and keeps the unresolved operation", isOn: $confirmed).accessibilityIdentifier("recovery.confirmFresh")
                    if let failure { Text(failure).font(.caption).foregroundStyle(.red) }
                    Button(attemptedUpload == nil ? "Upload fresh source" : "Retry the same upload attempt") { Task { await submit() } }
                        .disabled(!confirmed || (fileURL == nil && !syntheticSource) || store.isBusy)
                        .accessibilityIdentifier("recovery.uploadFresh")
                    if attemptedUpload != nil { Text("Retry keeps the same upload identity and source bytes; it does not start a second upload.").font(.caption) }
                }
            }.navigationTitle("Fresh source resume").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                .fileImporter(isPresented: $importer, allowedContentTypes: [.pdf, UTType(filenameExtension: "docx")!]) { result in
                    switch result {
                    case .success(let url): fileURL = url; syntheticSource = false
                    case .failure(let error): failure = error.localizedDescription
                    }
                }
        }
    }
    private func submit() async {
        failure = nil
        do {
            if attemptedUpload == nil {
                let content: Data
                let filename: String
                if syntheticSource && store.isRecoveryPreview {
                    content = Data("Offline UI fixture; never uploaded".utf8); filename = "Synthetic-QA-source.pdf"
                } else {
                    guard let fileURL else { return }
                    let granted = fileURL.startAccessingSecurityScopedResource()
                    defer { if granted { fileURL.stopAccessingSecurityScopedResource() } }
                    let size = try fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                    guard size <= 8 * 1024 * 1024 else { throw AppFailure.message("Choose a PDF or DOCX smaller than 8 MB.") }
                    content = try Data(contentsOf: fileURL); filename = fileURL.lastPathComponent
                }
                attemptedUpload = FreshResumeUpload(idempotencyKey: uploadIdentity, filename: filename, content: content,
                                                    label: URL(fileURLWithPath: filename).deletingPathExtension().lastPathComponent, roleFocus: "")
            }
            guard let upload = attemptedUpload else { return }
            await store.perform {
                do { try await store.uploadFreshRecoverySource(upload, operation: operation, kind: kind); dismiss() }
                catch { failure = error.localizedDescription }
            }
        } catch { failure = error.localizedDescription }
    }
}
