import SwiftUI

struct AssistantView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let job: Opportunity?
    @State private var message = ""
    @State private var mode = "application"
    @State private var consent = false
    @State private var questions = false
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 15) {
                    HStack { Eyebrow(text: job?.companyName ?? "Your career copilot"); Spacer(); Chip(text: "Fact-grounded", icon: "checkmark.shield") }
                    Picker("Conversation purpose", selection: $mode) { Text("Application").tag("application"); Text("Interview").tag("interview"); Text("Resume").tag("resume") }.pickerStyle(.segmented)
                }.padding(22)
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 20) {
                            if store.chat.isEmpty { introduction }
                            ForEach(store.chat) { line in
                                VStack(alignment: .leading, spacing: 12) {
                                    Eyebrow(text: line.role == "user" ? "You" : "Pursuit AI")
                                    Text(line.content).font(.body).lineSpacing(4).textSelection(.enabled)
                                    if !line.evidence.isEmpty {
                                        DisclosureGroup("What this is based on") { ForEach(Array(line.evidence.enumerated()), id: \.offset) { _, text in Text(text).font(.caption).foregroundStyle(Pursuit.muted).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6) } }.font(.caption.weight(.medium))
                                    }
                                }.padding(20).frame(maxWidth: .infinity, alignment: .leading).background(line.role == "user" ? Pursuit.subtle : Pursuit.card, in: RoundedRectangle(cornerRadius: 22)).id(line.id)
                            }
                            Color.clear.frame(height: 1).id("chat-end")
                        }.padding(.horizontal, 22).padding(.bottom, 14)
                    }.onChange(of: store.chat.count) { _, _ in withAnimation { proxy.scrollTo("chat-end", anchor: .bottom) } }
                }
                VStack(spacing: 10) {
                    if !consent {
                        Toggle(isOn: $consent) { Text("Use my relevant profile and resume with the configured AI provider. API credits apply.").font(.caption).foregroundStyle(Pursuit.muted) }
                    }
                    HStack(alignment: .bottom, spacing: 12) {
                        TextField("Ask something that matters…", text: $message, axis: .vertical).lineLimit(1...5).padding(14).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 18)).accessibilityIdentifier("assistant.message")
                        Button { send() } label: { Image(systemName: "arrow.up").font(.headline).foregroundStyle(.white).frame(width: 48, height: 48).background(Pursuit.violet, in: Circle()) }.disabled(message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !consent || store.isBusy).accessibilityLabel("Send message")
                    }
                }.padding(18).background(.regularMaterial)
            }.pursuitPage().navigationTitle("Ask AI").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }; ToolbarItem(placement: .primaryAction) { Button { store.chat = [] } label: { Image(systemName: "square.and.pencil") }.accessibilityLabel("New conversation") } }
                .onAppear { store.chat = [] }
                .sheet(isPresented: $questions) { QuestionsView(jobId: job?.id) }
        }
    }
    var introduction: some View {
        VStack(alignment: .leading, spacing: 23) {
            Image(systemName: "sparkles").font(.system(size: 35, weight: .light)).foregroundStyle(Pursuit.ink).padding(.top, 20)
            Text("A thoughtful\nsecond perspective.").font(.system(.largeTitle, design: .rounded, weight: .semibold)).tracking(-0.8)
            Text("We'll work with what you've actually done. Missing facts become questions—not made-up experience.").font(.subheadline).foregroundStyle(Pursuit.muted).lineSpacing(4)
            ForEach(["How does my experience fit this role?", "Help me prepare for an interview in my target role", "Which qualifications or details still need confirmation?", "Make my resume summary more focused"], id: \.self) { prompt in Button { message = prompt; if prompt.contains("interview") { mode = "interview" } else if prompt.contains("resume") { mode = "resume" } else { mode = "application" } } label: { HStack { Text(prompt).multilineTextAlignment(.leading); Spacer(); Image(systemName: "arrow.up.left") }.font(.subheadline).foregroundStyle(Pursuit.ink).padding(18).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 18)) }.buttonStyle(.plain) }
        }.padding(.bottom, 15)
    }
    func send() { let text = message.trimmingCharacters(in: .whitespacesAndNewlines); Task { await store.perform {
        questions = try await store.ask(text, mode: mode, jobId: job?.id); message = ""
    } } }
}
