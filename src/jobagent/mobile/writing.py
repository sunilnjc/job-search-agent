"""Closed, source-bound document composition; no model prose or external IO.

The studio must first validate every complete candidate claim. This module may
add a first-person subject to a narrowly recognized resume action fragment and
place an exact posting quotation beside it. It cannot paraphrase evidence, infer
an employer, combine metrics, or declare that a requirement is satisfied.
"""
from __future__ import annotations

import re

from .evidence import claim_allowed

IGNORED_TERMS = frozenset(
    "a an the for in on at of to and or with from this that as is are be have has it by "
    "job role work working experience skills required preferred team company candidate "
    "will must should ability able strong excellent good years year responsibility responsibilities".split())
_ACTION = re.compile(
    r"^(Built|Created|Developed|Reduced|Led|Prioritized|Analyzed|Analysed|Coordinated|"
    r"Owned|Shortened|Evaluated|Designed|Investigated|Prepared|Maintained|Organized|"
    r"Organised|Helped|Used|Completed|Delivered|Implemented|Improved|Supported|"
    r"Managed|Automated|Tested|Documented|Resolved|Migrated|Launched|Conducted|"
    r"Collaborated|Contributed|Increased|Saved|Trained) "
)
_PASSIVE_OR_NOUN = re.compile(r"^(?:by|for|to|services? provider)\b", re.I)
_GATE = re.compile(
    r"\b(?:licen[cs]e|licen[cs]ed|registration|certifi\w*|credential|degree|bachelor\w*|master'?s|"
    r"ph\.?d|diploma|visa|sponsor\w*|authori[sz]\w*|relocat\w*|citizen\w*|clearance|CPA|ACCA|NMC)\b", re.I)
_NEGATED = re.compile(r"\b(?:no|not|without|never|lack\w*|don't|do not)\b", re.I)
_LOW_EVIDENCE = re.compile(
    r"^(?:I am|I'm|I’m) (?:passionate|pasionate|enthusiastic|motivated|dedicated|eager|excited)\b", re.I)


def document_terms(text: str) -> set[str]:
    words = (word.casefold().strip(".-") for word in re.findall(r"[\w+#.-]+", text))
    return {word for word in words if len(word) > 1 and word not in IGNORED_TERMS}


def quality_flags(text: str, section: str) -> list[str]:
    """Omit an entire unsuitable fact; never launder or silently edit its words."""
    flags = []
    if _LOW_EVIDENCE.match(text):
        flags.append("generic_motivation_not_evidence")
    if section == "skills" and re.search(r"\b([\w+#.-]+)(?:[ ,;]+\1){2,}\b", text, re.I):
        flags.append("repeated_keywords_need_confirmation")
    return flags


def letter_sentence(text: str) -> tuple[str, str]:
    """One reversible grammatical operation, never model-generated rewriting.

    Unsupported fragments remain verbatim. No trimming, spelling fixes, pronoun
    substitution, clause removal or tense changes are allowed. The entire suffix
    (including attribution, numbers and negations) is byte-for-byte preserved.
    """
    match = _ACTION.match(text)
    if match and not _PASSIVE_OR_NOUN.match(text[match.end():]):
        return "I " + text[0].lower() + text[1:], "first_person_subject"
    return text, "verbatim"


def connection_options(facts: list[dict], hints: dict) -> dict[str, list[dict]]:
    """Literal positive-topic overlap only, not qualification/fit equivalence.

    Legal/credential gates, negated job criteria, long paragraphs and source
    instructions are never rendered as positive letter connections. Evidence
    limitations are retained in the output even when excluded from topic cues.
    """
    title = next((fact["text"] for fact in facts if fact["id"] == "job.title"), "")
    title_terms = document_terms(title)
    def title_label(text):
        # "B2B SaaS Product Manager" is a role label, not a duty. Do not use
        # such a line as false personalization when actual duties are available.
        return bool(title_terms and title_terms <= document_terms(text)
                    and len(text.split()) <= len(title.split()) + 3
                    and not re.search(r"\b(?:required|preferred|must|build|lead|manage|develop|deliver)\b", text, re.I))
    requirements = [fact for fact in facts if fact["id"].startswith("job.requirements.")
                    and claim_allowed(fact) and len(fact["text"]) <= 360
                    and len(fact["text"].split()) <= 45
                    and not _GATE.search(fact["text"]) and not _NEGATED.search(fact["text"])
                    and not title_label(fact["text"])]
    result = {}
    for fact in facts:
        hint = hints.get(fact["id"], {})
        if not hint or hint.get("quality_flags") or hint.get("employment_header"):
            continue
        if hint.get("section") in {"context_only", "heading_only", "qualifications", "skills"}:
            continue
        # Text before the first limitation is only a cue, never the output.
        positive = _NEGATED.split(fact["text"], maxsplit=1)[0]
        terms = document_terms(positive)
        options = []
        for requirement in requirements:
            overlap = sorted(terms & document_terms(requirement["text"]))
            if overlap:
                options.append({"job_source_id": requirement["id"], "literal_topics": overlap})
        if options:
            result[fact["id"]] = sorted(options, key=lambda item: -len(item["literal_topics"]))[:3]
    return result


def compose_letter(claims: list, facts: list[dict], hints: dict) -> list[dict]:
    """Return auditable paragraphs; each candidate fact appears exactly once.

    Quotes introduce the employer's topic, not a claim of suitability. No more
    than two short posting excerpts are used; unrelated examples are not forced
    into an invented narrative. Adjacent examples share a paragraph at <=90 words.
    """
    ledger = {fact["id"]: fact for fact in facts}
    options = connection_options(facts, hints)
    used_jobs, paragraphs = set(), []
    # Prioritize actual topic connections but preserve the model's order on ties.
    ordered = sorted(claims, key=lambda claim: not bool(options.get(claim.source_ids[0])))
    for claim in ordered:
        ref = claim.source_ids[0]
        # Defence in depth: composition never takes arbitrary model-written text.
        fact = ledger.get(ref)
        if not fact or not claim_allowed(fact, documents=True) or fact["text"] != claim.text:
            raise ValueError("Document composition requires complete validated candidate evidence.")
        sentence, operation = letter_sentence(claim.text)
        source = {"source_id": ref, "text": sentence, "operation": operation}
        connection = next((item for item in options.get(ref, [])
                           if item["job_source_id"] not in used_jobs), None) if len(used_jobs) < 2 else None
        if connection:
            job_ref = connection["job_source_id"]
            used_jobs.add(job_ref)
            quote = ledger[job_ref]["text"]
            paragraph = {"text": f'Your posting highlights “{quote}” {sentence}',
                         "candidate_sentences": [source], "job_source_id": job_ref,
                         "job_quote": quote, "literal_topics": connection["literal_topics"]}
            paragraphs.append(paragraph)
        elif paragraphs and len((paragraphs[-1]["text"] + " " + sentence).split()) <= 90:
            # A paragraph is a collection of evidence, not an employer assignment.
            separator = " " if paragraphs[-1]["text"].endswith((".", "!", "?", ";")) else ". "
            paragraphs[-1]["text"] += separator + sentence
            paragraphs[-1]["candidate_sentences"].append(source)
        else:
            paragraphs.append({"text": sentence, "candidate_sentences": [source],
                               "job_source_id": None, "job_quote": None, "literal_topics": []})
    return paragraphs
