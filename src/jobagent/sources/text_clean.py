from __future__ import annotations

import html

from bs4 import BeautifulSoup


def clean_html(text: str) -> str:
    """Strip HTML tags/entities from a job description, returning plain text."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    if "<" not in unescaped:
        return " ".join(unescaped.split())
    soup = BeautifulSoup(unescaped, "html.parser")
    return " ".join(soup.get_text(separator=" ", strip=True).split())
