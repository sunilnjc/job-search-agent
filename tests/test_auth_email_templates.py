"""Offline contract checks; these do not send mail or render Supabase templates."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "supabase" / "templates"
TEMPLATES = ("sign-in.html", "confirm-sign-up.html")


class EmailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class AuthEmailTemplateTests(unittest.TestCase):
    def test_both_auth_flows_have_code_and_confirmation_link(self):
        for filename in TEMPLATES:
            with self.subTest(filename=filename):
                source = (TEMPLATE_DIR / filename).read_text()
                self.assertEqual(source.count("{{ .Token }}"), 1)
                self.assertEqual(source.count("{{ .ConfirmationURL }}"), 1)
                self.assertEqual(
                    set(re.findall(r"{{\s*([^}]+?)\s*}}", source)),
                    {".Token", ".ConfirmationURL"},
                )

    def test_link_uses_supabase_confirmation_url_not_fixed_redirect(self):
        for filename in TEMPLATES:
            with self.subTest(filename=filename):
                parser = EmailParser()
                parser.feed((TEMPLATE_DIR / filename).read_text())
                links = [attrs.get("href") for tag, attrs in parser.elements if tag == "a"]
                self.assertEqual(links, ["{{ .ConfirmationURL }}"])

    def test_no_trackers_scripts_remote_assets_or_form_submission(self):
        for filename in TEMPLATES:
            with self.subTest(filename=filename):
                source = (TEMPLATE_DIR / filename).read_text()
                parser = EmailParser()
                parser.feed(source)
                for tag, attrs in parser.elements:
                    self.assertNotIn(tag, {"script", "iframe", "form", "img", "link", "object"})
                    self.assertFalse(any(key.startswith("on") for key in attrs))
                    self.assertNotIn("src", attrs)
                self.assertNotRegex(source, r"(?i)url\s*\(")

    def test_accessible_responsive_structure(self):
        for filename in TEMPLATES:
            with self.subTest(filename=filename):
                parser = EmailParser()
                parser.feed((TEMPLATE_DIR / filename).read_text())
                self.assertIn(("html", {"lang": "en"}), parser.elements)
                self.assertTrue(any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.elements))
                self.assertEqual(sum(tag == "h1" for tag, _ in parser.elements), 1)
                self.assertTrue(all(attrs.get("role") == "presentation" for tag, attrs in parser.elements if tag == "table"))


if __name__ == "__main__":
    unittest.main()
