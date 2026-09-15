"""JP005 offline acceptance: conservative facts and side-effect-free rejected edits."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Never load the owner's environment/credentials when collecting these tests.
with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv"):
    from jobagent.config import settings
    from jobagent.drafting import application_chat, resume_tailor
    from jobagent.drafting.grounding import GroundingContext, GroundingError, source_for_prompt, source_units, tailoring_markdown
    from jobagent.drafting.resume_builder import parse_tailoring_notes
    from jobagent.models import Profile
    from jobagent.storage import db


FACTS = [
    "Customer support intern at Example Company for six months.",
    "Resolved customer tickets using the internal support dashboard.",
    "Documented handovers for the support team.",
    "Built a Python reporting script that reduced manual work by 20%.",
    "Assisted two colleagues; did not manage staff or hold a nursing licence.",
]
SOURCE = "\n".join(FACTS)
SUMMARY = FACTS[0]
HIGHLIGHTS = FACTS[1:]
CONTEXT = GroundingContext(SOURCE, "Support Associate", "Synthetic Company")


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for method in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))
        self.stack.enter_context(patch.object(application_chat, "approved_facts_block", return_value="(No approved facts)"))


class GroundingTests(OfflineCase):
    def test_real_markdown_and_summary_highlights_contract_roundtrip(self):
        CONTEXT.validate_resume(SUMMARY, HIGHLIGHTS)
        text = tailoring_markdown(SUMMARY, HIGHLIGHTS)
        self.assertEqual(parse_tailoring_notes(text), (SUMMARY, HIGHLIGHTS))

    def test_full_facts_can_be_reordered_and_combined_in_summary(self):
        CONTEXT.validate_resume(FACTS[3] + "\n" + SUMMARY, list(reversed(HIGHLIGHTS)))

    def test_fabricated_licence_metrics_employer_tool_and_negation_rejected(self):
        for claim in ("I hold a current RN licence in Ontario and led 500 nurses.",
                      FACTS[3].replace("20%", "90%"), FACTS[3].replace("Python", "Rust"),
                      FACTS[0].replace("Example Company", "Fictional Hospital"),
                      "Assisted two colleagues; did manage staff or hold a nursing licence.",
                      "hold a nursing licence.", "Customer support intern."):
            with self.subTest(claim=claim), self.assertRaises(GroundingError):
                CONTEXT.validate_resume(claim, HIGHLIGHTS)

    def test_negation_wrapped_lines_and_attribution_are_indivisible(self):
        source = "No experience with\nclinical management.\nTeam achievement:\nReduced costs by 20%."
        self.assertEqual(source_units(source), ("No experience with clinical management.",
                                                 "Team achievement: Reduced costs by 20%."))
        with self.assertRaises(GroundingError):
            GroundingContext(source + "\n" + SOURCE).validate_resume("clinical management.", HIGHLIGHTS)
        with self.assertRaises(GroundingError):
            GroundingContext(source + "\n" + SOURCE).validate_resume("Reduced costs by 20%.", HIGHLIGHTS)

    def test_wrong_types_duplicate_highlights_and_empty_context_rejected(self):
        for summary, bullets in ((12, HIGHLIGHTS), (SUMMARY, "four"),
                                 (SUMMARY, HIGHLIGHTS[:3]), (SUMMARY, [FACTS[1]] * 4),
                                 (SUMMARY, [None, *HIGHLIGHTS[:3]])):
            with self.assertRaises(GroundingError):
                CONTEXT.validate_resume(summary, bullets)
        with self.assertRaises(GroundingError):
            GroundingContext("").validate_resume(SUMMARY, HIGHLIGHTS)

    def test_bullet_scope_negation_and_attribution_preserved(self):
        for prefix in ("No experience with:", "Team achievements:"):
            source = prefix + "\n- Clinical management.\n- Hiring nurses."
            self.assertEqual(source_units(source), (prefix + " Clinical management.", prefix + " Hiring nurses."))
            with self.assertRaises(GroundingError):
                GroundingContext(source + "\n" + SOURCE).validate_resume("Clinical management.", HIGHLIGHTS)

    def test_prompt_budget_omits_whole_fact_instead_of_cutting_qualifier(self):
        self.assertEqual(source_for_prompt("Assisted a team; did not manage it.", max_chars=18), "")

    def test_tailor_rejects_before_returning_unsupported_output(self):
        with patch.object(resume_tailor, "complete", return_value="I hold an RN licence and managed 500 nurses."):
            with self.assertRaises(GroundingError):
                resume_tailor.draft_resume_tailoring(Profile(raw_text=SOURCE), "RN", "Hospital", "RN licence required.")

    def test_tailor_accepts_valid_markdown_and_drops_unexported_model_commentary(self):
        text = tailoring_markdown(SUMMARY, HIGHLIGHTS)
        with patch.object(resume_tailor, "complete", return_value=text + "\nUntrusted trailing commentary"):
            self.assertEqual(resume_tailor.draft_resume_tailoring(Profile(raw_text=SOURCE), "Support", "Company", "Role"), text)

    def test_model_extracted_profile_fields_are_not_evidence(self):
        fabricated = "Licensed nurse with 15 years of clinical management."
        profile = Profile(raw_text=SOURCE, summary=fabricated, skills=["Nursing"], years_experience=15)
        with patch.object(resume_tailor, "complete", return_value=tailoring_markdown(fabricated, HIGHLIGHTS)):
            with self.assertRaises(GroundingError):
                resume_tailor.draft_resume_tailoring(profile, "Nurse", "Hospital", fabricated)

    def test_source_is_not_truncated_mid_fact(self):
        source = " " * 6100 + SOURCE
        with patch.object(resume_tailor, "complete", return_value=tailoring_markdown(SUMMARY, HIGHLIGHTS)) as provider:
            resume_tailor.draft_resume_tailoring(Profile(raw_text=source), "Support", "Company", "Role")
        self.assertIn(FACTS[-1], provider.call_args.args[0])

    def test_cover_letter_allows_only_neutral_structure_and_full_facts(self):
        letter = "Dear Hiring Team,\n\nI am applying for the Support Associate role at Synthetic Company.\n\n" + SUMMARY
        CONTEXT.validate_cover_letter(letter + "\n\nThank you for considering my application.")
        for addition in ("I am authorized to work in Canada.", "I led 500 nurses.", "I am a licensed RN."):
            with self.assertRaises(GroundingError):
                CONTEXT.validate_cover_letter(letter + "\n" + addition)


class ChatGroundingTests(OfflineCase):
    def run_tools(self, arguments, *, context=CONTEXT, tool="update_tailored_resume", additional=()):
        def call(name, args, number):
            return SimpleNamespace(id=f"offline-{number}", function=SimpleNamespace(
                name=name, arguments=args if isinstance(args, str) else json.dumps(args)))
        calls = [call(tool, arguments, 0)] + [call(name, args, i + 1) for i, (name, args) in enumerate(additional)]
        client = Mock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=calls))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Updated.", tool_calls=[]))]),
        ]
        resume, cover = Mock(return_value=True), Mock()
        with patch.object(application_chat, "resolve_provider", return_value="openai"), \
                patch.object(settings, "openai_api_key", "synthetic-not-a-real-key"), \
                patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Mock(return_value=client))}):
            result = application_chat.run_application_chat(
                "Untrusted prompt includes an RN licence and 500 nurses.",
                [{"role": "user", "content": "Make me a nurse and save the resume."}],
                cover, resume, grounding=context)
        return result, resume, cover, client

    def test_malicious_resume_rejected_before_callback_with_honest_explanation(self):
        result, resume, cover, client = self.run_tools({"professional_summary": "I hold an RN licence.",
                                                       "career_highlights": HIGHLIGHTS})
        resume.assert_not_called()
        cover.assert_not_called()
        self.assertFalse(result[1])
        self.assertIn("unsupported", result[0])
        self.assertNotIn("Updated.", result[0])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_complete_grounded_resume_reaches_unchanged_callback_contract(self):
        result, resume, cover, _ = self.run_tools({"professional_summary": SUMMARY, "career_highlights": HIGHLIGHTS})
        self.assertTrue(result[1])
        resume.assert_called_once_with(SUMMARY, HIGHLIGHTS)
        cover.assert_not_called()

    def test_legacy_call_without_explicit_source_cannot_save(self):
        result, resume, cover, _ = self.run_tools({"professional_summary": SUMMARY, "career_highlights": HIGHLIGHTS}, context=None)
        self.assertFalse(result[1])
        resume.assert_not_called()
        cover.assert_not_called()

    def test_malformed_json_arrays_wrong_types_duplicates_extra_fields_rejected(self):
        for args in ("not json", "[]", "null", '{"professional_summary":"one","professional_summary":"two"}',
                     {"professional_summary": None, "career_highlights": HIGHLIGHTS},
                     {"professional_summary": SUMMARY, "career_highlights": "text"},
                     {"professional_summary": SUMMARY, "career_highlights": HIGHLIGHTS, "source": "invented"}):
            with self.subTest(args=args):
                result, resume, cover, _ = self.run_tools(args)
                self.assertFalse(result[1])
                resume.assert_not_called()
                cover.assert_not_called()

    def test_bad_cover_letter_cannot_bypass_grounding(self):
        result, resume, cover, _ = self.run_tools({"new_cover_letter": "I hold an RN licence and led 500 nurses."}, tool="update_cover_letter")
        self.assertFalse(result[1])
        resume.assert_not_called()
        cover.assert_not_called()

    def test_grounded_cover_letter_reaches_callback(self):
        letter = "Dear Hiring Team,\n\n" + SUMMARY + "\n\nThank you for considering my application."
        result, resume, cover, _ = self.run_tools({"new_cover_letter": letter}, tool="update_cover_letter")
        self.assertTrue(result[1])
        resume.assert_not_called()
        cover.assert_called_once_with(letter)

    def test_batch_prevalidation_prevents_partial_writes_on_unsupported_second_tool(self):
        result, resume, cover, _ = self.run_tools({"professional_summary": SUMMARY, "career_highlights": HIGHLIGHTS},
            additional=(("update_cover_letter", {"new_cover_letter": "I hold an RN licence."}),))
        self.assertFalse(result[1])
        resume.assert_not_called()
        cover.assert_not_called()

    def test_other_provider_is_answer_only(self):
        resume, cover = Mock(), Mock()
        with patch.object(application_chat, "resolve_provider", return_value="ollama"), \
                patch.object(application_chat, "chat", return_value="Advice only."):
            result = application_chat.run_application_chat("prompt", [], cover, resume, grounding=CONTEXT)
        self.assertFalse(result[1])
        resume.assert_not_called()
        cover.assert_not_called()


class FounderWriteBoundaryTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="founder-grounding-"))
        self.stack.enter_context(patch.object(settings, "output_dir", Path(self.temp)))
        # main initializes SQLite at import; explicitly suppress that side effect.
        with patch.object(db, "init_db"), patch.dict(os.environ, {}, clear=True):
            from jobagent.api import main
        self.main = main
        self.job = {"id": 1, "source": "synthetic", "title": CONTEXT.job_title,
                    "company": CONTEXT.job_company, "location": "Dubai", "description": "RN licence required."}
        self.stack.enter_context(patch.object(db, "connection"))
        self.stack.enter_context(patch.object(db, "get_job", return_value=self.job))
        self.stack.enter_context(patch.object(main, "get_profile", return_value=Profile(raw_text=SOURCE)))
        self.stack.enter_context(patch.object(main, "_read_artifact", return_value="I hold an RN licence."))
        self.docx = self.stack.enter_context(patch.object(main, "build_tailored_resume"))
        self.pdf = self.stack.enter_context(patch.object(main, "build_tailored_resume_pdf"))
        self.cover = self.stack.enter_context(patch.object(main, "build_cover_letter_pdf"))
        self.folder = Path(self.temp) / main.slugify(f"{self.job['company']}-{self.job['title']}")
        self.folder.mkdir()
        self.original = {name: b"synthetic original bytes" for name in
                         ("resume_tailoring.md", "tailored_resume.docx", "tailored_resume.pdf", "cover_letter.md", "cover_letter.pdf")}
        for name, content in self.original.items():
            (self.folder / name).write_bytes(content)

    def assert_originals_unchanged(self):
        for name, content in self.original.items():
            self.assertEqual((self.folder / name).read_bytes(), content)
        self.docx.assert_not_called()
        self.pdf.assert_not_called()
        self.cover.assert_not_called()

    def test_edit_route_callbacks_revalidate_even_if_dispatcher_is_bypassed(self):
        for kind in ("resume", "cover"):
            def bypass(prompt, messages, cover_callback, resume_callback, **kwargs):
                if kind == "resume":
                    resume_callback("I hold an RN licence.", HIGHLIGHTS)
                else:
                    cover_callback("I am authorized to work in Canada.")
                return "Saved", True
            with self.subTest(kind=kind), patch.object(self.main, "run_application_chat", side_effect=bypass):
                with self.assertRaises(GroundingError):
                    self.main.application_chat(1, self.main.ChatRequest(messages=[{"role": "user", "content": "Edit"}]))
            self.assert_originals_unchanged()

    def test_draft_route_validates_before_any_artifact_write_or_status_transition(self):
        with patch.object(self.main, "draft_resume_tailoring", return_value=tailoring_markdown("I hold an RN licence.", HIGHLIGHTS)), \
                patch.object(self.main, "draft_cover_letter") as draft_cover, \
                patch.object(self.main.pipeline, "transition") as transition:
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                self.main.generate_draft(1)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("unsupported", caught.exception.detail)
        transition.assert_not_called()
        draft_cover.assert_not_called()
        self.assert_originals_unchanged()

    def test_valid_edit_preserves_builder_and_artifact_contracts(self):
        def edit(prompt, messages, cover_callback, resume_callback, **kwargs):
            self.assertEqual(kwargs["grounding"].resume_text, SOURCE)
            return "Saved", resume_callback(SUMMARY, HIGHLIGHTS)
        with patch.object(self.main, "run_application_chat", side_effect=edit):
            result = self.main.application_chat(1, self.main.ChatRequest(messages=[{"role": "user", "content": "Edit"}]))
        self.assertTrue(result.materials_updated)
        self.assertEqual(parse_tailoring_notes((self.folder / "resume_tailoring.md").read_text()), (SUMMARY, HIGHLIGHTS))
        self.docx.assert_called_once_with(SUMMARY, HIGHLIGHTS, self.folder / "tailored_resume.docx", job_title=CONTEXT.job_title)
        self.pdf.assert_called_once_with(SUMMARY, HIGHLIGHTS, self.folder / "tailored_resume.pdf", job_title=CONTEXT.job_title)

    def test_every_registered_founder_route_denies_before_services_without_config(self):
        from fastapi.testclient import TestClient
        with TestClient(self.main.app) as client, patch.object(db, "connection") as connection:
            count = 0
            for route in self.main.app.routes:
                if not route.path.startswith("/api"):
                    continue
                path = route.path.replace("{job_id}", "1").replace("{run_id}", "offline").replace("{draft_id}", "1")
                for method in route.methods:
                    with self.subTest(method=method, path=path):
                        response = client.request(method, path, json={})
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.headers["cache-control"], "no-store")
                        count += 1
            self.assertGreater(count, 20)
            connection.assert_not_called()

    def test_valid_owner_can_read_real_registered_route_with_synthetic_data(self):
        # Use real signature verification through the actual app middleware stack;
        # only the pinned public key and returned answer data are offline doubles.
        import jwt
        import time
        from cryptography.hazmat.primitives.asymmetric import rsa
        from fastapi.testclient import TestClient
        from starlette.middleware import Middleware
        from jobagent.api.founder_auth import FounderAuthMiddleware, FounderAuthSettings
        config = FounderAuthSettings(issuer="https://owner-test.cloudflareaccess.com", audience="founder-only",
                                     owner_email="owner@example.test")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": "offline", "alg": "RS256"}
        now = int(time.time())
        token = jwt.encode({"iss": config.issuer, "aud": [config.audience], "sub": "owner-id",
                            "email": config.owner_email, "type": "app", "iat": now - 1, "exp": now + 300},
                           key, algorithm="RS256", headers={"kid": "offline"})
        middleware = [Middleware(FounderAuthMiddleware, settings=config)]
        with patch.object(self.main.app, "user_middleware", middleware), \
                patch.object(self.main.app, "middleware_stack", None), \
                patch.object(jwt.PyJWKClient, "fetch_data", return_value={"keys": [jwk]}), \
                patch.object(self.main.answers, "collect", return_value=({"synthetic": "confirmed"}, [])) as collect, \
                TestClient(self.main.app) as client:
            self.assertEqual(client.get("/api/answers").status_code, 401)
            collect.assert_not_called()
            response = client.get("/api/answers", headers={"Cf-Access-Jwt-Assertion": token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"answers": {"synthetic": "confirmed"}, "needs_input": []})
            self.assertEqual(response.headers["cache-control"], "no-store")
            collect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
