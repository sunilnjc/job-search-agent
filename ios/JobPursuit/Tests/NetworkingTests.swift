import XCTest
@testable import JobPursuit

private final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, String, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, type, data) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": type])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class NetworkingTests: XCTestCase {
    override func tearDown() { StubProtocol.handler = nil; super.tearDown() }
    private func body(_ request: URLRequest) throws -> [String: Any] {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(contentsOf: buffer.prefix(count))
            }
        }
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }
    private func jobJSON(description: String? = "Complete employer requirements", review: [String: Any]? = nil) throws -> Data {
        var object: [String: Any] = ["id": "j1", "title": "Nurse", "company_name": "Hospital", "source_url": "https://example.test/j1", "description": description as Any? ?? NSNull()]
        if let review { object["eligibility_review"] = review; object["eligibility_status"] = review["status"] }
        return try JSONSerialization.data(withJSONObject: object)
    }
    private func client() -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return APIClient(configuration: AppConfiguration(apiBase: "https://mobile.example.org", supabaseUrl: "https://project.example.org", publishableKey: "sb_publishable_abcdefghijklmnopqrst"), transport: URLSession(configuration: config))
    }
    func testMobileRequestUsesOnlyCallerToken() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://mobile.example.org/api/mobile/bootstrap")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-user-token")
            XCTAssertNil(request.value(forHTTPHeaderField: "apikey"))
            return (200, "application/json", Data("{}".utf8))
        }
        _ = try await client().request("bootstrap", token: "test-user-token")
    }
    func testRefreshRoutesToSupabaseWithPublicKey() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://project.example.org/auth/v1/token?grant_type=refresh_token")
            XCTAssertNotNil(request.value(forHTTPHeaderField: "apikey"))
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            XCTAssertEqual(request.httpMethod, "POST")
            return (200, "application/json", Data("{}".utf8))
        }
        _ = try await client().request("token", method: "POST", body: ["refresh_token": "test-refresh"], auth: true)
    }
    func testHTMLLoginPageIsNotAcceptedAsAPIResponse() async throws {
        StubProtocol.handler = { _ in (200, "text/html", Data("<html>Sign in</html>".utf8)) }
        do { _ = try await client().request("bootstrap"); XCTFail("HTML must not appear as a successful workspace") }
        catch { XCTAssertTrue(error.localizedDescription.contains("not routed")) }
    }
    func testRedirectAndRateLimitAreActionable() async throws {
        for code in [302, 429] {
            StubProtocol.handler = { _ in (code, "text/html", Data()) }
            do { _ = try await client().request("bootstrap"); XCTFail("Unexpected success") }
            catch { XCTAssertTrue(error.localizedDescription.contains(code == 302 ? "redirected" : "Too many")) }
        }
    }
    func testMissingFactsResponsePreservesQuestionsForNativeSheet() async throws {
        StubProtocol.handler = { _ in (422, "application/json", Data("""
        {"detail":{"message":"Confirm career details first.","questions":[{"id":"q1","job_id":"j1","prompt":"Which licence can you confirm?","status":"pending"}]}}
        """.utf8)) }
        do { _ = try await client().request("jobs/j1/prepare", method: "POST"); XCTFail("Must not report prepared documents") }
        catch AppFailure.inputRequired(let message, let questions) {
            XCTAssertEqual(message, "Confirm career details first.")
            XCTAssertEqual(questions.first?.jobId, "j1")
            XCTAssertEqual(questions.first?.status, "pending")
        }
    }
    func testJobDetailFetchReturnsFullDescriptionAndUsesMobileToken() async throws {
        let requirements = String(repeating: "R", count: 80_000)
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mobile/jobs/j1")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-user-token")
            XCTAssertNil(request.value(forHTTPHeaderField: "apikey"))
            return (200, "application/json", try self.jobJSON(description: requirements))
        }
        let job = try await client().jobDetail(id: "j1", token: "test-user-token")
        XCTAssertEqual(job.description, requirements)
        XCTAssertNil(job.eligibilityReview)
    }
    func testDetailFailureAndMismatchedJobAreNotSuccessfulEmptyDescriptions() async throws {
        StubProtocol.handler = { _ in (404, "application/json", Data("{\"detail\":\"Job unavailable\"}".utf8)) }
        do { _ = try await client().jobDetail(id: "j1", token: "test"); XCTFail("A failed detail read is not an empty job") }
        catch { XCTAssertEqual(error.localizedDescription, "Job unavailable") }
        StubProtocol.handler = { _ in (200, "application/json", try self.jobJSON()) }
        do { _ = try await client().jobDetail(id: "different-job", token: "test"); XCTFail("Must not cache another job") }
        catch { XCTAssertTrue(error.localizedDescription.contains("different opportunity")) }
    }
    func testEligibilityReviewPostsExplicitSelfReportForAllChoices() async throws {
        for choice in EligibilityChoice.allCases {
            StubProtocol.handler = { request in
                XCTAssertEqual(request.url?.path, "/api/mobile/jobs/j1/eligibility")
                XCTAssertEqual(request.httpMethod, "POST")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test")
                let body = try self.body(request)
                XCTAssertEqual(body.keys.sorted(), ["confirmed", "reason", "status"])
                XCTAssertEqual(body["status"] as? String, choice.rawValue)
                XCTAssertEqual(body["reason"] as? String, "My job-specific assessment")
                XCTAssertEqual(body["confirmed"] as? Bool, true)
                let review: [String: Any] = ["status": choice.rawValue, "reason": "My job-specific assessment", "confirmed": true,
                                          "confirmed_at": "2026-09-15T00:00:00Z", "provenance": "user_self_report", "independently_verified": false]
                return (200, "application/json", try self.jobJSON(review: review))
            }
            let job = try await client().reviewEligibility(jobId: "j1", draft: EligibilityReviewDraft(status: choice, reason: " My job-specific assessment \n", confirmed: true), token: "test")
            XCTAssertEqual(job.eligibilityStatus, choice.rawValue)
            XCTAssertEqual(job.eligibilityReview?.independentlyVerified, false)
            XCTAssertEqual(job.eligibilityReview?.label, "Self-reported eligibility: \(choice.label)")
        }
    }
    func testUnconfirmedEligibilityNeverSendsRequestAndMalformedReviewIsRejected() async throws {
        StubProtocol.handler = { _ in XCTFail("Unconfirmed review cannot be sent"); return (500, "application/json", Data()) }
        do { _ = try await client().reviewEligibility(jobId: "j1", draft: EligibilityReviewDraft(reason: "Unconfirmed"), token: "test"); XCTFail("Must require confirmation") }
        catch { XCTAssertTrue(error.localizedDescription.contains("explicitly confirm")) }
        StubProtocol.handler = { _ in (200, "application/json", try self.jobJSON()) }
        do { _ = try await client().reviewEligibility(jobId: "j1", draft: EligibilityReviewDraft(reason: "Uncertain", confirmed: true), token: "test"); XCTFail("Missing review is not success") }
        catch { XCTAssertTrue(error.localizedDescription.contains("did not return a confirmed self-report")) }
    }
    func testPatchEnrichesBookmarkWithOnlyExplicitSourceChanges() async throws {
        var edits = OpportunityEdits(job: Opportunity(id: "j1", title: "Nurse", companyName: "Hospital", sourceUrl: "https://example.test/j1"))
        edits.description = "Complete employer requirements"
        edits.location = "Dubai"
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mobile/jobs/j1")
            XCTAssertEqual(request.httpMethod, "PATCH")
            let body = try self.body(request)
            XCTAssertEqual(body.keys.sorted(), ["description", "location_text"])
            XCTAssertEqual(body["description"] as? String, "Complete employer requirements")
            XCTAssertEqual(body["location_text"] as? String, "Dubai")
            return (200, "application/json", try self.jobJSON())
        }
        let result = try await client().updateJob(edits, token: "test")
        XCTAssertEqual(result.description, "Complete employer requirements")
    }
    func testOnboardingMarkerIsWrittenOnlyAfterBothSavesAndDoesNotRewriteCareerFacts() async throws {
        var paths: [String] = []
        StubProtocol.handler = { request in
            paths.append(request.url!.path)
            XCTAssertEqual(request.httpMethod, "PUT")
            let body = try self.body(request)
            if paths.count == 1 { XCTAssertNil(body["onboarding_completed_at"]); XCTAssertEqual(body["career_text"] as? String, "Preserve these facts") }
            if paths.count == 3 {
                XCTAssertEqual(body.keys.sorted(), ["onboarding_completed_at"])
                XCTAssertNotNil(ISO8601DateFormatter().date(from: try XCTUnwrap(body["onboarding_completed_at"] as? String)))
            }
            return (200, "application/json", Data("{}".utf8))
        }
        try await client().saveProfileAndPreferences(profile: ["display_name": "Test Person", "career_text": "Preserve these facts"], preferences: ["target_titles": ["Nurse"]], onboardingCompletedAt: nil, token: "test")
        XCTAssertEqual(paths, ["/api/mobile/profile", "/api/mobile/preferences", "/api/mobile/profile"])
    }
    func testFailedPreferenceSaveDoesNotMarkOnboardingComplete() async throws {
        var paths: [String] = []
        StubProtocol.handler = { request in
            paths.append(request.url!.path)
            XCTAssertNil(try self.body(request)["onboarding_completed_at"])
            return (paths.count == 2 ? 502 : 200, "application/json", Data("{}".utf8))
        }
        do { try await client().saveProfileAndPreferences(profile: ["display_name": "Test Person"], preferences: ["target_titles": ["Nurse"]], onboardingCompletedAt: nil, token: "test"); XCTFail("Partial save must fail") }
        catch { XCTAssertEqual(paths, ["/api/mobile/profile", "/api/mobile/preferences"]) }
    }
    func testExistingMarkerAndIncompleteProfileNeverWriteNewCompletionDate() async throws {
        for (name, roles, marker) in [("Test Person", ["Nurse"], "2025-01-01T00:00:00Z" as String?), ("Test Person", [], nil), (" \n ", ["Nurse"], nil)] {
            var count = 0
            StubProtocol.handler = { request in
                count += 1
                XCTAssertNil(try self.body(request)["onboarding_completed_at"])
                return (200, "application/json", Data("{}".utf8))
            }
            try await client().saveProfileAndPreferences(profile: ["display_name": name], preferences: ["target_titles": roles], onboardingCompletedAt: marker, token: "test")
            XCTAssertEqual(count, 2)
        }
    }
    func testCompletionMarkerFailureExplainsPartialSuccessAndRetry() async throws {
        var count = 0
        StubProtocol.handler = { _ in count += 1; return (count == 3 ? 502 : 200, "application/json", Data("{}".utf8)) }
        do { try await client().saveProfileAndPreferences(profile: ["display_name": "Test Person"], preferences: ["target_titles": ["Nurse"]], onboardingCompletedAt: nil, token: "test"); XCTFail("Marker failure should be recoverable") }
        catch { XCTAssertTrue(error.localizedDescription.contains("Profile and preferences saved")); XCTAssertTrue(error.localizedDescription.contains("retry")) }
        XCTAssertEqual(count, 3)
    }
    func testSavedSyncPendingPreservesDocumentsAndWarnings() async throws {
        StubProtocol.handler = { _ in (200, "application/json", Data("""
        {"artifacts":[{"id":"a1","job_id":"j1","kind":"resume","filename":"saved.pdf"}],"questions":[],"operation_status":"saved_sync_pending","warnings":["Documents saved; activity log pending."]}
        """.utf8)) }
        let result = try await client().decode(PreparedDocuments.self, path: "jobs/j1/prepare", method: "POST")
        XCTAssertEqual(result.artifacts.first?.filename, "saved.pdf")
        XCTAssertTrue(try XCTUnwrap(result.savedSyncWarning).contains("Documents saved; activity log pending."))
        XCTAssertTrue(try XCTUnwrap(result.savedSyncWarning).contains("before requesting another AI operation"))
        let rank = try APIClient.decoder.decode(Opportunity.self, from: Data("""
        {"id":"j1","title":"Nurse","company_name":"Hospital","source_url":"https://example.test/j1","score":8.5,"operation_status":"saved_sync_pending","warnings":[]}
        """.utf8))
        XCTAssertEqual(rank.score, 8.5)
        XCTAssertTrue(try XCTUnwrap(rank.savedSyncWarning).contains("Outputs were saved"))
    }
    func testPartialStorageFailureRetainsSavedArtifactsAndNeverRetriesGeneration() async throws {
        var calls = 0
        StubProtocol.handler = { _ in
            calls += 1
            return (503, "application/json", Data("""
            {"detail":{"code":"artifact_recovery_required","operation_id":"11111111-1111-4111-8111-111111111111","state":"upload_pending","message":"Stored bytes require reconciliation.","saved_artifacts":[{"id":"a1","job_id":"j1","kind":"resume","filename":"saved.pdf"}]}}
            """.utf8))
        }
        do { _ = try await client().request("jobs/j1/prepare", method: "POST"); XCTFail("Must expose partial persistence") }
        catch AppFailure.recoveryRequired(let recovery) {
            XCTAssertEqual(recovery.savedArtifacts?.first?.id, "a1")
            XCTAssertTrue(recovery.userMessage.contains("Do not regenerate"))
            XCTAssertTrue(recovery.userMessage.contains("11111111-1111-4111-8111-111111111111"))
        }
        XCTAssertEqual(calls, 1)
    }
    func testArtifactRecoveryOnlyReconcilesKnownUploadAndNeverCallsPrepare() async throws {
        let id = "11111111-1111-4111-8111-111111111111"
        let recovery = StorageRecoveryRequired(code: "artifact_recovery_required", operationId: id, state: "upload_pending", message: "Recover stored bytes")
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.url?.path, "/api/mobile/artifact-operations/\(id)/recover")
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertTrue(try self.body(request).isEmpty)
            return (200, "application/json", Data("{\"id\":\"\(id)\",\"job_id\":\"j1\",\"kind\":\"resume\",\"filename\":\"recovered.pdf\"}".utf8))
        }
        let result = try await client().recoverArtifact(recovery, token: "test")
        XCTAssertEqual(result.id, id)
        var deletion = recovery; deletion.code = "resume_recovery_required"; deletion.state = "delete_pending"
        do { _ = try await client().recoverArtifact(deletion, token: "test"); XCTFail("Must not trigger deletion or resume recovery") }
        catch { XCTAssertTrue(error.localizedDescription.contains("manual recovery")) }
        XCTAssertEqual(calls, 1)
    }
    func testResumeStorageFailureExplainsRecoveryWithoutReplacementUpload() async throws {
        StubProtocol.handler = { _ in (503, "application/json", Data("""
        {"detail":{"code":"resume_recovery_required","operation_id":"11111111-1111-4111-8111-111111111111","state":"upload_pending","message":"Resume persistence is not confirmed."}}
        """.utf8)) }
        do { _ = try await client().request("resumes", method: "POST"); XCTFail("Must explain recovery") }
        catch AppFailure.recoveryRequired(let recovery) { XCTAssertTrue(recovery.userMessage.contains("replacement upload")) }
    }
    @MainActor func testPendingRecoveryListsUseFixedAuthenticatedRoutesAndIgnoreRetryURL() async throws {
        for kind in StorageKind.allCases {
            StubProtocol.handler = { request in
                XCTAssertEqual(request.url?.path, "/api/mobile/\(kind.rawValue)-operations")
                XCTAssertEqual(request.httpMethod, "GET")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer owner")
                return (200, "application/json", Data("""
                [{"id":"10000000-0000-4000-8000-000000000001","state":"upload_pending","filename":"stored.pdf","job_id":"20000000-0000-4000-8000-000000000001","retry_path":"https://wrong.example.test/steal-token"}]
                """.utf8))
            }
            let rows = try await client().pendingOperations(kind, token: "owner")
            XCTAssertEqual(rows.count, 1); XCTAssertTrue(rows[0].canRecoverStoredBytes)
        }
    }
    @MainActor func testRecoveryListsAcceptBelowCapButRejectFullOrOversizedPages() async throws {
        for kind in StorageKind.allCases {
            for count in [0, 199, 200, 201] {
                let payload = try JSONSerialization.data(withJSONObject: (0..<count).map { index in
                    ["id": String(format: "10000000-0000-4000-8000-%012d", index),
                     "state": "upload_pending", "filename": "stored.pdf",
                     "job_id": "20000000-0000-4000-8000-000000000001"]
                })
                StubProtocol.handler = { request in
                    XCTAssertEqual(request.httpMethod, "GET")
                    XCTAssertEqual(request.url?.path, "/api/mobile/\(kind.rawValue)-operations")
                    return (200, "application/json", payload)
                }
                do {
                    let rows = try await client().pendingOperations(kind, token: "owner")
                    XCTAssertLessThan(count, 200, "A full page cannot establish completeness")
                    XCTAssertEqual(rows.count, count)
                } catch {
                    XCTAssertGreaterThanOrEqual(count, 200)
                    XCTAssertTrue(error.localizedDescription.contains("may be incomplete"))
                }
            }
        }
    }
    @MainActor func testFullRecoveryPageRetainsKnownRowsAndBlocksUnlistedJobAndRecoveryWrite() async throws {
        let operation = PendingStorageOperation(id: "10000000-0000-4000-8000-000000000001", state: "upload_pending",
                                               filename: "stored.pdf", jobId: "20000000-0000-4000-8000-000000000001")
        let hiddenJob = "20000000-0000-4000-8000-000000000002"
        var count = 1
        var methods: [String] = []
        StubProtocol.handler = { request in
            methods.append(request.httpMethod ?? "")
            XCTAssertEqual(request.url?.path, "/api/mobile/artifact-operations")
            return (200, "application/json", try JSONSerialization.data(withJSONObject: (1...count).map { index in
                ["id": String(format: "10000000-0000-4000-8000-%012d", index),
                 "state": "upload_pending", "filename": "stored.pdf", "job_id": operation.jobId!]
            }))
        }
        let service = client()
        let controller = StorageRecoveryController()
        await controller.refresh(.artifact, service: service, token: "owner")
        XCTAssertEqual(controller.list(.artifact).operations, [operation])
        XCTAssertFalse(controller.blocksPreparation(jobId: hiddenJob))
        count = 200
        await controller.refresh(.artifact, service: service, token: "owner")
        XCTAssertEqual(controller.list(.artifact).operations, [operation], "Do not replace known rows with a truncated page")
        XCTAssertTrue(controller.list(.artifact).error?.contains("may be incomplete") == true)
        XCTAssertTrue(controller.blocksPreparation(jobId: hiddenJob), "The first 200 unrelated rows cannot clear an unlisted job")
        XCTAssertThrowsError(try controller.consumePreparationAllowances(jobId: hiddenJob))
        do {
            _ = try await service.recoverStoredBytes(.artifact, operation: operation, token: "owner")
            XCTFail("A capped preflight must not send a recovery write")
        } catch { XCTAssertTrue(error.localizedDescription.contains("may be incomplete")) }
        XCTAssertEqual(methods, ["GET", "GET", "GET"], "No AI, recovery POST or deletion may be called")
    }
    @MainActor func testResumeRecoveryRechecksUploadStateAndNeverFollowsRetryURL() async throws {
        let id = "10000000-0000-4000-8000-000000000001"
        let operation = PendingStorageOperation(id: id, state: "upload_pending", filename: "stored.pdf")
        var paths: [String] = []
        StubProtocol.handler = { request in
            paths.append(request.url!.path)
            if request.httpMethod == "GET" {
                return (200, "application/json", Data("[{\"id\":\"\(id)\",\"state\":\"upload_pending\",\"filename\":\"stored.pdf\"}]".utf8))
            }
            XCTAssertEqual(request.httpMethod, "POST")
            return (200, "application/json", Data("{\"id\":\"\(id)\",\"label\":\"Recovered\",\"original_filename\":\"stored.pdf\"}".utf8))
        }
        let result = try await client().recoverStoredBytes(.resume, operation: operation, token: "owner")
        guard case .resume(let resume) = result else { return XCTFail("Expected resume") }
        XCTAssertEqual(resume.id, id)
        XCTAssertEqual(paths, ["/api/mobile/resume-operations", "/api/mobile/resumes/\(id)/recover"])
    }
    @MainActor func testChangedToDeletePendingPreventsRecoveryWrite() async throws {
        let id = "10000000-0000-4000-8000-000000000001"
        var requests = 0
        StubProtocol.handler = { request in
            requests += 1; XCTAssertEqual(request.httpMethod, "GET")
            return (200, "application/json", Data("[{\"id\":\"\(id)\",\"state\":\"delete_pending\",\"filename\":\"stored.pdf\"}]".utf8))
        }
        do { _ = try await client().recoverStoredBytes(.resume, operation: PendingStorageOperation(id: id, state: "upload_pending", filename: "stored.pdf"), token: "owner"); XCTFail("Must not resume a deletion") }
        catch { XCTAssertTrue(error.localizedDescription.contains("no recovery write")) }
        XCTAssertEqual(requests, 1)
    }
    @MainActor func testFreshSourceUploadRetainsIdempotencyKeyAndUsesNoAIEndpoint() async throws {
        let upload = FreshResumeUpload(idempotencyKey: UUID(), filename: "fresh.pdf", content: Data("synthetic".utf8), label: "New source", roleFocus: "")
        var keys: [String] = []
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mobile/resumes"); XCTAssertEqual(request.httpMethod, "POST")
            keys.append(try XCTUnwrap(request.value(forHTTPHeaderField: "Idempotency-Key")))
            let body = try self.body(request)
            XCTAssertEqual(body["filename"] as? String, "fresh.pdf")
            XCTAssertEqual(body["content_base64"] as? String, upload.content.base64EncodedString())
            return (200, "application/json", Data("{\"id\":\"30000000-0000-4000-8000-000000000001\",\"label\":\"New source\",\"original_filename\":\"fresh.pdf\"}".utf8))
        }
        for _ in 0..<2 { _ = try await client().uploadFreshResume(upload, token: "owner") }
        XCTAssertEqual(keys, [upload.idempotencyKey.uuidString, upload.idempotencyKey.uuidString])
    }
    @MainActor func testInvalidRecoveryMetadataAndUnsafeUploadAreRejected() async throws {
        StubProtocol.handler = { _ in (200, "application/json", Data("[{\"id\":\"../resumes\",\"state\":\"upload_pending\",\"filename\":\"stored.pdf\"}]".utf8)) }
        do { _ = try await client().pendingOperations(.resume, token: "owner"); XCTFail("Must reject malformed identity") }
        catch { XCTAssertTrue(error.localizedDescription.contains("invalid recovery metadata")) }
        StubProtocol.handler = { _ in XCTFail("Unsupported source must not be uploaded"); return (500, "application/json", Data()) }
        do { _ = try await client().uploadFreshResume(FreshResumeUpload(idempotencyKey: UUID(), filename: "source.exe", content: Data([1]), label: "Source", roleFocus: ""), token: "owner"); XCTFail("Unsafe type") } catch {}
    }
}
