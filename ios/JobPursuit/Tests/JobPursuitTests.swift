import XCTest
@testable import JobPursuit

final class JobPursuitTests: XCTestCase {
    func testAppBundleIncludesPrivacyManifestAndPublicConfiguration() throws {
        #if !os(iOS)
        throw XCTSkip("Requires the real iOS application bundle; host tests do not establish iOS packaging.")
        #else
        let bundle = Bundle.main
        XCTAssertNotNil(bundle.url(forResource: "PrivacyInfo", withExtension: "xcprivacy"))
        XCTAssertNotNil(bundle.object(forInfoDictionaryKey: "API_BASE_URL") as? String)
        XCTAssertNotNil(bundle.object(forInfoDictionaryKey: "SUPABASE_URL") as? String)
        XCTAssertNotNil(bundle.object(forInfoDictionaryKey: "CFBundleIcons"))
        #endif
    }
    func testPreviewIsFictionalAndRanksDescending() {
        let workspace = Workspace.preview
        XCTAssertEqual(workspace.profile?.firstName, "Alex")
        XCTAssertEqual(workspace.rankedJobs.first?.companyName, "Meridian")
        XCTAssertEqual(workspace.rankedJobs.first?.matchLabel, "92% match")
        XCTAssertEqual(workspace.pendingQuestions.count, 1)
        XCTAssertFalse(workspace.applications.contains { $0.status == "submitted" })
    }
    func testSecureConfigurationRejectsUnsafeURLs() {
        for raw in ["http://example.com", "file:///tmp/data", "https://user:pass@example.com", "https://example.com?token=x"] { XCTAssertNil(AppConfiguration.secureURL(raw)) }
        XCTAssertNotNil(AppConfiguration.secureURL("https://api.example.com"))
        XCTAssertFalse(AppConfiguration(apiBase: "https://example.com", supabaseUrl: "https://example.supabase.co", publishableKey: ["sb", "secret", "not_for_client"].joined(separator: "_")).isReady)
        XCTAssertFalse(AppConfiguration.isPublicKey(["sk", "proj", "never-use-server-credentials"].joined(separator: "-")))
        XCTAssertTrue(AppConfiguration.isPublicKey("sb_publishable_abcdefghijklmnopqrst"))
    }
    func testBootstrapSnakeCaseAndNullableFields() throws {
        let data = Data("""
        {"profile":{"display_name":"Test Person","career_text":"Confirmed facts"},"preferences":{"sponsorship_required":true},"jobs":[{"id":"id","title":"Engineer","company_name":"Acme","source_url":"https://example.com","score":8.5,"location_text":null}],"resumes":[],"artifacts":[],"applications":[],"questions":[]}
        """.utf8)
        let value = try APIClient.decoder.decode(Workspace.self, from: data)
        XCTAssertEqual(value.profile?.careerText, "Confirmed facts")
        XCTAssertEqual(value.jobs[0].sourceUrl, "https://example.com")
        XCTAssertEqual(value.jobs[0].matchLabel, "85% match")
        XCTAssertEqual(value.jobs[0].displayLocation, "Location to confirm")
        XCTAssertNil(value.profile?.careerBackground)
    }
    func testProfessionBackgroundSupportsNonEngineeringAndEmptyLegacyContext() throws {
        let empty = try APIClient.decoder.decode(CareerBackground.self, from: Data("{}".utf8))
        XCTAssertTrue(empty.profession.isEmpty)
        XCTAssertEqual(empty.experienceLevel, "unspecified")
        XCTAssertTrue(empty.qualifications.isEmpty)
        for profession in ["Nursing", "Accounting", "Electrician", "Marketing", "Teaching", "An entirely new field"] {
            let raw: [String: Any] = ["profession": profession, "experience_level": "career_change", "qualifications": [
                ["name": "Example credential", "kind": "licence", "status": "expired", "jurisdiction": "Example jurisdiction", "expires_on": "2020-01-01", "evidence_note": "Self-reported"]
            ]]
            let parsed = try APIClient.decoder.decode(CareerBackground.self, from: JSONSerialization.data(withJSONObject: raw))
            XCTAssertEqual(parsed.profession, profession)
            XCTAssertEqual(parsed.qualifications.first?.statusLabel, "Expired")
            XCTAssertEqual(parsed.qualifications.first?.expiresOn, "2020-01-01")
            XCTAssertEqual(parsed.requestBody["experience_level"] as? String, "career_change")
            XCTAssertNil(parsed.qualifications.first?.requestBody["id"])
            XCTAssertNil(parsed.qualifications.first?.requestBody["verified"])
        }
    }
    func testUnconfirmedQualificationNeverDefaultsToCurrent() throws {
        let qualification = CareerQualification()
        XCTAssertEqual(qualification.status, "unknown")
        XCTAssertEqual(qualification.statusLabel, "Not confirmed")
        let payload = try JSONSerialization.data(withJSONObject: qualification.requestBody)
        let parsed = try APIClient.decoder.decode(CareerQualification.self, from: payload)
        XCTAssertNil(parsed.expiresOn)
        XCTAssertEqual(parsed.status, "unknown")
        let sparse = try APIClient.decoder.decode(CareerQualification.self, from: Data("{\"name\":\"Example course\",\"kind\":\"education\"}".utf8))
        XCTAssertEqual(sparse.status, "unknown")
        XCTAssertTrue(sparse.jurisdiction.isEmpty)
        XCTAssertTrue(sparse.evidenceNote.isEmpty)
        XCTAssertNil(sparse.expiresOn)
    }
    func testNewDocumentFocusDoesNotRestrictProfession() {
        XCTAssertEqual(DocumentFocus.allCases.map(\.rawValue), ["role_aligned", "career_change"])
        XCTAssertFalse(DocumentFocus.allCases.map(\.label).contains("Software engineering"))
    }
    func testEmployerLinksKeepJobQueriesWithoutAllowingUnsafeSchemes() {
        let listing = "https://careers.example.org/role?gh_jid=123"
        XCTAssertEqual(AppConfiguration.listingURL(listing)?.absoluteString, listing)
        XCTAssertNil(AppConfiguration.secureURL(listing))
        XCTAssertNil(AppConfiguration.listingURL("javascript:alert(1)"))
        XCTAssertNil(AppConfiguration.listingURL("https://user:password@example.org/role"))
    }
    func testSummaryBootstrapKeepsApplicationOutsideJobPage() throws {
        let data = Data("""
        {"jobs":[{"id":"j1","title":"Nurse","company_name":"Hospital","source_url":"https://example.test/j1","description":null}],"resumes":[],"artifacts":[],"applications":[{"id":"a1","job_id":"older-job","status":"submitted","notes":"Confirmation ABC"}],"questions":[],"capabilities":{"job_detail_fetch":true,"eligibility_review":"job_scoped_user_self_report","bootstrap_list_limit":200}}
        """.utf8)
        let workspace = try APIClient.decoder.decode(Workspace.self, from: data)
        XCTAssertNil(workspace.jobs.first?.description)
        XCTAssertEqual(workspace.capabilities?.jobDetailFetch, true)
        XCTAssertEqual(workspace.capabilities?.supportsEligibilityReview, true)
        XCTAssertEqual(workspace.applications.count, 1)
        let placeholder = Opportunity.trackerPlaceholder(jobId: workspace.applications[0].jobId)
        XCTAssertEqual(placeholder.id, "older-job")
        XCTAssertEqual(placeholder.title, "Tracked opportunity")
        XCTAssertTrue(placeholder.sourceUrl.isEmpty)
        XCTAssertNil(placeholder.eligibilityReview)
        XCTAssertEqual(workspace.applications.first?.notes, "Confirmation ABC")
    }
    func testEligibilityDraftNeverAssumesEligibilityOrConfirmation() {
        var draft = EligibilityReviewDraft()
        XCTAssertEqual(draft.status, .unknown)
        XCTAssertFalse(draft.isValid)
        draft.reason = "I still need to confirm sponsorship."
        XCTAssertFalse(draft.isValid)
        draft.confirmed = true
        XCTAssertTrue(draft.isValid)
        draft.reason = " \n "
        XCTAssertFalse(draft.isValid)
        draft.reason = String(repeating: "x", count: 2001)
        XCTAssertFalse(draft.isValid)
        draft.reason = String(repeating: "x", count: 2000)
        XCTAssertTrue(draft.isValid)
        XCTAssertEqual(EligibilityChoice.allCases.map(\.rawValue), ["eligible", "ineligible", "unknown"])
    }
    func testJobEditingDoesNotClearOmittedDescriptionOrChangeEligibility() {
        let summary = Opportunity(id: "j1", title: "Nurse", companyName: "Hospital", sourceUrl: "https://example.test/j1")
        var edits = OpportunityEdits(job: summary)
        XCTAssertFalse(edits.isValid)
        edits.title = "Senior nurse"
        XCTAssertTrue(edits.isValid)
        XCTAssertEqual(edits.changes.keys.sorted(), ["title"])
        XCTAssertNil(edits.changes["description"])
        XCTAssertNil(edits.changes["eligibility_status"])
        edits.description = "Current registration required."
        XCTAssertEqual(edits.changes["description"] as? String, "Current registration required.")
        edits.description = " \n "
        XCTAssertFalse(edits.isValid)
        edits.description = "Current registration required."
        edits.company = String(repeating: "x", count: 161)
        XCTAssertFalse(edits.isValid)
    }
    @MainActor func testPreviewDetailAndMissingTrackerJobRemainOfflineAndRecoverable() async throws {
        let store = AppStore(isUITesting: true); store.enterPreview()
        try await store.fetchJobDetails(id: "preview-1")
        XCTAssertNotNil(store.opportunity(id: "preview-1")?.description)
        store.workspace.jobs.removeAll { $0.id == "preview-2" }
        do { try await store.fetchJobDetails(id: "preview-2"); XCTFail("Missing preview job should remain unavailable") }
        catch { XCTAssertTrue(error.localizedDescription.contains("application remains visible")) }
        XCTAssertEqual(store.workspace.applications.first?.jobId, "preview-2")
        XCTAssertEqual(store.workspace.applications.first?.status, "ready")
        let draft = EligibilityReviewDraft(status: .eligible, reason: "Synthetic declaration", confirmed: true)
        do { try await store.reviewEligibility(jobId: "preview-1", draft: draft); XCTFail("Preview must not save a review") }
        catch { XCTAssertTrue(error.localizedDescription.contains("offline")) }
        var edits = OpportunityEdits(job: store.workspace.jobs[0]); edits.title = "Edited role"
        do { try await store.updateJob(edits); XCTFail("Preview must not patch jobs") }
        catch { XCTAssertTrue(error.localizedDescription.contains("offline")) }
        await store.signOut()
        XCTAssertTrue(store.jobDetails.isEmpty)
        XCTAssertTrue(store.workspace.applications.isEmpty)
    }
    @MainActor func testPreviewRejectsWrites() {
        let store = AppStore(); store.enterPreview()
        XCTAssertThrowsError(try store.requireLive())
        XCTAssertNil(store.session)
    }
    @MainActor func testAutomatedModeUsesOnlyInMemoryConfigurationAndBlocksLiveAuth() async {
        let before = AppConfiguration.load()
        let store = AppStore(isUITesting: true)
        XCTAssertFalse(store.configuration.isReady)
        await store.start()
        XCTAssertNil(store.session)
        store.setConfiguration(AppConfiguration(apiBase: "https://offline.example.org", supabaseUrl: "https://offline.example.org", publishableKey: "sb_publishable_abcdefghijklmnopqrst"))
        XCTAssertEqual(AppConfiguration.load(), before)
        XCTAssertFalse(store.client.configuration.isReady)
        do { try await store.sendCode(email: "offline@example.test"); XCTFail("Test mode cannot send mail") }
        catch { XCTAssertTrue(error.localizedDescription.contains("offline")) }
        do { try await store.verifyCode(email: "offline@example.test", code: "000000"); XCTFail("Test mode cannot authenticate") }
        catch { XCTAssertTrue(error.localizedDescription.contains("offline")) }
        await store.signOut()
        XCTAssertEqual(AppConfiguration.load(), before)
        XCTAssertNil(store.session)
    }
}
