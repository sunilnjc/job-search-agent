import SwiftUI
import UniformTypeIdentifiers
import PDFKit
import QuickLook

struct SharedFile: Identifiable { var id = UUID(); var url: URL }

struct StudioView: View {
    @EnvironmentObject private var store: AppStore
    @State private var importer = false
    @State private var profile = false
    @State private var assistant = false
    @State private var file: SharedFile?
    @State private var roleFocus = ""
    @State private var reviewFacts = false
    @State private var importedFacts = ""
    @State private var recovery = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                SheetHeader(eyebrow: "The application studio", title: "Your story.\nBeautifully told.", detail: "One foundation. A focused version for every opportunity.")
                Button { recovery = true } label: {
                    Label("Saved file recovery (\(store.storageRecovery.pendingCount))", systemImage: "arrow.clockwise.icloud")
                }.accessibilityIdentifier("studio.recovery")
                ForEach(store.operationWarnings.keys.sorted(), id: \.self) { id in
                    Label(store.operationWarnings[id] ?? "", systemImage: "exclamationmark.triangle").font(.subheadline).cardSurface()
                }
                resumeIllustration
                HStack { SectionTitle(title: "Your foundation", subtitle: "Private resumes. Confirmed facts."); Button { profile = true } label: { Image(systemName: "slider.horizontal.3").font(.title3) }.accessibilityLabel("Edit profile and facts") }
                ForEach(store.workspace.resumes) { resume in
                    VStack(alignment: .leading, spacing: 10) {
                        Button { open(id: resume.id, filename: resume.originalFilename, isResume: true) } label: {
                            documentRow(title: resume.label, subtitle: "\(resume.roleFocus?.isEmpty == false ? resume.roleFocus! : "Career foundation") · \(resume.originalFilename)", icon: "doc.text", badge: resume.isDefault == true ? "BASE" : nil)
                        }.buttonStyle(.plain)
                        Button { Task { await store.perform { importedFacts = try await store.resumeText(resume); reviewFacts = true } } } label: { Label("Review & confirm facts for AI", systemImage: "checkmark.shield").font(.caption.weight(.medium)) }.padding(.horizontal, 6)
                    }
                }
                if store.workspace.resumes.isEmpty { EmptyPanel(icon: "doc.badge.plus", title: "Bring your experience", detail: "Import a text-based PDF or Word resume. Your original stays unchanged while tailored versions are saved separately.") }
                VStack(alignment: .leading, spacing: 8) {
                    Text("Role or career focus (optional)").font(.subheadline.weight(.semibold))
                    TextField("For example, nursing, accounting or design", text: $roleFocus).textFieldStyle(.roundedBorder).accessibilityIdentifier("studio.roleFocus")
                    Text("Use any profession—or leave this blank for a general resume.").font(.caption).foregroundStyle(Pursuit.muted)
                }
                PrimaryButton(title: "Import a resume", icon: "plus") { if store.isPreview { store.error = "Sign in to upload a real resume. Preview files are fictional." } else { importer = true } }.accessibilityIdentifier("studio.import")
                HStack(spacing: 7) { Image(systemName: "lock.shield"); Text("Private storage · PDF & DOCX · Up to 8 MB") }.font(.caption).foregroundStyle(Pursuit.muted)
                SectionTitle(title: "Made for the opportunity", subtitle: "Tailored documents, ready to review and share.")
                if store.workspace.artifacts.isEmpty { EmptyPanel(icon: "square.stack.3d.up", title: "Your first version is ahead", detail: "Open a role in Discover and choose Prepare my application. Your resume and cover letter will appear here.") }
                ForEach(store.workspace.artifacts) { artifact in
                    Button { open(id: artifact.id, filename: artifact.filename, isResume: false) } label: { documentRow(title: artifact.kind.replacingOccurrences(of: "_", with: " ").capitalized, subtitle: artifact.filename, icon: artifact.kind == "cover_letter" ? "envelope" : "doc.text", badge: artifact.filename.uppercased().hasSuffix("PDF") ? "PDF" : "WORD") }.buttonStyle(.plain)
                }
                Button { assistant = true } label: { HStack { Image(systemName: "sparkles"); VStack(alignment: .leading, spacing: 5) { Text("Need a second perspective?").font(.headline); Text("Refine your story with grounded AI.").font(.caption).foregroundStyle(Pursuit.muted) }; Spacer(); Image(systemName: "arrow.up.right") }.foregroundStyle(Pursuit.ink).cardSurface() }.buttonStyle(.plain)
            }.padding(22).frame(maxWidth: 700).frame(maxWidth: .infinity)
        }.pursuitPage().navigationTitle("Studio").navigationBarTitleDisplayMode(.inline)
            .fileImporter(isPresented: $importer, allowedContentTypes: [.pdf, UTType(filenameExtension: "docx")!]) { result in
                switch result { case .success(let url): Task { await store.perform { try await store.upload(url: url, label: "", roleFocus: roleFocus) } }; case .failure(let error): store.error = error.localizedDescription }
            }
            .sheet(item: $file) { DocumentView(url: $0.url) }
            .sheet(isPresented: $profile) { ProfileView() }
            .sheet(isPresented: $assistant) { AssistantView(job: nil) }
            .sheet(isPresented: $reviewFacts) { ConfirmFactsView(initialText: importedFacts) }
            .sheet(isPresented: $recovery) { StorageRecoveryView() }
            .task { await store.refreshRecoveryOperations() }
            .refreshable { await store.perform { try await store.reload() } }
    }
    var resumeIllustration: some View {
        HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 12) {
                BrandMark(size: 26)
                RoundedRectangle(cornerRadius: 2).fill(Pursuit.night).frame(width: 75, height: 6)
                RoundedRectangle(cornerRadius: 2).fill(Pursuit.violet.opacity(0.5)).frame(width: 48, height: 4)
                ForEach(0..<5) { i in RoundedRectangle(cornerRadius: 2).fill(Pursuit.night.opacity(0.09)).frame(width: i == 4 ? 56 : 90, height: 3) }
            }.padding(21).background(.white, in: RoundedRectangle(cornerRadius: 10)).rotationEffect(.degrees(-6)).shadow(color: .black.opacity(0.08), radius: 10, y: 8).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 13) {
                Eyebrow(text: "Substance over noise")
                Text("A clear story\nopens doors.").font(.system(.title3, design: .rounded, weight: .semibold)).foregroundStyle(Pursuit.ink)
                Text("Focused. Readable.\nGrounded in you.").font(.caption).lineSpacing(4).foregroundStyle(Pursuit.muted)
                Chip(text: "ATS-friendly", icon: "checkmark")
            }
            Spacer(minLength: 0)
        }.padding(23).frame(maxWidth: .infinity, alignment: .leading).background(Pursuit.subtle, in: RoundedRectangle(cornerRadius: 26))
    }
    func documentRow(title: String, subtitle: String, icon: String, badge: String?) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.title2).foregroundStyle(Pursuit.ink).frame(width: 44, height: 52).background(Pursuit.subtle, in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 6) { Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Pursuit.ink); Text(subtitle).font(.caption2).foregroundStyle(Pursuit.muted).lineLimit(2); if let badge { Eyebrow(text: badge) } }
            Spacer(minLength: 1); Image(systemName: "square.and.arrow.up").font(.subheadline).foregroundStyle(Pursuit.muted)
        }.cardSurface()
    }
    func open(id: String, filename: String, isResume: Bool) { Task { await store.perform { file = SharedFile(url: try await store.download(id: id, filename: filename, isResume: isResume)) } } }
}

struct ConfirmFactsView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let initialText: String
    @State private var text = ""
    @State private var confirmed = false
    var body: some View {
        NavigationStack {
            Form {
                Section { Text("Review the text extracted from your resume. Correct old dates or claims, remove sensitive details you don't need, and confirm what AI may use. Existing career facts will be kept.").font(.subheadline) }
                Section("Your evidence") { TextEditor(text: $text).frame(minHeight: 320).accessibilityLabel("Review extracted resume facts") }
                Section {
                    Toggle("These facts accurately describe my experience", isOn: $confirmed)
                    PrimaryButton(title: "Confirm and add to profile", icon: "checkmark.shield", busy: store.isBusy) { Task { await store.perform { try await store.confirmImportedFacts(text); dismiss() } } }.disabled(!confirmed || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.navigationTitle("Confirm your foundation").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                .onAppear { text = initialText }
        }
    }
}

struct PDFPreview: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> PDFView { let v = PDFView(); v.autoScales = true; v.displayMode = .singlePageContinuous; v.backgroundColor = .systemGroupedBackground; v.document = PDFDocument(url: url); return v }
    func updateUIView(_ uiView: PDFView, context: Context) {}
}
struct WordPreview: UIViewControllerRepresentable {
    let url: URL
    func makeCoordinator() -> Coordinator { Coordinator(url: url) }
    func makeUIViewController(context: Context) -> QLPreviewController { let c = QLPreviewController(); c.dataSource = context.coordinator; return c }
    func updateUIViewController(_ c: QLPreviewController, context: Context) {}
    class Coordinator: NSObject, QLPreviewControllerDataSource {
        let url: URL
        init(url: URL) { self.url = url }
        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }
        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem { url as NSURL }
    }
}
struct DocumentView: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            Group { if url.pathExtension.lowercased() == "pdf" { PDFPreview(url: url) } else { WordPreview(url: url) } }
                .navigationTitle("Review document").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }; ToolbarItem(placement: .primaryAction) { ShareLink(item: url) { Label("Save or share", systemImage: "square.and.arrow.up") } } }
                .safeAreaInset(edge: .bottom) { Text("Use Share → Save to Files to keep a copy on your iPhone.").font(.caption).foregroundStyle(Pursuit.muted).padding(14).frame(maxWidth: .infinity).background(.regularMaterial) }
        }
    }
}
