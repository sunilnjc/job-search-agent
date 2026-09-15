import Foundation

struct CandidateProfile: Codable {
    var displayName: String?
    var phone: String?
    var baseLocation: String?
    var careerText: String?
    var careerBackground: CareerBackground?
    var onboardingCompletedAt: String?
    var firstName: String { (displayName ?? "there").split(separator: " ").first.map(String.init) ?? "there" }
}

/// Self-reported information. A status is never independent credential verification.
struct CareerBackground: Codable {
    var profession = ""
    var experienceLevel = "unspecified"
    var qualifications: [CareerQualification] = []
    init(profession: String = "", experienceLevel: String = "unspecified", qualifications: [CareerQualification] = []) {
        self.profession = profession; self.experienceLevel = experienceLevel; self.qualifications = qualifications
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        profession = try values.decodeIfPresent(String.self, forKey: .profession) ?? ""
        experienceLevel = try values.decodeIfPresent(String.self, forKey: .experienceLevel) ?? "unspecified"
        qualifications = try values.decodeIfPresent([CareerQualification].self, forKey: .qualifications) ?? []
    }
    var requestBody: [String: Any] {
        ["profession": profession, "experience_level": experienceLevel, "qualifications": qualifications.map(\.requestBody)]
    }
}

struct CareerQualification: Codable, Identifiable {
    var id = UUID() // UI identity only; never sent to the API.
    var name = ""
    var kind = "education"
    var status = "unknown"
    var jurisdiction = ""
    var expiresOn: String?
    var evidenceNote = ""
    enum CodingKeys: String, CodingKey { case name, kind, status, jurisdiction, expiresOn, evidenceNote }
    init(name: String = "", kind: String = "education", status: String = "unknown", jurisdiction: String = "", expiresOn: String? = nil, evidenceNote: String = "") {
        self.name = name; self.kind = kind; self.status = status; self.jurisdiction = jurisdiction
        self.expiresOn = expiresOn; self.evidenceNote = evidenceNote
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decode(String.self, forKey: .name)
        kind = try values.decode(String.self, forKey: .kind)
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "unknown"
        jurisdiction = try values.decodeIfPresent(String.self, forKey: .jurisdiction) ?? ""
        expiresOn = try values.decodeIfPresent(String.self, forKey: .expiresOn)
        evidenceNote = try values.decodeIfPresent(String.self, forKey: .evidenceNote) ?? ""
    }
    var requestBody: [String: Any] {
        ["name": name, "kind": kind, "status": status, "jurisdiction": jurisdiction,
         "expires_on": expiresOn as Any? ?? NSNull(), "evidence_note": evidenceNote]
    }
    var statusLabel: String {
        switch status {
        case "current": return "Current / completed"
        case "expired": return "Expired"
        case "in_progress": return "In progress"
        case "not_held": return "Not held"
        default: return "Not confirmed"
        }
    }
}

enum DocumentFocus: String, CaseIterable {
    case roleAligned = "role_aligned", careerChange = "career_change"
    var label: String { self == .roleAligned ? "Match this role" : "Career transition" }
}

struct Preferences: Codable {
    var targetTitles: [String]?
    var preferredLocations: [String]?
    var preferredRegions: [String]?
    var remotePreference: String?
    var sponsorshipRequired: Bool?
    var workAuthorizationNotes: String?
    var minimumMatchScore: Double?
}

struct Opportunity: Codable, Identifiable, Hashable {
    var id: String
    var title: String
    var companyName: String
    var sourceUrl: String
    var locationText: String?
    var description: String?
    var status: String?
    var eligibilityStatus: String?
    var workplaceType: String?
    var score: Double?
    var rationale: String?
    var eligibilityReview: JobEligibilityReview?
    var operationStatus: String?
    var warnings: [String]?
    var savedSyncWarning: String? { savedOperationWarning(status: operationStatus, warnings: warnings) }
    var initials: String { companyName.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined() }
    var matchLabel: String { score.map { "\(Int(($0 * 10).rounded()))% match" } ?? "Not scored" }
    var displayLocation: String { locationText?.isEmpty == false ? locationText! : "Location to confirm" }
    static func trackerPlaceholder(jobId: String) -> Opportunity {
        Opportunity(id: jobId, title: "Tracked opportunity", companyName: "Job details not loaded", sourceUrl: "")
    }
}

enum EligibilityChoice: String, CaseIterable {
    case eligible, ineligible, unknown
    var label: String {
        switch self { case .eligible: return "Eligible"; case .ineligible: return "Ineligible"; case .unknown: return "Not sure yet" }
    }
}

struct JobEligibilityReview: Codable, Hashable {
    var status: String
    var reason: String
    var confirmed: Bool
    var confirmedAt: String?
    var provenance: String?
    var independentlyVerified: Bool?
    static let disclaimer = "Self-reported by you for this opportunity. Not independently verified; not proof of work rights, sponsorship or credentials."
    var label: String { "Self-reported eligibility: \(EligibilityChoice(rawValue: status)?.label ?? "Not sure yet")" }
}

struct EligibilityReviewDraft {
    var status: EligibilityChoice = .unknown
    var reason = ""
    var confirmed = false
    var trimmedReason: String { reason.trimmingCharacters(in: .whitespacesAndNewlines) }
    var isValid: Bool { confirmed && !trimmedReason.isEmpty && trimmedReason.count <= 2000 }
}

/// Only changed source fields are sent. An omitted bootstrap description must
/// never become an accidental empty-description update.
struct OpportunityEdits {
    let original: Opportunity
    var title: String
    var company: String
    var location: String
    var description: String
    init(job: Opportunity) {
        original = job; title = job.title; company = job.companyName
        location = job.locationText ?? ""; description = job.description ?? ""
    }
    var changes: [String: Any] {
        var fields: [String: Any] = [:]
        if title != original.title { fields["title"] = title.trimmingCharacters(in: .whitespacesAndNewlines) }
        if company != original.companyName { fields["company_name"] = company.trimmingCharacters(in: .whitespacesAndNewlines) }
        if location != (original.locationText ?? "") { fields["location_text"] = location.trimmingCharacters(in: .whitespacesAndNewlines) }
        if description != (original.description ?? "") { fields["description"] = description }
        return fields
    }
    var isValid: Bool {
        let name = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let employer = company.trimmingCharacters(in: .whitespacesAndNewlines)
        return !changes.isEmpty && !name.isEmpty && name.count <= 160 && !employer.isEmpty && employer.count <= 160
            && location.count <= 300 && description.count <= 80_000
            && (changes["description"] == nil || !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
}

struct ResumeDocument: Codable, Identifiable {
    var id: String
    var label: String
    var roleFocus: String?
    var originalFilename: String
    var mimeType: String?
    var byteSize: Int?
    var isDefault: Bool?
    var createdAt: String?
}

struct Artifact: Codable, Identifiable {
    var id: String
    var jobId: String?
    var kind: String
    var filename: String
    var mimeType: String?
    var createdAt: String?
    var modelName: String?
}

struct JobApplication: Codable, Identifiable {
    var id: String
    var jobId: String
    var status: String
    var notes: String?
    var appliedAt: String?
    var createdAt: String?
    var label: String { status.replacingOccurrences(of: "_", with: " ").capitalized }
}

struct CandidateQuestion: Codable, Identifiable {
    var id: String
    var jobId: String?
    var prompt: String
    var answer: String?
    var status: String
    var remember: Bool?
}

struct Workspace: Decodable {
    var profile: CandidateProfile?
    var preferences: Preferences?
    var jobs: [Opportunity]
    var resumes: [ResumeDocument]
    var artifacts: [Artifact]
    var applications: [JobApplication]
    var questions: [CandidateQuestion]
    var capabilities: WorkspaceCapabilities?
    static var empty: Workspace { Workspace(jobs: [], resumes: [], artifacts: [], applications: [], questions: []) }
    var pendingQuestions: [CandidateQuestion] { questions.filter { $0.status == "pending" } }
    var rankedJobs: [Opportunity] { jobs.filter { !["excluded", "archived"].contains($0.status ?? "") }.sorted { ($0.score ?? -1) > ($1.score ?? -1) } }
}

struct WorkspaceCapabilities: Decodable {
    var jobDetailFetch: Bool?
    var eligibilityReview: String?
    var bootstrapListLimit: Int?
    var supportsEligibilityReview: Bool { eligibilityReview == "job_scoped_user_self_report" }
}

struct ChatLine: Codable, Identifiable {
    var id = UUID()
    var role: String
    var content: String
    var evidence: [String] = []
}

struct ChatReply: Decodable { var reply: String; var evidence: [String]; var questions: [CandidateQuestion] }
struct PreparedDocuments: Decodable {
    var artifacts: [Artifact]
    var questions: [CandidateQuestion]?
    var operationStatus: String?
    var warnings: [String]?
    var savedSyncWarning: String? { savedOperationWarning(status: operationStatus, warnings: warnings) }
}
func savedOperationWarning(status: String?, warnings: [String]?) -> String? {
    guard status == "saved_sync_pending" || warnings?.isEmpty == false else { return nil }
    let explanation = (warnings ?? []).joined(separator: " ")
    return (explanation.isEmpty ? "Outputs were saved, but their activity log is not synchronized." : explanation)
        + " Refresh and review saved results before requesting another AI operation."
}
struct StorageRecoveryRequired: Decodable {
    var code: String
    var operationId: String
    var state: String?
    var message: String
    var savedArtifacts: [Artifact]?
    var bytesMissing: Bool { ["artifact_upload_bytes_required", "resume_upload_bytes_required"].contains(code) }
    var userMessage: String {
        if bytesMissing {
            return message + " Recovery reference: \(operationId). Recovery cannot recreate missing bytes. You may explicitly upload a fresh source as a new resume; the old operation stays unresolved. No AI regeneration is performed."
        }
        return message + " Recovery reference: \(operationId). Do not regenerate documents or start a replacement upload while persistence is unresolved."
    }
}
struct ExtractedResume: Decodable { var text: String }

struct Session: Codable {
    var accessToken: String
    var refreshToken: String
    var expiresAt: Double?
    var expiresIn: Double?
    var user: SessionUser
    var authority: String?
}
struct SessionUser: Codable { var id: String; var email: String? }

// Synthetic, opt-in design preview. Never uploaded or merged into a signed-in account.
extension Workspace {
    static var preview: Workspace {
        let roles = [
            Opportunity(id: "preview-1", title: "Senior Backend Engineer", companyName: "Meridian", sourceUrl: "https://example.com", locationText: "London, UK · Hybrid", description: "Build dependable distributed systems with a small, collaborative engineering team. Work across Java services, event-driven integrations, and the systems behind our core product.\n\nThis is a fictional role in the design preview. Import a real job to research and prepare an application.", status: "matched", eligibilityStatus: "needs_review", workplaceType: "hybrid", score: 9.2, rationale: "Your Java and distributed-systems experience aligns with the core work. Sponsorship still needs confirmation."),
            Opportunity(id: "preview-2", title: "Forward Deployed Engineer", companyName: "Forma", sourceUrl: "https://example.com", locationText: "Amsterdam, NL · On-site", description: "Partner with customers to turn complex operational problems into reliable software. Fictional preview opportunity.", status: "ready", eligibilityStatus: "unknown", workplaceType: "onsite", score: 8.7, rationale: "Strong overlap with customer discovery, integration delivery, and production ownership."),
            Opportunity(id: "preview-3", title: "Staff Software Engineer", companyName: "Arc Systems", sourceUrl: "https://example.com", locationText: "Berlin, DE · Hybrid", description: "Own the next generation of an event-driven platform. Fictional preview opportunity.", status: "matched", eligibilityStatus: "unknown", workplaceType: "hybrid", score: 8.4, rationale: "Backend architecture is a strong fit. Confirm the team's scope and expectations.")
        ]
        return Workspace(profile: CandidateProfile(displayName: "Alex Morgan", baseLocation: "Dubai, UAE", careerText: "Preview profile: backend engineering, Java and customer-facing integration delivery."), preferences: Preferences(targetTitles: ["Senior Backend Engineer", "Forward Deployed Engineer"], preferredLocations: ["London", "Amsterdam"], remotePreference: "open", sponsorshipRequired: true, minimumMatchScore: 7), jobs: roles, resumes: [ResumeDocument(id: "preview-resume", label: "Backend engineering", roleFocus: "SSE", originalFilename: "Alex_Morgan_SSE.pdf", mimeType: "application/pdf", byteSize: 42000, isDefault: true, createdAt: nil)], artifacts: [], applications: [JobApplication(id: "preview-application", jobId: "preview-2", status: "ready", notes: "Documents reviewed. Application has not been submitted.")], questions: [CandidateQuestion(id: "preview-question", jobId: "preview-1", prompt: "Do you currently have the right to work in the UK, or would you need employer sponsorship?", status: "pending")])
    }
}
