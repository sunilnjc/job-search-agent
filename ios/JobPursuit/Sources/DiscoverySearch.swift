import Foundation
import CryptoKit

/// Public postings are deliberately not Opportunities: ats_* is not a saved UUID.
struct DiscoveryJob: Decodable, Identifiable {
    let sourceId: String
    let source: String
    let provider: String
    let board: String
    let externalId: String
    let title: String
    let companyName: String
    let companyNameIsBoardIdentifier: Bool?
    let sourceUrl: String
    let description: String
    let locationText: String?
    let workplaceType: String
    let contentTruncated: Bool
    let fetchedAt: String
    let sourcePublishedAt: String?
    let sourceCreatedAt: String?
    let sourceUpdatedAt: String?
    let matchReasons: [String]
    let eligibilityStatus: String
    let eligibility: DiscoveryEligibility
    let persisted: Bool
    let relevance: DiscoveryRelevance?
    var id: String { sourceId }

    func validate() throws {
        guard sourceId.utf8.count == 68, sourceId.range(of: "^ats_[a-f0-9]{64}$", options: .regularExpression) != nil,
              discoveryText(title, 160, required: true), discoveryText(companyName, 160, required: true),
              discoveryText(description, 16000, required: true), discoveryText(sourceUrl, 2048),
              discoveryListingURL(sourceUrl) != nil, discoveryText(source, 200), discoveryText(provider, 100),
              discoveryText(board, 100), discoveryText(externalId, 300),
              locationText.map({ discoveryText($0, 300) }) ?? true,
              ["remote", "hybrid", "onsite", "unknown"].contains(workplaceType), discoveryText(fetchedAt, 100),
              [sourcePublishedAt, sourceCreatedAt, sourceUpdatedAt].allSatisfy({ $0.map { discoveryText($0, 100) } ?? true }),
              discoveryTexts(matchReasons), !persisted, eligibilityStatus == "unknown",
              eligibility.status == "unknown", eligibility.provisional, !eligibility.independentlyVerified,
              discoveryTexts(eligibility.reasons) else { throw DiscoveryFailure.invalidResponse }
        if let relevance {
            guard relevance.method == "profile_rules_v1", relevance.score.isFinite, (0...100).contains(relevance.score),
                  discoveryTexts(relevance.reasons), discoveryTexts(relevance.gaps) else { throw DiscoveryFailure.invalidResponse }
        }
    }

    func savePayload() throws -> [String: Any] {
        try validate()
        // No public identity, score, eligibility claim or AI instruction is saved.
        return ["source_url": sourceUrl, "title": title, "company_name": companyName,
                "description": description, "location_text": locationText as Any? ?? NSNull()]
    }
}

struct DiscoveryEligibility: Decodable {
    let status: String
    let provisional: Bool
    let independentlyVerified: Bool
    let reasons: [String]
}

struct DiscoveryRelevance: Decodable {
    let method: String
    let score: Double
    let reasons: [String]
    let gaps: [String]
    let reviewRequired: Bool
}

struct DiscoverySource: Decodable {
    let source: String
    let status: String
    let cached: Bool
    let fetchedAt: String?
    let checkedAt: String
    let truncated: Bool
    let receivedCount: Int
    let returnedCount: Int
    let droppedCount: Int
    let unlistedCount: Int
    let duplicateCount: Int
    let errorCode: String?
    let retryAfter: Int?
}

struct DiscoveryResponse: Decodable {
    let status: String
    let results: [DiscoveryJob]
    let sources: [DiscoverySource]
    let partial: Bool
    let truncated: Bool
    let matchedCount: Int
    let returnedCount: Int
    let searchedAt: String
    let persisted: Bool
    let eligibilityVerified: Bool
    let warnings: [String]?
    let ranking: String?

    static func checked(_ data: Data) throws -> Self {
        do {
            guard data.count <= 2 * 1024 * 1024 else { throw DiscoveryFailure.invalidResponse }
            let value = try APIClient.decoder.decode(Self.self, from: data)
            guard ["ok", "partial", "unavailable"].contains(value.status), !value.persisted, !value.eligibilityVerified,
                  value.results.count <= 50, value.sources.count <= 8, value.matchedCount >= value.returnedCount,
                  value.returnedCount == value.results.count, discoveryText(value.searchedAt, 100),
                  value.warnings.map(discoveryTexts) ?? true,
                  Set(value.results.map(\.sourceId)).count == value.results.count,
                  value.status != "unavailable" || value.results.isEmpty else { throw DiscoveryFailure.invalidResponse }
            for job in value.results { try job.validate() }
            for source in value.sources {
                guard discoveryText(source.source, 200), ["ok", "partial", "error"].contains(source.status),
                      [source.receivedCount, source.returnedCount, source.droppedCount, source.unlistedCount, source.duplicateCount].allSatisfy({ $0 >= 0 }),
                      source.fetchedAt.map({ discoveryText($0, 100) }) ?? true, discoveryText(source.checkedAt, 100),
                      source.errorCode.map({ discoveryText($0, 200) }) ?? true,
                      source.retryAfter.map({ $0 >= 0 }) ?? true else { throw DiscoveryFailure.invalidResponse }
            }
            return value
        } catch { throw DiscoveryFailure.invalidResponse }
    }
}

private func discoveryText(_ value: String, _ maximum: Int, required: Bool = false) -> Bool {
    value.utf16.count <= maximum && (!required || !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
}
private func discoveryTexts(_ values: [String]) -> Bool { values.count <= 50 && values.allSatisfy { discoveryText($0, 2000) } }

func discoveryListingURL(_ raw: String) -> URL? {
    guard !raw.isEmpty, raw.rangeOfCharacter(from: .whitespacesAndNewlines.union(.controlCharacters)) == nil,
          raw.range(of: "%(?![0-9A-Fa-f]{2})", options: .regularExpression) == nil,
          let url = AppConfiguration.listingURL(raw), let host = url.host, !host.isEmpty else { return nil }
    return url
}

enum DiscoveryFailure: LocalizedError, Equatable {
    case invalidResponse, unavailable, signIn, rateLimited, notSupported, network, cancelled, uncertainSave
    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Discovery returned an unsupported result. No role was saved. Refresh later or contact support."
        case .unavailable: return "Discovery sources are unavailable. No role was saved. Refresh later."
        case .signIn: return "Your session could not be verified. Sign out and sign in again."
        case .rateLimited: return "Too many requests. Wait before choosing Refresh. No automatic retry will run."
        case .notSupported: return "This server does not support discovery yet. You can still add a role manually."
        case .network: return "Discovery could not connect. No role was saved. Check your connection and choose Refresh."
        case .cancelled: return "Search cancelled. Choose Refresh when you are ready."
        case .uncertainSave: return "The save could not be confirmed. Check Saved and refresh your workspace before trying again. No automatic retry was made."
        }
    }
    static func sanitized(_ error: Error) -> Self {
        if let failure = error as? Self { return failure }
        if error is CancellationError || (error as? URLError)?.code == .cancelled { return .cancelled }
        // Never render token/auth/server detail strings, URLs, or raw transport errors.
        return .network
    }
}

extension APIClient {
    static var discoveryRequest: [String: Any] {
        ["query": "", "filters": ["titles": [String](), "locations": [String](), "workplace_type": "any"], "limit": 20]
    }
    func discoverySearch(token: String) async throws -> DiscoveryResponse {
        let data = try await discoveryData(path: "discovery/search", token: token, body: Self.discoveryRequest)
        return try DiscoveryResponse.checked(data)
    }
    func saveDiscoveryJob(_ job: DiscoveryJob, token: String) async throws -> Opportunity {
        let body = try job.savePayload()
        let data = try await discoveryData(path: "jobs", token: token, body: body)
        guard let saved = try? Self.decoder.decode(Opportunity.self, from: data),
              let uuid = UUID(uuidString: saved.id), uuid.uuidString.lowercased() == saved.id.lowercased(),
              discoveryListingURL(saved.sourceUrl) == discoveryListingURL(job.sourceUrl),
              discoveryText(saved.title, 160, required: true), discoveryText(saved.companyName, 160, required: true)
        else { throw DiscoveryFailure.uncertainSave }
        return saved
    }
    private func discoveryData(path: String, token: String, body: [String: Any]) async throws -> Data {
        guard let base = AppConfiguration.secureURL(configuration.apiBase) else { throw DiscoveryFailure.network }
        var request = URLRequest(url: base.appendingPathComponent("api/mobile/\(path)"))
        request.httpMethod = "POST"; request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        // Reuse the existing ephemeral, no-redirect transport. Read status here to
        // retain source coverage on 503 without exposing arbitrary server errors.
        do {
            try Task.checkCancellation()
            let (data, response) = try await transport.data(for: request)
            try Task.checkCancellation()
            guard let http = response as? HTTPURLResponse else { throw DiscoveryFailure.network }
            switch http.statusCode {
            case 200..<300: return data
            case 401, 403: throw DiscoveryFailure.signIn
            case 429: throw DiscoveryFailure.rateLimited
            case 404, 405: throw DiscoveryFailure.notSupported
            case 503 where path == "discovery/search":
                if let result = try? DiscoveryResponse.checked(data), result.status == "unavailable" { return data }
                throw DiscoveryFailure.unavailable
            default: throw DiscoveryFailure.network
            }
        } catch { throw DiscoveryFailure.sanitized(error) }
    }
}

/// One bounded, private, in-memory snapshot. Tokens and UI qualification UUIDs
/// are not part of identity; all confirmed facts/preferences and authority are.
struct DiscoveryScope: Hashable {
    let digest: String
    init(owner: String, authority: String, apiBase: String, sessionID: String = "", profile: CandidateProfile?, preferences: Preferences?) {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let context = [owner, authority, apiBase, sessionID, profile?.careerText ?? "",
                       (try? encoder.encode(profile?.careerBackground)).map { String(decoding: $0, as: UTF8.self) } ?? "null",
                       (try? encoder.encode(preferences)).map { String(decoding: $0, as: UTF8.self) } ?? "null"]
        let data = (try? encoder.encode(context)) ?? Data()
        digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

struct DiscoverySearchState {
    static let lifetime: TimeInterval = 300
    private(set) var scope: DiscoveryScope?
    private(set) var attemptedAt: Date?
    private(set) var response: DiscoveryResponse?
    private(set) var failure: DiscoveryFailure?
    private(set) var requestID: UUID?
    var isLoading: Bool { requestID != nil }

    mutating func begin(scope: DiscoveryScope, refresh: Bool, now: Date = Date()) -> UUID? {
        if self.scope != scope { self = Self(); self.scope = scope }
        guard !isLoading else { return nil }
        if !refresh, let attemptedAt, now >= attemptedAt, now.timeIntervalSince(attemptedAt) < Self.lifetime { return nil }
        let id = UUID(); requestID = id; attemptedAt = now; response = nil; failure = nil
        return id
    }
    mutating func finish(id: UUID, scope: DiscoveryScope, response: DiscoveryResponse? = nil, failure: DiscoveryFailure? = nil) {
        guard requestID == id, self.scope == scope else { return }
        requestID = nil; self.response = response; self.failure = failure
    }
}
