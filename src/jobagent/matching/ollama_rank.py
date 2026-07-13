from __future__ import annotations

import json
import re
from typing import Optional

import ollama

from jobagent.config import settings
from jobagent.models import Profile

RANK_PROMPT = """You are screening a job posting for fit against a candidate's profile.
Return ONLY a JSON object, no other text: {{"score": <1-10 integer>, "reasoning": "<one short sentence>"}}

Candidate summary: {summary}
Candidate skills: {skills}
Candidate past titles: {titles}
Candidate years of experience: {years}
Candidate resume (excerpt):
---
{resume_text}
---

Job title: {job_title}
Job company: {job_company}
Job location: {job_location}
Job description:
---
{job_description}
---
"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output")
    return json.loads(match.group(0))


def rank_job(profile: Profile, job_title: str, job_company: str, job_location: str, job_description: str) -> tuple[Optional[int], Optional[str]]:
    prompt = RANK_PROMPT.format(
        summary=profile.summary or "(none extracted)",
        skills=", ".join(profile.skills) or "(none extracted)",
        titles=", ".join(profile.titles) or "(none extracted)",
        years=profile.years_experience or "unknown",
        resume_text=profile.raw_text[:3000],
        job_title=job_title,
        job_company=job_company,
        job_location=job_location,
        job_description=job_description[:4000],
    )

    if settings.rank_provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_rank_model,
            max_tokens=150,
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
        score = int(data.get("score"))
        reasoning = str(data.get("reasoning", ""))
        return score, reasoning
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, None
