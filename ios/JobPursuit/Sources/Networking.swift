import Foundation
import Security

enum AppFailure: LocalizedError {
    case message(String)
    case inputRequired(String, [CandidateQuestion])
    case recoveryRequired(StorageRecoveryRequired)
    var errorDescription: String? {
        switch self {
        case .message(let text), .inputRequired(let text, _): return text
        case .recoveryRequired(let recovery): return recovery.userMessage
        }
    }
}

private struct InputRequiredResponse: Decodable { var message: String; var questions: [CandidateQuestion] }

struct AppConfiguration: Equatable {
    var apiBase: String
    var supabaseUrl: String
    var publishableKey: String
    static func load() -> AppConfiguration {
        func value(_ key: String) -> String { UserDefaults.standard.string(forKey: key) ?? Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "" }
        return AppConfiguration(apiBase: value("API_BASE_URL"), supabaseUrl: value("SUPABASE_URL"), publishableKey: value("SUPABASE_PUBLISHABLE_KEY"))
    }
    static func secureURL(_ raw: String) -> URL? {
        guard let u = URL(string: raw), u.scheme == "https", u.host != nil, u.user == nil, u.password == nil, u.query == nil, u.fragment == nil else { return nil }
        return u
    }
    static func listingURL(_ raw: String) -> URL? {
        guard let u = URL(string: raw), ["https", "http"].contains(u.scheme?.lowercased() ?? ""), u.host != nil, u.user == nil, u.password == nil else { return nil }
        // Employer links commonly use job IDs in queries. These are browser
        // handoffs, never API origins and never receive an authentication token.
        return u
    }
    static func isPublicKey(_ key: String) -> Bool {
        if key.range(of: "^sb_publishable_[A-Za-z0-9_-]{20,}$", options: .regularExpression) != nil { return true }
        let parts = key.split(separator: ".")
        guard parts.count == 3 else { return false }
        var part = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        part += String(repeating: "=", count: (4 - part.count % 4) % 4)
        guard let data = Data(base64Encoded: part), let claims = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return false }
        // This only identifies the key type; Supabase validates it remotely.
        return claims["role"] as? String == "anon"
    }
    var isReady: Bool { Self.secureURL(apiBase) != nil && Self.secureURL(supabaseUrl) != nil && Self.isPublicKey(publishableKey) }
    func save() {
        UserDefaults.standard.set(apiBase, forKey: "API_BASE_URL")
        UserDefaults.standard.set(supabaseUrl, forKey: "SUPABASE_URL")
        UserDefaults.standard.set(publishableKey, forKey: "SUPABASE_PUBLISHABLE_KEY")
    }
}

enum SecureSession {
    private static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.thejobpursuit.session", kSecAttrAccount as String: "supabase"] }
    static func save(_ session: Session) throws {
        let data = try JSONEncoder().encode(session)
        clear()
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(q as CFDictionary, nil) == errSecSuccess else { throw AppFailure.message("Could not securely save your session. Please sign in again.") }
    }
    static func load() -> Session? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Session.self, from: data)
    }
    static func clear() { SecItemDelete(query as CFDictionary) }
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

struct APIClient {
    let configuration: AppConfiguration
    static let privateTransport: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.urlCache = nil
        return URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }()
    var transport: URLSession = APIClient.privateTransport
    static var decoder: JSONDecoder { let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d }

    func request(_ path: String, method: String = "GET", token: String? = nil, body: [String: Any]? = nil, auth: Bool = false, idempotencyKey: String? = nil) async throws -> Data {
        let raw = auth ? configuration.supabaseUrl : configuration.apiBase
        guard let base = AppConfiguration.secureURL(raw) else { throw AppFailure.message("Configure a valid HTTPS server and Supabase project in Connection settings.") }
        let url = base.appendingPathComponent(auth ? "auth/v1/\(path)" : "api/mobile/\(path)")
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        // Queries are fixed protocol parameters, never part of the user-supplied path.
        if path == "token" { components.queryItems = [URLQueryItem(name: "grant_type", value: "refresh_token")] }
        var r = URLRequest(url: components.url!)
        r.httpMethod = method
        r.timeoutInterval = 120
        r.cachePolicy = .reloadIgnoringLocalCacheData
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        if auth { r.setValue(configuration.publishableKey, forHTTPHeaderField: "apikey") }
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let idempotencyKey {
            guard !auth, UUID(uuidString: idempotencyKey) != nil else { throw AppFailure.message("Invalid upload attempt identity.") }
            r.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
        }
        if let body { r.httpBody = try JSONSerialization.data(withJSONObject: body); r.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await transport.data(for: r)
        guard let http = response as? HTTPURLResponse else { throw AppFailure.message("The server returned an invalid response.") }
        guard (200..<300).contains(http.statusCode) else {
            if (300..<400).contains(http.statusCode) { throw AppFailure.message("The API redirected to another page. Route /api/mobile to the authenticated mobile service; don't send it through the website's login page.") }
            if http.statusCode == 401 { throw AppFailure.message("Your session has expired. Sign out and sign in again.") }
            if http.statusCode == 429 { throw AppFailure.message("Too many requests. Wait a little before trying again.") }
            let details = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            if !auth, let detail = details?["detail"] as? [String: Any],
               let code = detail["code"] as? String,
               ["artifact_recovery_required", "resume_recovery_required", "artifact_upload_bytes_required", "resume_upload_bytes_required"].contains(code),
               let encoded = try? JSONSerialization.data(withJSONObject: detail),
               let recovery = try? Self.decoder.decode(StorageRecoveryRequired.self, from: encoded) {
                throw AppFailure.recoveryRequired(recovery)
            }
            if !auth, http.statusCode == 422, let detail = details?["detail"] as? [String: Any],
               let encoded = try? JSONSerialization.data(withJSONObject: detail),
               let input = try? Self.decoder.decode(InputRequiredResponse.self, from: encoded) {
                throw AppFailure.inputRequired(input.message, input.questions)
            }
            let message = details?["detail"] as? String ?? details?["msg"] as? String ?? details?["error_description"] as? String
            throw AppFailure.message(message ?? "The service couldn't complete this request (\(http.statusCode)). Please try again.")
        }
        if !auth && http.value(forHTTPHeaderField: "Content-Type")?.contains("text/html") == true { throw AppFailure.message("The mobile API is not routed yet. Your website returned a page instead of app data. Check the deployment connection.") }
        return data
    }
    func decode<T: Decodable>(_ type: T.Type, path: String, method: String = "GET", token: String? = nil, body: [String: Any]? = nil, auth: Bool = false) async throws -> T {
        let data = try await request(path, method: method, token: token, body: body, auth: auth)
        do { return try Self.decoder.decode(type, from: data) }
        catch { throw AppFailure.message("The server response doesn't match this app version. Refresh or check the mobile API deployment.") }
    }
    func jobDetail(id: String, token: String) async throws -> Opportunity {
        let job = try await decode(Opportunity.self, path: "jobs/\(id)", token: token)
        guard job.id == id else { throw AppFailure.message("The server returned a different opportunity. Refresh and try again.") }
        return job
    }
    func reviewEligibility(jobId: String, draft: EligibilityReviewDraft, token: String) async throws -> Opportunity {
        guard draft.isValid else { throw AppFailure.message("Add a reason (up to 2,000 characters) and explicitly confirm your self-report.") }
        let job = try await decode(Opportunity.self, path: "jobs/\(jobId)/eligibility", method: "POST", token: token,
                                   body: ["status": draft.status.rawValue, "reason": draft.trimmedReason, "confirmed": true])
        guard job.id == jobId, let review = job.eligibilityReview, review.confirmed,
              review.status == draft.status.rawValue, review.provenance == "user_self_report", review.independentlyVerified == false else {
            throw AppFailure.message("The service did not return a confirmed self-report. Refresh this opportunity before trying again.")
        }
        return job
    }
    func updateJob(_ edits: OpportunityEdits, token: String) async throws -> Opportunity {
        guard edits.isValid else { throw AppFailure.message("Check the opportunity fields and make at least one change before saving.") }
        let job = try await decode(Opportunity.self, path: "jobs/\(edits.original.id)", method: "PATCH", token: token, body: edits.changes)
        guard job.id == edits.original.id else { throw AppFailure.message("The server returned a different opportunity. Refresh and try again.") }
        return job
    }
    func recoverArtifact(_ recovery: StorageRecoveryRequired, token: String) async throws -> Artifact {
        guard recovery.code == "artifact_recovery_required", recovery.state == "upload_pending", UUID(uuidString: recovery.operationId) != nil else {
            throw AppFailure.message("This operation requires manual recovery; no files were changed.")
        }
        let artifact = try await decode(Artifact.self, path: "artifact-operations/\(recovery.operationId)/recover", method: "POST", token: token, body: [:])
        guard artifact.id == recovery.operationId else { throw AppFailure.message("The recovery response did not match the stored operation.") }
        return artifact
    }
    func saveProfileAndPreferences(profile: [String: Any], preferences: [String: Any], onboardingCompletedAt: String?, token: String) async throws {
        _ = try await request("profile", method: "PUT", token: token, body: profile)
        _ = try await request("preferences", method: "PUT", token: token, body: preferences)
        let name = (profile["display_name"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let roles = preferences["target_titles"] as? [String] ?? []
        // Match web's required name + target-role assessment. Never mark partial
        // saves complete or replace an existing completion timestamp.
        if onboardingCompletedAt == nil, !name.isEmpty, roles.contains(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) {
            do {
                _ = try await request("profile", method: "PUT", token: token,
                                      body: ["onboarding_completed_at": ISO8601DateFormatter().string(from: Date())])
            } catch {
                throw AppFailure.message("Profile and preferences saved, but shared setup completion could not be recorded. Save your profile again to retry.")
            }
        }
    }
}
