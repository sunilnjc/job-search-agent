from __future__ import annotations

import math

import ollama

from jobagent.config import settings

MAX_CHARS = 6000
MIN_CHARS = 500


def embed(text: str) -> list[float]:
    """Embed text, halving the input on context-length errors.

    Older Ollama servers ignore num_ctx for embeddings and hard-cap at 2048
    tokens; char->token ratios also vary wildly (non-English or mojibake text
    tokenizes much denser), so no fixed char cutoff is safe.
    """
    length = min(len(text), MAX_CHARS)
    while True:
        try:
            response = ollama.embeddings(
                model=settings.ollama_embed_model,
                prompt=text[:length],
                options={"num_ctx": 8192},
            )
            return response["embedding"]
        except ollama.ResponseError as exc:
            if "context length" not in str(exc).lower() or length <= MIN_CHARS:
                raise
            length //= 2


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
