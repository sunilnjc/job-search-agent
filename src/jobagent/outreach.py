"""Public recruiting-inbox discovery and explicitly approved outreach delivery."""
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx

from jobagent.config import settings
from jobagent.drafting.llm import chat

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
ROLE_INBOX_PREFIXES = ("career", "careers", "job", "jobs", "recruit", "recruiting", "talent", "hiring")


def find_public_recruiting_inbox(job_url: str, description: str) -> str | None:
    """Return a *role inbox* published on the job/careers page, never guessed contacts.

    This avoids harvesting employees' personal email addresses. One page request is used
    as a fallback if the job description itself does not provide a contact.
    """
    text = description
    try:
        response = httpx.get(job_url, follow_redirects=True, timeout=12)
        if response.status_code < 400:
            text += "\n" + response.text[:150_000]
    except httpx.HTTPError:
        pass
    for candidate in EMAIL_RE.findall(text):
        local, _, domain = candidate.lower().partition("@")
        # A hosted ATS is commonly on greenhouse.io/workday.com while the published
        # recruiting inbox is on the employer's own domain, so domain equality would
        # incorrectly discard valid public role inboxes.
        if local.startswith(ROLE_INBOX_PREFIXES) and domain:
            return candidate
    return None


def draft_outreach_email(*, resume_text: str, title: str, company: str, description: str) -> tuple[str, str]:
    system = """You write truthful, concise recruiter outreach for Sunilkumar Kalabandi. Use only
the resume facts below. Do not invent metrics, contacts, referrals, work authorization, or claims.
Return exactly two parts in this format:\nSUBJECT: <subject>\nBODY:\n<body>\n
Keep the body under 170 words, mention the role and two relevant truthful strengths, state that a
tailored resume and cover letter are attached, and end with Sunilkumar Kalabandi."""
    prompt = f"""ROLE: {title}\nCOMPANY: {company}\nJOB DESCRIPTION:\n{description[:3500]}\n\nRESUME:\n{resume_text[:6000]}"""
    raw = chat(system, [{"role": "user", "content": prompt}], max_tokens=700).strip()
    subject_match = re.search(r"^SUBJECT:\s*(.+)$", raw, re.M)
    body_match = re.search(r"BODY:\s*(.*)$", raw, re.S)
    subject = subject_match.group(1).strip() if subject_match else f"Interest in {title} — Sunilkumar Kalabandi"
    body = body_match.group(1).strip() if body_match else raw
    return subject, body


def send_outreach_email(recipient: str, subject: str, body: str, attachments: list[Path]) -> None:
    """Send only after an explicit dashboard action and only when SMTP is configured."""
    if not settings.outreach_smtp_host or not settings.outreach_from_email:
        raise RuntimeError("Email sending is not configured. Set OUTREACH_SMTP_HOST and OUTREACH_FROM_EMAIL in .env.")
    message = EmailMessage()
    message["From"] = settings.outreach_from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    for path in attachments:
        if not path.is_file():
            continue
        content = path.read_bytes()
        maintype, subtype = ("application", "pdf") if path.suffix.lower() == ".pdf" else ("application", "octet-stream")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=path.name)
    with smtplib.SMTP_SSL(settings.outreach_smtp_host, settings.outreach_smtp_port, timeout=20) as server:
        if settings.outreach_smtp_username:
            server.login(settings.outreach_smtp_username, settings.outreach_smtp_password)
        server.send_message(message)
