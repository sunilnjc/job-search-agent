"""Read-only PDF policy inspection; call inside the resource-limited PDF worker.

No streams are explicitly decoded here, no actions/JavaScript are executed, no
links are fetched, and no original bytes are rewritten. Object-stream resolution
can still decompress data in pypdf: the caller MUST retain parser CPU/memory/time
limits. This is an active-content policy, not malware or viewer-safety clearance.
Ordinary HTTP(S) URI links and internal GoTo links are permitted; auto-open,
additional actions, external-file navigation and all other actions are rejected.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, TextStringObject


PDF_REJECTION_MESSAGE = (
    "This PDF contains active, embedded, unsupported, or overly complex content. "
    "Export a new static, text-based PDF without scripts, automatic actions, "
    "attachments or multimedia, or upload a DOCX. The original file was not changed."
)


class PDFSafetyError(ValueError):
    """Fixed, user-safe failure text; never include a PDF string or parser error."""

    def __init__(self):
        super().__init__(PDF_REJECTION_MESSAGE)


_FORBIDDEN_KEYS = frozenset({
    "/JS", "/JavaScript", "/OpenAction", "/AA", "/Launch", "/XFA", "/EmbeddedFiles",
    "/EF", "/AF", "/RichMedia", "/RichMediaContent", "/RichMediaSettings", "/3DD",
    "/3DA", "/3DV", "/Movie", "/Sound", "/Rendition", "/Collection", "/Ref",
})
_FORBIDDEN_TYPES = frozenset({"/EmbeddedFile", "/Filespec", "/RichMedia", "/RichMediaContent"})
_FORBIDDEN_SUBTYPES = frozenset({"/RichMedia", "/Screen", "/Movie", "/Sound", "/3D", "/FileAttachment"})
_ACTIONS = frozenset({
    "/GoTo", "/URI", "/GoToR", "/GoToE", "/Launch", "/JavaScript", "/SubmitForm",
    "/ImportData", "/Rendition", "/RichMediaExecute", "/Sound", "/Movie", "/Named",
    "/SetOCGState", "/Trans", "/Hide", "/ResetForm", "/Thread", "/GoTo3DView",
})


def validate_pdf_reader(reader, *, max_depth: int = 64, max_objects: int = 20000,
                        max_edges: int = 100000) -> None:
    """Reject unsafe/unsupported content in the current trailer and live xref graph.

    Visit direct dictionaries/arrays AND indirect objects, including live orphan
    objects. Cyclic page-parent graphs are ordinary and tracked, not recursed.
    Traversal does not call get_data(), extract_text(), or a viewer. Never call
    this on hostile bytes in the ASGI process; use studio.extract_resume_text.
    """
    try:
        _inspect(reader, max_depth=max_depth, max_objects=max_objects, max_edges=max_edges)
    except PDFSafetyError:
        raise
    except Exception:
        raise PDFSafetyError() from None


def _inspect(reader, *, max_depth, max_objects, max_edges):
    if any(type(limit) is not int or limit <= 0 for limit in (max_depth, max_objects, max_edges)):
        raise PDFSafetyError()
    if reader.is_encrypted:
        raise PDFSafetyError()
    seen_refs, seen_containers = set(), set()
    visits = 0
    pending = [(reader.trailer, 0, False)]

    def resolve(value):
        # Policy fields themselves may be indirect; bound and track ref chains.
        chain = set()
        while isinstance(value, IndirectObject):
            ref = (value.generation, value.idnum)
            if value.pdf is not reader or ref in chain or len(chain) >= max_depth:
                raise PDFSafetyError()
            chain.add(ref)
            value = value.get_object()
            if value is None:
                raise PDFSafetyError()
        return value

    def check_dictionary(node, action_context):
        if any(key in _FORBIDDEN_KEYS for key in node):
            raise PDFSafetyError()
        kind = resolve(dict.get(node, "/Type"))
        subtype = resolve(dict.get(node, "/Subtype"))
        if kind in _FORBIDDEN_TYPES or subtype in _FORBIDDEN_SUBTYPES:
            raise PDFSafetyError()
        action = resolve(dict.get(node, "/S"))
        is_action = action_context or kind == "/Action" or (isinstance(action, NameObject) and action in _ACTIONS)
        if is_action:
            if action not in ("/GoTo", "/URI") or "/Next" in node:
                raise PDFSafetyError()
            if action == "/URI":
                uri = resolve(dict.get(node, "/URI"))
                if (not isinstance(uri, TextStringObject) or not 1 <= len(uri) <= 4096
                        or any(ord(char) <= 32 or ord(char) == 127 for char in uri) or "\\" in uri):
                    raise PDFSafetyError()
                parsed = urlsplit(uri)
                if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None):
                    raise PDFSafetyError()
                _ = parsed.port
        # A stream's /F can reference external data. Plain file specifications
        # and all known external actions are rejected above as well.
        if "/F" in node and (hasattr(node, "get_data") or is_action):
            raise PDFSafetyError()
        return is_action

    def drain():
        nonlocal visits
        while pending:
            node, depth, action_context = pending.pop()
            visits += 1
            if visits > max_edges:
                raise PDFSafetyError()
            if isinstance(node, IndirectObject):
                ref = (node.generation, node.idnum)
                if node.pdf is not reader:
                    raise PDFSafetyError()
                if ref in seen_refs:
                    # Re-check policy when a shared object is used as an action.
                    if action_context:
                        value = resolve(node)
                        if not isinstance(value, DictionaryObject):
                            raise PDFSafetyError()
                        check_dictionary(value, True)
                    continue
                if depth > max_depth or len(seen_refs) >= max_objects:
                    raise PDFSafetyError()
                seen_refs.add(ref)
                value = node.get_object()
                if value is None:
                    raise PDFSafetyError()
                pending.append((value, depth + 1, action_context))
            elif isinstance(node, (DictionaryObject, ArrayObject)):
                if id(node) in seen_containers:
                    if action_context and isinstance(node, DictionaryObject):
                        check_dictionary(node, True)
                    continue
                if (depth > max_depth or len(seen_containers) >= max_objects
                        or len(pending) + len(node) > max_edges - visits):
                    raise PDFSafetyError()
                seen_containers.add(id(node))
                if isinstance(node, DictionaryObject):
                    is_action = check_dictionary(node, action_context)
                    # items() preserves raw references; __getitem__ auto-resolves.
                    for key, value in node.items():
                        pending.append((value, depth + 1, key == "/A" or (is_action and key == "/Next")))
                else:
                    for value in node:
                        pending.append((value, depth + 1, action_context))
            elif action_context:
                raise PDFSafetyError()

    drain()
    # Inspection of live orphan objects avoids silently retaining active payloads
    # simply because the currently traversed catalog doesn't reference them.
    xref_count = 0
    for generation, entries in reader.xref.items():
        for number in entries:
            if not number or getattr(reader, "xref_free_entry", {}).get(generation, {}).get(number, False):
                continue
            xref_count += 1
            if xref_count > max_objects:
                raise PDFSafetyError()
            pending.append((IndirectObject(number, generation, reader), 0, False))
            drain()
    for number in reader.xref_objStm:
        xref_count += 1
        if xref_count > max_objects:
            raise PDFSafetyError()
        pending.append((IndirectObject(number, 0, reader), 0, False))
        drain()
