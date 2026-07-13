from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import ollama
from docx import Document
from pypdf import PdfReader

from jobagent.config import settings
from jobagent.models import Profile

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def find_resume_file(resumes_dir: Optional[Path] = None) -> Path:
    resumes_dir = resumes_dir or settings.resumes_dir
    candidates = [
        p for p in resumes_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No .pdf or .docx resume found in {resumes_dir}. Drop your resume there first."
        )
    return sorted(candidates)[0]


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if path.suffix.lower() == ".docx":
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError(f"Unsupported resume file type: {path.suffix}")


EXTRACTION_PROMPT = """You extract structured data from a resume's raw text.
Return ONLY a JSON object with these keys, no other text:
{{
  "skills": [list of technical/professional skills, max 25],
  "titles": [list of past job titles, most recent first],
  "years_experience": integer estimate of total years of professional experience,
  "summary": "2-3 sentence professional summary"
}}

Resume text:
---
{resume_text}
---
"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output")
    return json.loads(match.group(0))


def parse_resume(path: Optional[Path] = None) -> Profile:
    resume_path = path or find_resume_file()
    raw_text = extract_text(resume_path)

    prompt = EXTRACTION_PROMPT.format(resume_text=raw_text[:8000])
    if settings.rank_provider == "openai" and settings.openai_api_key:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_rank_model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content or ""
    else:
        response = ollama.chat(
            model=settings.ollama_rank_model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 8192},
        )
        content = response["message"]["content"]

    try:
        data = _extract_json(content)
    except (ValueError, json.JSONDecodeError):
        print(
            "WARNING: structured profile extraction failed; matching will rely on raw resume text only."
        )
        return Profile(raw_text=raw_text)

    return Profile(
        raw_text=raw_text,
        skills=data.get("skills", []) or [],
        titles=data.get("titles", []) or [],
        years_experience=data.get("years_experience"),
        summary=data.get("summary"),
    )
