import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var store: AppStore
    @Binding var selectedTab: Int
    @State private var profile = false
    @State private var questions = false
    @State private var assistant = false
    @ScaledMetric(relativeTo: .largeTitle) private var headlineSize = 37
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                HStack {
                    HStack(spacing: 9) { BrandMark(size: 36).accessibilityHidden(true); Text("the job pursuit").font(.system(.headline, design: .serif, weight: .semibold)) }
                    Spacer()
                    Button { profile = true } label: { Image(systemName: "person.crop.circle").font(.title2).foregroundStyle(Pursuit.ink).padding(9).background(Pursuit.card, in: Circle()) }.accessibilityLabel("Your profile")
                }
                VStack(alignment: .leading, spacing: 9) {
                    Eyebrow(text: Date.now.formatted(.dateTime.weekday(.wide).month(.wide).day()))
                    Text("Your next move,\n\(store.workspace.profile?.firstName ?? "there").").font(.system(size: headlineSize, weight: .semibold, design: .rounded)).tracking(-1.3).foregroundStyle(Pursuit.ink).fixedSize(horizontal: false, vertical: true)
                    Text("Less searching. More possibility.").font(.subheadline).foregroundStyle(Pursuit.muted)
                }
                focusCard
                HStack(spacing: 12) {
                    metric("Opportunities", value: store.workspace.rankedJobs.count, icon: "scope", tab: 1)
                    metric("In progress", value: store.workspace.applications.filter { !["rejected", "withdrawn", "closed"].contains($0.status) }.count, icon: "arrow.up.right", tab: 3)
                }
                if !store.workspace.pendingQuestions.isEmpty {
                    Button { questions = true } label: {
                        HStack(spacing: 13) {
                            Image(systemName: "bubble.left.and.text.bubble.right").foregroundStyle(Pursuit.ink).font(.title3)
                            VStack(alignment: .leading, spacing: 4) { Text("A little input. A better answer.").font(.subheadline.weight(.semibold)).foregroundStyle(Pursuit.ink); Text("\(store.workspace.pendingQuestions.count) question\(store.workspace.pendingQuestions.count == 1 ? "" : "s") need your confirmation").font(.caption).foregroundStyle(Pursuit.muted) }
                            Spacer(); Image(systemName: "chevron.right").font(.caption).foregroundStyle(Pursuit.muted)
                        }.cardSurface()
                    }.buttonStyle(.plain)
                }
                HStack { SectionTitle(title: "Worth a closer look", subtitle: "Your opportunities, in order of fit."); Button("See all") { selectedTab = 1 }.font(.caption.weight(.semibold)) }
                if store.workspace.rankedJobs.isEmpty {
                    EmptyPanel(icon: "safari", title: "Start with one great role", detail: "Add a job in Discover. We'll help you understand the fit and prepare an application grounded in your experience.")
                } else {
                    ForEach(store.workspace.rankedJobs.prefix(2)) { job in NavigationLink(value: job) { JobCard(job: job) }.buttonStyle(.plain) }
                }
                Button { assistant = true } label: {
                    HStack { Image(systemName: "sparkles"); Text("Talk through your next step").font(.subheadline.weight(.semibold)); Spacer(); Image(systemName: "arrow.right") }.foregroundStyle(Pursuit.ink).padding(21).background(Pursuit.subtle, in: RoundedRectangle(cornerRadius: 22))
                }.buttonStyle(.plain)
                Text("Your pace. Your story. Your next chapter.").font(.caption).foregroundStyle(Pursuit.muted).frame(maxWidth: .infinity).padding(.vertical, 5)
            }.padding(22).frame(maxWidth: 700).frame(maxWidth: .infinity)
        }.pursuitPage().toolbar(.hidden, for: .navigationBar).refreshable { await store.perform { try await store.reload() } }
            .navigationDestination(for: Opportunity.self) { JobDetailView(jobId: $0.id) }
            .sheet(isPresented: $profile) { ProfileView() }
            .sheet(isPresented: $questions) { QuestionsView() }
            .sheet(isPresented: $assistant) { AssistantView(job: nil) }
            .onAppear {
                #if DEBUG
                if store.isPreview && ProcessInfo.processInfo.arguments.contains("--preview-profile") { profile = true }
                #endif
            }
    }
    var focusCard: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack { Eyebrow(text: "Your focus", color: Pursuit.mutedOnNight); Spacer(); Image(systemName: "sparkle").font(.title2).foregroundStyle(Pursuit.mint) }
            Text(store.workspace.resumes.isEmpty ? "A stronger story\nstarts with you." : "Make the right\nfirst impression.").font(.system(.title, design: .rounded, weight: .medium)).tracking(-0.6).foregroundStyle(.white)
            Text(store.workspace.resumes.isEmpty ? "Add your resume and career details. Build a foundation that every application can trust." : "Turn your experience into a clear, focused application for the role you really want.").font(.subheadline).foregroundStyle(Pursuit.mutedOnNight).lineSpacing(4)
            Button { selectedTab = 2 } label: {
                HStack { Text(store.workspace.resumes.isEmpty ? "Build your foundation" : "Open your studio"); Spacer(); Image(systemName: "arrow.up.right") }.font(.subheadline.weight(.semibold)).foregroundStyle(Pursuit.night).padding(16).background(Pursuit.mint, in: RoundedRectangle(cornerRadius: 14))
            }.buttonStyle(.plain)
        }.padding(25).frame(maxWidth: .infinity, alignment: .leading).background(Pursuit.night, in: RoundedRectangle(cornerRadius: 28))
    }
    func metric(_ title: String, value: Int, icon: String, tab: Int) -> some View {
        Button { selectedTab = tab } label: {
            VStack(alignment: .leading, spacing: 12) {
                HStack { Text(value, format: .number).font(.system(size: 32, weight: .medium, design: .rounded)); Spacer(); Image(systemName: icon).font(.subheadline).foregroundStyle(Pursuit.ink) }
                Text(title).font(.caption).foregroundStyle(Pursuit.muted)
            }.foregroundStyle(Pursuit.ink).padding(20).frame(maxWidth: .infinity, alignment: .leading).background(Pursuit.card, in: RoundedRectangle(cornerRadius: 22))
        }.buttonStyle(.plain)
    }
}
