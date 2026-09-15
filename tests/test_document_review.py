"""Private review provenance checks; no credentials, network or real exports."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jobagent.mobile import studio
from jobagent.mobile.document_review import (
    DocumentReviewError, NOTICE, source_snapshot, validated_document_review, validated_saved_document_review,
)
from test_studio_launch_quality import Provider, context, selection


class DocumentReviewTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def prepared(self):
        candidate = context()
        candidate["career_text"] += "\nPrepared annual forecasts for budget owners.\nStudied a separate unrelated topic."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.1"])
        with patch.object(studio, "_provider", return_value=Provider(output)), \
                patch.object(studio, "render_pdf", return_value=b"structural-only"), \
                patch.object(studio, "render_docx", return_value=b"structural-only"):
            result = studio.prepare_documents(candidate)
        return candidate, result

    def resign(self, review):
        snapshot = review["snapshot"]
        review["snapshot"] = source_snapshot(snapshot["sources"], snapshot["generation_id"], snapshot["captured_at"])

    def test_full_packet_validates_and_copies_generation_time_facts(self):
        candidate, documents = self.prepared()
        review = validated_document_review(documents)
        self.assertEqual(review["notice"], NOTICE)
        self.assertEqual(review["snapshot"]["generation_id"], documents[0]["version_id"])
        ledger = {source["id"]: source for source in review["snapshot"]["sources"]}
        self.assertEqual(ledger["career_text.0"]["text"], candidate["career_text"].splitlines()[0])
        self.assertEqual(ledger["job.title"]["text"], "Finance Manager")
        self.assertNotIn("profile.email", ledger)
        self.assertEqual(review["omitted_source_ids"], ["career_text.2"])

    def test_later_profile_edit_cannot_change_or_resolve_old_source_ids(self):
        candidate, documents = self.prepared()
        old = documents.review["snapshot"]["sources"]
        candidate["career_text"] = "Different career fact that now occupies career_text.0."
        candidate["job"]["title"] = "Different role"
        review = validated_document_review(documents)
        self.assertEqual(review["snapshot"]["sources"], old)
        self.assertNotIn("Different", repr(review))
        review["snapshot"]["sources"][0]["text"] = "mutated response"
        self.assertNotIn("mutated response", repr(documents.review))

    def test_legacy_missing_review_is_explicitly_unavailable(self):
        self.assertIsNone(validated_document_review([]))
        self.assertIsNone(validated_document_review(studio.StudioDocuments([])))
        self.assertIsNone(validated_document_review(SimpleNamespace()))

    def test_snapshot_text_tampering_is_detected_without_provider_call(self):
        _, documents = self.prepared()
        documents.review["snapshot"]["sources"][0]["text"] = "An uncaptured replacement fact."
        with self.assertRaises(DocumentReviewError):
            validated_document_review(documents)

    def test_generation_or_created_time_must_match_every_actual_document(self):
        _, original = self.prepared()
        for key, value in (("version_id", "00000000-0000-4000-8000-000000000001"),
                           ("created_at", "2000-01-01T00:00:00+00:00")):
            documents = copy.deepcopy(original)
            documents[3][key] = value
            with self.subTest(key=key), self.assertRaises(DocumentReviewError):
                validated_document_review(documents)

    def test_rendered_source_mapping_cannot_be_replaced_by_union_of_documents(self):
        _, documents = self.prepared()
        documents[0]["source_ids"].append("career_text.1")
        with self.assertRaises(DocumentReviewError):
            validated_document_review(documents)

    def test_all_candidate_snapshot_facts_are_selected_or_explicitly_omitted(self):
        _, original = self.prepared()
        for mutation in (lambda r: r.update(omitted_source_ids=[]),
                         lambda r: r["omitted_source_ids"].append("career_text.0"),
                         lambda r: r["omitted_with_literal_job_overlap"].append("career_text.0")):
            review = copy.deepcopy(original.review)
            mutation(review)
            with self.assertRaises(DocumentReviewError):
                validated_saved_document_review(review)

    def test_unknown_ids_and_job_facts_cannot_be_candidate_selections(self):
        _, original = self.prepared()
        for invalid in ("career_text.999", "job.title", "profile.password", "__proto__"):
            review = copy.deepcopy(original.review)
            review["cover_letter_source_ids"] = [invalid]
            with self.subTest(source=invalid), self.assertRaises(DocumentReviewError):
                validated_saved_document_review(review)

    def test_private_transport_fields_or_mixed_source_kinds_are_rejected(self):
        _, original = self.prepared()
        for source in ({"id": "profile.experience.0.api_key", "kind": "candidate", "text": "synthetic-not-a-secret"},
                       {"id": "job.title", "kind": "candidate", "text": "Employer-provided role"}):
            review = copy.deepcopy(original.review)
            review["snapshot"]["sources"].append(source)
            self.resign(review)
            with self.assertRaises(DocumentReviewError):
                validated_saved_document_review(review)

    def test_snapshot_and_reference_duplicates_fail_closed(self):
        _, original = self.prepared()
        for location in ("source", "selection"):
            review = copy.deepcopy(original.review)
            if location == "source":
                review["snapshot"]["sources"].append(copy.deepcopy(review["snapshot"]["sources"][0]))
                self.resign(review)
            else:
                review["resume_sections"]["Education"] = review["resume_sections"]["Work Experience"][:]
            with self.assertRaises(DocumentReviewError):
                validated_saved_document_review(review)

    def test_malformed_and_oversized_reviews_do_not_escape_safe_validation_errors(self):
        _, original = self.prepared()
        changes = [lambda r: r.update(unexpected="field"), lambda r: r.update(mode="rewritten_and_verified"),
                   lambda r: r["snapshot"].update(captured_at="not-a-date"),
                   lambda r: r["snapshot"]["sources"][0].update(text="x" * 3001),
                   lambda r: r.update(omitted_source_ids=["x"] * 401)]
        for mutate in changes:
            review = copy.deepcopy(original.review)
            mutate(review)
            with self.assertRaises(DocumentReviewError) as error:
                validated_saved_document_review(review)
            self.assertNotIn("Prepared monthly forecasts", str(error.exception))

    def test_html_is_only_source_text_not_an_instruction_to_the_review_renderer(self):
        _, documents = self.prepared()
        source = next(s for s in documents.review["snapshot"]["sources"] if s["id"] == "career_text.0")
        source["text"] = '<img src=x onerror=alert(1)> This is synthetic source text.'
        self.resign(documents.review)
        review = validated_document_review(documents)
        self.assertIn("<img", next(s for s in review["snapshot"]["sources"] if s["id"] == "career_text.0")["text"])
        # This boundary does not render HTML or fetch links. The React panel must
        # use ordinary text children, never dangerouslySetInnerHTML.

    def test_incomplete_packet_is_not_claimed_as_complete_provenance(self):
        _, documents = self.prepared()
        del documents[2:]
        with self.assertRaises(DocumentReviewError):
            validated_document_review(documents)

    def test_unattributed_warning_must_name_an_actual_selected_fact(self):
        _, documents = self.prepared()
        documents.review["unattributed_source_ids"] = ["career_text.2"]  # omitted, not in either draft
        with self.assertRaises(DocumentReviewError):
            validated_document_review(documents)

    def test_multi_employer_contributions_snapshot_names_uncertain_attribution(self):
        candidate = context()
        candidate["career_text"] = "Analyst, Example Shop, 2021-2025.\nAnalyst, Fictional Mart, 2018-2021.\nReduced reporting time by two days."
        output = selection(candidate, experience=["career_text.0", "career_text.1", "career_text.2"], cover_letter=["career_text.2"])
        with patch.object(studio, "_provider", return_value=Provider(output)), \
                patch.object(studio, "render_pdf", return_value=b"structural-only"), \
                patch.object(studio, "render_docx", return_value=b"structural-only"):
            result = studio.prepare_documents(candidate)
        review = validated_document_review(result)
        self.assertEqual(review["unattributed_source_ids"], ["career_text.2"])
        self.assertEqual(review["resume_sections"]["Selected Career Contributions"], ["career_text.2"])


if __name__ == "__main__":
    unittest.main()
