// Offline Foundation-only checks against the shipping native models. This is
// not a replacement for simulator UI tests or authenticated device testing.
import Foundation

@main
struct ProfessionContractChecks {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        var checks = 0
        func check(_ value: @autoclosure () -> Bool, _ message: String) {
            precondition(value(), message); checks += 1
        }
        let legacy = try decoder.decode(CandidateProfile.self, from: Data("{\"display_name\":\"Example Person\"}".utf8))
        check(legacy.careerBackground == nil, "Legacy profile must remain usable")
        let empty = try decoder.decode(CareerBackground.self, from: Data("{}".utf8))
        check(empty.profession.isEmpty, "No forced profession")
        check(empty.experienceLevel == "unspecified", "No assumed experience")
        check(empty.qualifications.isEmpty, "No invented qualifications")
        for profession in ["Nursing", "Accounting", "Electrician", "Marketing", "Teaching", "A custom career"] {
            for status in ["current", "expired", "in_progress", "not_held", "unknown"] {
                let source = CareerBackground(profession: profession, experienceLevel: "career_change", qualifications: [
                    CareerQualification(name: "Example credential", kind: "licence", status: status, jurisdiction: "Example region", expiresOn: "2020-01-01", evidenceNote: "Synthetic self-report")
                ])
                let bytes = try JSONSerialization.data(withJSONObject: source.requestBody)
                let decoded = try decoder.decode(CareerBackground.self, from: bytes)
                check(decoded.profession == profession, "Freeform profession round trip")
                check(decoded.qualifications.first?.status == status, "Status must not be upgraded")
                check(decoded.qualifications.first?.expiresOn == "2020-01-01", "Date-only values must not shift")
                check(decoded.qualifications.first?.requestBody["id"] == nil, "No local UI ID transmitted")
                check(decoded.qualifications.first?.requestBody["verified"] == nil, "No verification claim transmitted")
            }
        }
        let unknown = try decoder.decode(CareerQualification.self, from: JSONSerialization.data(withJSONObject: CareerQualification().requestBody))
        check(unknown.status == "unknown", "New credentials are not implicitly current")
        check(unknown.expiresOn == nil, "An absent date stays absent")
        let sparse = try decoder.decode(CareerQualification.self, from: Data("{\"name\":\"Example course\",\"kind\":\"education\"}".utf8))
        check(sparse.status == "unknown", "Sparse qualification is not automatically current")
        check(sparse.jurisdiction.isEmpty && sparse.evidenceNote.isEmpty, "Optional context is not invented")
        check(sparse.expiresOn == nil, "Sparse qualification has no invented expiry")
        check(DocumentFocus.allCases.map(\.rawValue) == ["role_aligned", "career_change"], "Document choices must not restrict profession")
        let olderResult = try decoder.decode(PreparedDocuments.self, from: Data("{\"artifacts\":[]}".utf8))
        check(olderResult.questions == nil, "Old document responses remain decodable")
        print("PASS: \(checks) offline native profession-model assertions; no network or credentials used.")
    }
}
