from __future__ import annotations

from jobagent.config import settings

ANTHROPIC_MODEL = "claude-sonnet-5"


def resolve_provider() -> str:
    """Pick the drafting provider: explicit DRAFT_PROVIDER wins, else first configured key."""
    if settings.draft_provider:
        return settings.draft_provider
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    return "ollama"


def complete(prompt: str, max_tokens: int = 1500) -> str:
    provider = resolve_provider()
    if provider == "anthropic":
        return _complete_anthropic(prompt, max_tokens)
    if provider == "openai":
        return _complete_openai(prompt, max_tokens)
    if provider == "ollama":
        return _complete_ollama(prompt)
    raise ValueError(f"Unknown DRAFT_PROVIDER '{provider}'. Use anthropic, openai, or ollama.")


def _complete_anthropic(prompt: str, max_tokens: int) -> str:
    import anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def _complete_openai(prompt: str, max_tokens: int) -> str:
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_draft_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _complete_ollama(prompt: str) -> str:
    import ollama

    response = ollama.chat(
        model=settings.ollama_draft_model,
        messages=[{"role": "user", "content": prompt}],
        options={"num_ctx": 8192},
    )
    return response["message"]["content"]


def chat(system: str, messages: list[dict], max_tokens: int = 1200) -> str:
    """Multi-turn chat with a system prompt and a [{role, content}, ...] history.

    Uses the same provider resolution as complete(). Used by the application-question
    assistant, which keeps a running conversation grounded in the resume + job context.
    """
    provider = resolve_provider()
    if provider == "anthropic":
        import anthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(block.text for block in message.content if block.type == "text")

    if provider == "openai":
        from openai import OpenAI

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_draft_model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
        )
        return response.choices[0].message.content or ""

    import ollama

    response = ollama.chat(
        model=settings.ollama_draft_model,
        messages=[{"role": "system", "content": system}, *messages],
        options={"num_ctx": 8192},
    )
    return response["message"]["content"]
