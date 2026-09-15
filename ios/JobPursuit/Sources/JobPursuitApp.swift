import SwiftUI

@main struct JobPursuitApp: App {
    @StateObject private var store = AppStore()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(store).tint(Pursuit.ink)
                .task { await store.start() }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active && (store.session != nil || store.isRecoveryPreview) {
                        Task { await store.refreshRecoveryOperations() }
                    }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        Group { if store.isSignedIn { WorkspaceTabs() } else { WelcomeView() } }
            .foregroundStyle(Pursuit.ink)
            .alert("Let's take a look", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
                Button("OK", role: .cancel) { store.error = nil }
            } message: { Text(store.error ?? "") }
            .sheet(isPresented: $store.showConfiguration) { ConnectionView() }
            .overlay(alignment: .top) {
                if store.isBusy {
                    HStack(spacing: 8) { ProgressView().scaleEffect(0.8); Text("Working securely…").font(.caption.weight(.medium)) }
                        .padding(12).background(.regularMaterial, in: Capsule()).padding(.top, 4).allowsHitTesting(false)
                }
            }
    }
}

struct WorkspaceTabs: View {
    @EnvironmentObject private var store: AppStore
    @State private var selection = 0
    var body: some View {
        // Reserve real layout space outside the TabView's navigation stacks.
        // A top inset on TabView can overlap UIKit's navigation bars.
        VStack(spacing: 0) {
            if store.isPreview {
                HStack(spacing: 12) {
                    Label("DESIGN PREVIEW · FICTIONAL DATA", systemImage: "eye")
                        .font(.caption2.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    Button("Exit") { Task { await store.signOut() } }.font(.caption.weight(.semibold)).frame(minHeight: 44)
                }.foregroundStyle(Pursuit.muted).padding(.horizontal, 22).background(Pursuit.subtle)
                    .accessibilityIdentifier("preview.banner")
            }
            TabView(selection: $selection) {
                NavigationStack { TodayView(selectedTab: $selection) }.tabItem { Label("Today", systemImage: "square.grid.2x2.fill") }.tag(0)
                NavigationStack { DiscoverView() }.tabItem { Label("Discover", systemImage: "safari") }.tag(1)
                NavigationStack { StudioView() }.tabItem { Label("Studio", systemImage: "sparkles.rectangle.stack") }.tag(2)
                NavigationStack { TrackerView() }.tabItem { Label("Tracker", systemImage: "tray.full") }.tag(3)
            }
        }
        .overlay(alignment: .bottom) {
            if let notice = store.notice {
                Text(notice).font(.subheadline.weight(.medium)).padding(16).frame(maxWidth: 360)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18)).padding(.bottom, 80).padding(.horizontal, 24)
                    .onTapGesture { store.notice = nil }
                    .task(id: notice) { try? await Task.sleep(for: .seconds(4)); if store.notice == notice { store.notice = nil } }
            }
        }
    }
}
