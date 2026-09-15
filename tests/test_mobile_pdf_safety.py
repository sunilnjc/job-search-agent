"""JP032 offline policy tests: inert in-memory PDFs; never open a viewer."""
from __future__ import annotations

import base64
import io
import socket
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                           IndirectObject, NameObject, NumberObject, TextStringObject)

from jobagent.mobile import studio
from jobagent.mobile.pdf_safety import PDFSafetyError, PDF_REJECTION_MESSAGE, validate_pdf_reader


def dictionary(**values):
    return DictionaryObject({NameObject("/" + key): value for key, value in values.items()})


def writer_with_text():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = dictionary(Type=NameObject("/Font"), Subtype=NameObject("/Type1"), BaseFont=NameObject("/Helvetica"))
    page[NameObject("/Resources")] = dictionary(Font=dictionary(F1=writer._add_object(font)))
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 250 Td (Synthetic resume with confirmed support experience.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    return writer


def pdf_bytes(writer):
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def add_link(writer, action):
    annotation = dictionary(Type=NameObject("/Annot"), Subtype=NameObject("/Link"),
                            Rect=ArrayObject([FloatObject(number) for number in (0, 0, 100, 20)]),
                            A=writer._add_object(action))
    writer.pages[0][NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])


class PDFSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for method in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))

    def validate(self, writer, **limits):
        content = pdf_bytes(writer)
        reader = PdfReader(io.BytesIO(content), strict=True)
        validate_pdf_reader(reader, **limits)
        return content

    def reject(self, writer):
        with self.assertRaises(PDFSafetyError) as caught:
            self.validate(writer)
        self.assertEqual(str(caught.exception), PDF_REJECTION_MESSAGE)

    def test_static_pdf_and_original_bytes_unchanged(self):
        content = pdf_bytes(writer_with_text())
        source = io.BytesIO(content)
        validate_pdf_reader(PdfReader(source, strict=True))
        self.assertEqual(source.getvalue(), content)

    def test_ordinary_http_https_uri_links_permitted_without_fetch(self):
        for url in ("http://example.test/profile", "https://example.test/profile?ref=resume"):
            writer = writer_with_text()
            add_link(writer, dictionary(S=NameObject("/URI"), URI=TextStringObject(url)))
            self.validate(writer)

    def test_internal_goto_permitted(self):
        writer = writer_with_text()
        add_link(writer, dictionary(S=NameObject("/GoTo"), D=ArrayObject([writer.pages[0].indirect_reference, NameObject("/Fit")])))
        self.validate(writer)

    def test_javascript_name_tree_rejected(self):
        writer = writer_with_text()
        writer.add_js("/* INERT_SYNTHETIC_SCRIPT: not executed */")
        self.reject(writer)

    def test_openaction_even_harmless_destination_rejected(self):
        writer = writer_with_text()
        writer._root_object[NameObject("/OpenAction")] = ArrayObject([writer.pages[0].indirect_reference, NameObject("/Fit")])
        self.reject(writer)

    def test_additional_actions_on_page_and_form_field_rejected(self):
        for form in (False, True):
            writer = writer_with_text()
            action = dictionary(S=NameObject("/JavaScript"), JS=TextStringObject("/* inert */"))
            if form:
                field = dictionary(FT=NameObject("/Tx"), T=TextStringObject("synthetic"), AA=dictionary(K=writer._add_object(action)))
                writer._root_object[NameObject("/AcroForm")] = dictionary(Fields=ArrayObject([writer._add_object(field)]))
            else:
                writer.pages[0][NameObject("/AA")] = dictionary(O=writer._add_object(action))
            self.reject(writer)

    def test_launch_remote_file_and_external_form_actions_rejected(self):
        for action in ("Launch", "GoToR", "GoToE", "SubmitForm", "ImportData", "Named", "Rendition", "UnknownAction"):
            with self.subTest(action=action):
                writer = writer_with_text()
                add_link(writer, dictionary(S=NameObject("/" + action), F=TextStringObject("https://example.test/inert")))
                self.reject(writer)

    def test_xfa_embedded_files_and_associated_files_rejected(self):
        for kind in ("xfa", "attachment", "associated"):
            writer = writer_with_text()
            if kind == "xfa":
                writer._root_object[NameObject("/AcroForm")] = dictionary(XFA=TextStringObject("inert"))
            elif kind == "attachment":
                writer.add_attachment("inert.txt", b"inert synthetic attachment")
            else:
                writer._root_object[NameObject("/AF")] = ArrayObject([dictionary(Type=NameObject("/Filespec"))])
            self.reject(writer)

    def test_richmedia_and_other_multimedia_annotations_rejected(self):
        for subtype in ("RichMedia", "Screen", "Movie", "Sound", "3D", "FileAttachment"):
            writer = writer_with_text()
            annotation = dictionary(Type=NameObject("/Annot"), Subtype=NameObject("/" + subtype))
            writer.pages[0][NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
            self.reject(writer)

    def test_non_web_and_ambiguous_uri_links_rejected(self):
        for uri in ("javascript:inert", "file:///inert", "data:text/plain,inert", "mailto:synthetic@example.test",
                    "//example.test", "https://user:secret@example.test", "https://example.test/\nvalue"):
            writer = writer_with_text()
            add_link(writer, dictionary(S=NameObject("/URI"), URI=TextStringObject(uri)))
            self.reject(writer)

    def test_action_chains_rejected_even_when_next_is_http(self):
        writer = writer_with_text()
        action = dictionary(S=NameObject("/URI"), URI=TextStringObject("https://example.test"),
                            Next=dictionary(S=NameObject("/URI"), URI=TextStringObject("https://example.test/next")))
        add_link(writer, action)
        self.reject(writer)

    def test_unreferenced_live_action_and_indirect_policy_fields_rejected(self):
        writer = writer_with_text()
        writer._add_object(dictionary(S=writer._add_object(NameObject("/JavaScript")), JS=TextStringObject("/* inert */")))
        self.reject(writer)

    def test_keyword_in_plain_metadata_is_not_an_action(self):
        writer = writer_with_text()
        writer.add_metadata({"/Title": "Literal /JavaScript /OpenAction /Launch words in text"})
        self.validate(writer)

    def test_passive_acroform_without_actions_permitted(self):
        writer = writer_with_text()
        field = dictionary(FT=NameObject("/Tx"), T=TextStringObject("Name"), V=TextStringObject("Synthetic"))
        writer._root_object[NameObject("/AcroForm")] = dictionary(Fields=ArrayObject([writer._add_object(field)]))
        self.validate(writer)

    def test_external_stream_source_rejected_without_reading_it(self):
        writer = writer_with_text()
        stream = DecodedStreamObject()
        stream.set_data(b"")
        stream[NameObject("/F")] = TextStringObject("https://example.test/inert")
        writer._root_object[NameObject("/Synthetic")] = writer._add_object(stream)
        self.reject(writer)

    def test_depth_object_and_edge_limits_fail_closed(self):
        for limits in ({"max_depth": 1}, {"max_objects": 1}, {"max_edges": 2}):
            with self.subTest(limits=limits), self.assertRaises(PDFSafetyError):
                self.validate(writer_with_text(), **limits)

    def test_cycles_terminate_and_still_visit_other_active_branches(self):
        first, second = dictionary(), dictionary()
        first[NameObject("/Cycle")] = second
        second[NameObject("/Cycle")] = first
        reader = SimpleNamespace(is_encrypted=False, trailer=first, xref={}, xref_objStm={}, xref_free_entry={})
        validate_pdf_reader(reader, max_edges=20)
        second[NameObject("/AA")] = dictionary()
        with self.assertRaises(PDFSafetyError):
            validate_pdf_reader(reader, max_edges=20)

    def test_inspection_does_not_decode_content_streams(self):
        content = pdf_bytes(writer_with_text())
        reader = PdfReader(io.BytesIO(content), strict=True)
        with patch.object(DecodedStreamObject, "get_data", side_effect=AssertionError("Do not decode during graph inspection")):
            validate_pdf_reader(reader)

    def test_worker_rejects_before_extract_and_returns_only_static_message(self):
        writer = writer_with_text()
        writer.add_js("/* INERT_PRIVATE_SENTINEL */")
        connection = Mock()
        with patch("resource.setrlimit"), patch("pypdf._page.PageObject.extract_text") as extract:
            studio._pdf_worker(pdf_bytes(writer), connection)
        extract.assert_not_called()
        connection.send.assert_called_once_with((False, PDF_REJECTION_MESSAGE))
        connection.close.assert_called_once()

    def test_worker_extracts_static_pdf_after_policy_check(self):
        connection = Mock()
        with patch("resource.setrlimit"):
            studio._pdf_worker(pdf_bytes(writer_with_text()), connection)
        success, text = connection.send.call_args.args[0]
        self.assertTrue(success)
        self.assertIn("confirmed support experience", text)

    def test_upload_route_rejects_active_pdf_before_storage_with_production_adapter(self):
        import httpx
        from fastapi.testclient import TestClient
        from jobagent.mobile.app import create_app
        from test_mobile_api import FakeSupabase, SETTINGS

        def inline_bounded_worker(content):
            # Exercise the real worker policy/extraction without changing this
            # test process's resource limits or spawning a process/viewer.
            connection = Mock()
            with patch("resource.setrlimit"):
                studio._pdf_worker(content, connection)
            success, result = connection.send.call_args.args[0]
            if not success:
                raise studio.StudioError(result)
            return result

        fake = FakeSupabase()
        writer = writer_with_text()
        writer.add_js("/* INERT_SYNTHETIC_UPLOAD */")
        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(fake), studio=studio)
        with patch.object(studio, "_extract_pdf", side_effect=inline_bounded_worker), \
                patch("jobagent.mobile.app.persist_resume") as persist, TestClient(app) as client:
            response = client.post("/api/mobile/resumes", headers={"Authorization": "Bearer session-a"},
                                   json={"filename": "synthetic.pdf", "label": "Synthetic",
                                         "content_base64": base64.b64encode(pdf_bytes(writer)).decode()})
        self.assertEqual(response.status_code, 422)
        persist.assert_not_called()
        self.assertFalse(any(req.url.path.startswith("/storage/") for req in fake.requests))


if __name__ == "__main__":
    unittest.main()
