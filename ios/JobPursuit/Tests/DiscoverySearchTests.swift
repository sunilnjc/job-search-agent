import XCTest
@testable import JobPursuit

private final class DiscoveryStubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, data) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class DiscoverySearchTests: XCTestCase {
    private let publicID = "ats_" + String(repeating: "a", count: 64)
    private let savedID = "11111111-1111-4111-8111-111111111111"
    override func tearDown() { DiscoveryStubProtocol.handler = nil; super.tearDown() }
    private func fixture() -> [String: Any] {
        ["status": "ok", "results": [[
            "source_id": publicID, "source": "greenhouse/example", "provider": "greenhouse", "board": "example", "external_id": "123",
            "title": "Nurse", "company_name": "Example Hospital", "company_name_is_board_identifier": true,
            "source_url": "https://careers.example.test/role?gh_jid=123", "description": "Current registration required.",
            "location_text": NSNull(), "workplace_type": "unknown", "content_truncated": false,
            "fetched_at": "2026-09-15T10:00:00Z", "source_published_at": NSNull(), "match_reasons": ["Target title"],
            "eligibility_status": "unknown", "eligibility": ["status": "unknown", "provisional": true, "independently_verified": false, "reasons": ["Confirm registration and work rights"]],
            "persisted": false,
            "relevance": ["method": "profile_rules_v1", "score": 65.5, "reasons": ["Nursing experience"], "gaps": ["Registration not confirmed"], "review_required": true]
        ]], "sources": [[
            "source": "greenhouse/example", "status": "ok", "cached": false, "fetched_at": NSNull(), "checked_at": "2026-09-15T10:00:00Z",
            "truncated": false, "received_count": 2, "returned_count": 1, "dropped_count": 1, "unlisted_count": 0, "duplicate_count": 0
        ]], "partial": false, "truncated": false, "matched_count": 1, "returned_count": 1,
         "searched_at": "2026-09-15T10:00:00Z", "persisted": false, "eligibility_verified": false]
    }
    private func data(_ object: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: object) }
    private func response(_ object: [String: Any]) throws -> DiscoveryResponse { try DiscoveryResponse.checked(data(object)) }
    private func jobFixture(_ mutate: (inout [String: Any]) -> Void) -> [String: Any] {
        var root = fixture(); var job = (root["results"] as! [[String: Any]])[0]
        mutate(&job); root["results"] = [job]; return root
    }
    private func scope(owner: String = "owner-a", authority: String = "https://auth.example.test", apiBase: String = "https://api.example.test", sessionID: String = "one", profile: CandidateProfile? = nil, preferences: Preferences? = nil) -> DiscoveryScope {
        DiscoveryScope(owner: owner, authority: authority, apiBase: apiBase, sessionID: sessionID, profile: profile, preferences: preferences)
    }
    private func client() -> APIClient {
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [DiscoveryStubProtocol.self]
        return APIClient(configuration: AppConfiguration(apiBase: "https://api.example.test", supabaseUrl: "https://auth.example.test", publishableKey: "unused"), transport: URLSession(configuration: config))
    }
    private func body(_ request: URLRequest) throws -> [String: Any] {
        var bytes = request.httpBody ?? Data()
        if bytes.isEmpty, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }; bytes.append(contentsOf: buffer.prefix(count))
            }
        }
        return try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
    }
    func testRealSourceAndOptionalRelevanceDecodeWithoutAssumingEligibility() throws {
        let result = try response(fixture()); let job = try XCTUnwrap(result.results.first)
        XCTAssertEqual(job.id, publicID); XCTAssertNil(UUID(uuidString: job.id))
        XCTAssertEqual(job.relevance?.score, 65.5); XCTAssertEqual(job.relevance?.method, "profile_rules_v1")
        XCTAssertEqual(job.relevance?.gaps, ["Registration not confirmed"])
        XCTAssertEqual(job.eligibility.status, "unknown"); XCTAssertFalse(job.eligibility.independentlyVerified)
        XCTAssertNil(job.locationText); XCTAssertEqual(job.workplaceType, "unknown")
        XCTAssertTrue(job.companyNameIsBoardIdentifier == true)
        XCTAssertNil(try response(jobFixture { $0.removeValue(forKey: "relevance") }).results[0].relevance)
    }
    func testMalformedPublicIDsFailClosed() {
        for id in [savedID, "ats_123", "ats_" + String(repeating: "A", count: 64), publicID + "/rank", "../jobs", publicID + "\n"] {
            XCTAssertThrowsError(try response(jobFixture { $0["source_id"] = id }), id)
        }
    }
    func testMalformedAndCredentialBearingURLsFailClosed() {
        for url in ["javascript:alert(1)", "file:///tmp/a", "https:///", "https://user:secret@example.test/job", "https://example.test/a b", "https://example.test/%xx", "https://example.test/\njob", "/jobs/123"] {
            XCTAssertThrowsError(try response(jobFixture { $0["source_url"] = url }), url)
        }
        XCTAssertNotNil(discoveryListingURL("https://example.test/job?gh_jid=123"))
        XCTAssertNotNil(discoveryListingURL("http://example.test/job"))
    }
    func testUnknownEligibilityAndPersistenceContractsAreRejected() {
        for key in ["persisted", "eligibility_verified"] {
            var raw = fixture(); raw[key] = true; XCTAssertThrowsError(try response(raw))
        }
        XCTAssertThrowsError(try response(jobFixture { $0["persisted"] = true }))
        XCTAssertThrowsError(try response(jobFixture { $0["eligibility_status"] = "eligible" }))
        for (key, value) in [("status", "eligible" as Any), ("provisional", false as Any), ("independently_verified", true as Any)] {
            XCTAssertThrowsError(try response(jobFixture { job in
                var eligibility = job["eligibility"] as! [String: Any]; eligibility[key] = value; job["eligibility"] = eligibility
            }))
        }
        XCTAssertThrowsError(try response(jobFixture { $0["workplace_type"] = "maybe" }))
    }
    func testUnsupportedAndMalformedRelevanceIsRejected() {
        for (key, value) in [("method", "ai_v1" as Any), ("score", -1 as Any), ("score", 101 as Any), ("score", "90" as Any), ("reasons", "not a list" as Any), ("gaps", [true] as Any), ("review_required", "false" as Any)] {
            XCTAssertThrowsError(try response(jobFixture { job in
                var relevance = job["relevance"] as! [String: Any]; relevance[key] = value; job["relevance"] = relevance
            }))
        }
        for score in [0, 100] {
            XCTAssertNoThrow(try response(jobFixture { job in
                var relevance = job["relevance"] as! [String: Any]; relevance["score"] = score; job["relevance"] = relevance
            }))
        }
    }
    func testMalformedCountsDuplicatesBoundsAndStatusesAreRejected() {
        var raw = fixture(); raw["returned_count"] = 2; XCTAssertThrowsError(try response(raw))
        raw = fixture(); raw["matched_count"] = -1; XCTAssertThrowsError(try response(raw))
        raw = fixture(); raw["status"] = "new_contract"; XCTAssertThrowsError(try response(raw))
        raw = fixture(); raw["status"] = "unavailable"; XCTAssertThrowsError(try response(raw))
        raw = fixture(); raw["results"] = Array(repeating: (raw["results"] as! [[String: Any]])[0], count: 2)
        raw["returned_count"] = 2; raw["matched_count"] = 2; XCTAssertThrowsError(try response(raw))
        raw = fixture(); raw["sources"] = Array(repeating: (raw["sources"] as! [[String: Any]])[0], count: 9); XCTAssertThrowsError(try response(raw))
        XCTAssertThrowsError(try response(jobFixture { $0["description"] = String(repeating: "x", count: 16001) }))
        XCTAssertThrowsError(try response(jobFixture { $0["title"] = " \n " }))
        XCTAssertThrowsError(try response(jobFixture { $0["match_reasons"] = Array(repeating: "x", count: 51) }))
        XCTAssertThrowsError(try DiscoveryResponse.checked(Data("<html>secret</html>".utf8)))
    }
    func testSavePayloadContainsOnlyExplicitImportFields() throws {
        let job = try response(fixture()).results[0]; let payload = try job.savePayload()
        XCTAssertEqual(payload.keys.sorted(), ["company_name", "description", "location_text", "source_url", "title"])
        XCTAssertTrue(payload["location_text"] is NSNull)
        XCTAssertNil(payload["id"]); XCTAssertNil(payload["source_id"]); XCTAssertNil(payload["score"]); XCTAssertNil(payload["eligibility_status"])
    }
    func testSearchPostsStoredPreferenceDefaultsOnceWithAuthentication() async throws {
        var calls = 0
        DiscoveryStubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.url?.path, "/api/mobile/discovery/search"); XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
            let body = try self.body(request)
            XCTAssertEqual(body.keys.sorted(), ["filters", "limit", "query"])
            XCTAssertEqual(body["query"] as? String, ""); XCTAssertEqual(body["limit"] as? Int, 20)
            let filters = try XCTUnwrap(body["filters"] as? [String: Any])
            XCTAssertEqual(filters["titles"] as? [String], []); XCTAssertEqual(filters["locations"] as? [String], [])
            XCTAssertEqual(filters["workplace_type"] as? String, "any")
            return (200, try self.data(self.fixture()))
        }
        let result = try await client().discoverySearch(token: "synthetic-token")
        XCTAssertEqual(result.results.count, 1)
        XCTAssertEqual(calls, 1)
    }
    func testUnavailable503RetainsCoverageNotSuccessfulEmpty() async throws {
        var raw = fixture(); raw["status"] = "unavailable"; raw["results"] = []; raw["returned_count"] = 0; raw["matched_count"] = 0
        var source = (raw["sources"] as! [[String: Any]])[0]; source["status"] = "error"; source["retry_after"] = 60; raw["sources"] = [source]
        let bytes = try data(raw)
        DiscoveryStubProtocol.handler = { _ in (503, bytes) }
        let value = try await client().discoverySearch(token: "synthetic-token")
        XCTAssertEqual(value.status, "unavailable"); XCTAssertEqual(value.sources[0].retryAfter, 60)
    }
    func testHTTPFailuresAreSanitizedAndNeverRetried() async {
        for (status, expected) in [(401, DiscoveryFailure.signIn), (403, .signIn), (429, .rateLimited), (404, .notSupported), (500, .network), (302, .network)] {
            var calls = 0
            DiscoveryStubProtocol.handler = { _ in calls += 1; return (status, Data("{\"detail\":\"secret token and private URL\"}".utf8)) }
            do { _ = try await client().discoverySearch(token: "synthetic-token"); XCTFail("Must fail") }
            catch { XCTAssertEqual(error as? DiscoveryFailure, expected); XCTAssertFalse(error.localizedDescription.contains("secret")) }
            XCTAssertEqual(calls, 1)
        }
        XCTAssertEqual(DiscoveryFailure.sanitized(AppFailure.message("private token")), .network)
        XCTAssertEqual(DiscoveryFailure.sanitized(CancellationError()), .cancelled)
        XCTAssertEqual(DiscoveryFailure.sanitized(URLError(.cancelled)), .cancelled)
    }
    func testExplicitSaveUsesJobsEndpointAndReturnedUUIDOnly() async throws {
        let job = try response(fixture()).results[0]; var calls = 0
        DiscoveryStubProtocol.handler = { request in
            calls += 1; XCTAssertEqual(request.url?.path, "/api/mobile/jobs"); XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(try self.body(request).keys.sorted(), ["company_name", "description", "location_text", "source_url", "title"])
            return (201, try self.data(["id": self.savedID, "title": job.title, "company_name": job.companyName, "source_url": job.sourceUrl, "eligibility_status": "unknown"]))
        }
        let saved = try await client().saveDiscoveryJob(job, token: "synthetic-token")
        XCTAssertEqual(saved.id, savedID); XCTAssertNil(saved.score); XCTAssertEqual(saved.eligibilityStatus, "unknown"); XCTAssertEqual(calls, 1)
    }
    func testSaveRejectsPublicOrMalformedIdentityAndWrongURL() async throws {
        let job = try response(fixture()).results[0]
        for (id, url) in [(publicID, job.sourceUrl), ("../bad", job.sourceUrl), (savedID, "https://different.example.test/job")] {
            DiscoveryStubProtocol.handler = { _ in (200, try self.data(["id": id, "title": job.title, "company_name": job.companyName, "source_url": url])) }
            do { _ = try await client().saveDiscoveryJob(job, token: "synthetic-token"); XCTFail("Must reject uncertain save") }
            catch { XCTAssertEqual(error as? DiscoveryFailure, .uncertainSave) }
        }
    }
    func testCacheCoalescesOpenAndRefreshAndExpiresAtFiveMinutes() throws {
        var cache = DiscoverySearchState(); let key = scope(); let now = Date(timeIntervalSince1970: 1000)
        let request = try XCTUnwrap(cache.begin(scope: key, refresh: false, now: now))
        XCTAssertNil(cache.begin(scope: key, refresh: true, now: now)); XCTAssertNil(cache.begin(scope: key, refresh: false, now: now))
        cache.finish(id: request, scope: key, response: try response(fixture()))
        XCTAssertNil(cache.begin(scope: key, refresh: false, now: now.addingTimeInterval(299)))
        XCTAssertNotNil(cache.response)
        XCTAssertNotNil(cache.begin(scope: key, refresh: false, now: now.addingTimeInterval(300)))
        XCTAssertNil(cache.response)
    }
    func testFailureAndCancellationRequireExplicitRefreshWithinLifetime() throws {
        for failure in [DiscoveryFailure.rateLimited, .network, .cancelled] {
            var cache = DiscoverySearchState(); let key = scope(); let now = Date()
            let request = try XCTUnwrap(cache.begin(scope: key, refresh: false, now: now))
            cache.finish(id: request, scope: key, failure: failure)
            XCTAssertFalse(cache.isLoading); XCTAssertEqual(cache.failure, failure)
            XCTAssertNil(cache.begin(scope: key, refresh: false, now: now.addingTimeInterval(20)))
            XCTAssertNotNil(cache.begin(scope: key, refresh: true, now: now.addingTimeInterval(20)))
        }
    }
    func testCacheInvalidatesAcrossOwnerAuthoritySessionFactsAndEveryPreference() throws {
        let base = scope()
        var variants = [scope(owner: "owner-b"), scope(authority: "https://other-auth.example.test"), scope(apiBase: "https://other-api.example.test"), scope(sessionID: "two"), scope(profile: CandidateProfile(careerText: "Confirmed resume facts")), scope(profile: CandidateProfile(careerBackground: CareerBackground(profession: "Nursing")))]
        for raw: [String: Any] in [["target_titles": ["Nurse"]], ["preferred_locations": ["Dubai"]], ["preferred_regions": ["Gulf"]], ["remote_preference": "remote"], ["sponsorship_required": true], ["work_authorization_notes": "Unknown"], ["minimum_match_score": 8]] {
            variants.append(scope(preferences: try APIClient.decoder.decode(Preferences.self, from: data(raw))))
        }
        for changed in variants {
            XCTAssertNotEqual(base, changed)
            var cache = DiscoverySearchState(); let old = try XCTUnwrap(cache.begin(scope: base, refresh: false))
            let new = try XCTUnwrap(cache.begin(scope: changed, refresh: false))
            cache.finish(id: old, scope: base, response: try response(fixture()))
            XCTAssertEqual(cache.requestID, new); XCTAssertNil(cache.response)
        }
    }
    func testScopeIgnoresUIOnlyQualificationIdentityAndDisplayName() {
        let first = CandidateProfile(displayName: "First", careerBackground: CareerBackground(qualifications: [CareerQualification(name: "Registration")]))
        let second = CandidateProfile(displayName: "Second", careerBackground: CareerBackground(qualifications: [CareerQualification(name: "Registration")]))
        XCTAssertEqual(scope(profile: first), scope(profile: second))
        XCTAssertEqual(scope().digest.count, 64)
    }
    func testClockRollbackDoesNotReuseSnapshot() throws {
        var cache = DiscoverySearchState(); let key = scope(); let now = Date()
        let id = try XCTUnwrap(cache.begin(scope: key, refresh: false, now: now))
        cache.finish(id: id, scope: key, response: try response(fixture()))
        XCTAssertNotNil(cache.begin(scope: key, refresh: false, now: now.addingTimeInterval(-1)))
    }
    @MainActor func testPreviewAndUITestingNeverSearchSaveOrRestoreRealSession() async throws {
        let store = AppStore(isUITesting: true); store.enterPreview()
        await store.loadDiscovery(); await store.loadDiscovery(refresh: true)
        let before = store.workspace.jobs.count
        await store.saveDiscoveryRole(try response(fixture()).results[0])
        XCTAssertEqual(store.workspace.jobs.count, before); XCTAssertFalse(store.discovery.isLoading); XCTAssertNil(store.discovery.response)
        XCTAssertNil(store.discoveryScope); XCTAssertFalse(store.isBusy)
        await store.signOut(); XCTAssertTrue(store.uncertainDiscoverySaves.isEmpty)
    }
}
