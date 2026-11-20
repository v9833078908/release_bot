import json
from pathlib import Path

import httpx

from app.filter import Commit
from app.models import Post, Theme

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "release_notes_ru.md"
_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_message(
    commits: list[Commit],
    hint: str | None,
    context: str | None = None,
) -> str:
    lines: list[str] = []
    for c in commits:
        entry = f"- [{c.sha[:8]}] {c.type}({c.scope or '-'}): {c.subject}"
        if c.surfaces:
            entry += f"\n  surfaces: {', '.join(c.surfaces)}"
        if c.body:
            entry += f"\n  details: {c.body}"
        lines.append(entry)
    body = "Commits:\n" + "\n".join(lines)
    if context and context.strip():
        body += f"\n\nRollout flags (infra/rollout-flags.env):\n{context.strip()}"
    if hint:
        body += f"\n\nAdditional note from the editor: {hint}"
    return body


def _shas_grounded(raw_shas: object, known_shas: set[str] | None) -> bool:
    """When known_shas is non-empty and source_shas is provided, ensure at least one SHA matches."""
    if not known_shas:
        return True
    if raw_shas is None:
        return True
    if not isinstance(raw_shas, list):
        return False
    cleaned = [str(s).strip().lower() for s in raw_shas if str(s).strip()]
    if not cleaned:
        return False
    return any(any(k.startswith(ref) or ref.startswith(k[:8]) for k in known_shas) for ref in cleaned)


def _parse_post(content: str, *, known_shas: set[str] | None = None) -> Post:
    content = content.strip()
    if not (content.startswith("{") and content.endswith("}")):
        s, e = content.find("{"), content.rfind("}")  # tolerate code fences / prose around the JSON
        if s != -1 and e > s:
            content = content[s:e + 1]
    data = json.loads(content)

    themes: list[Theme] = []
    for t in (data.get("themes") or []):
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip().rstrip(".")
        body = (t.get("body") or "").strip()
        if not (title or body):
            continue
        if "source_shas" in t and not _shas_grounded(t.get("source_shas"), known_shas):
            continue
        themes.append(Theme(title=title, body=body))

    fixes: list[str] = []
    for f in (data.get("fixes") or []):
        if isinstance(f, dict):
            text = (f.get("text") or "").strip()
            if not text:
                continue
            if "source_shas" in f and not _shas_grounded(f.get("source_shas"), known_shas):
                continue
            fixes.append(text)
        elif isinstance(f, str) and f.strip():
            fixes.append(f.strip())

    return Post(themes=themes, fixes=fixes)


async def draft_release_notes(
    api_key: str,
    model: str,
    commits: list[Commit],
    hint: str | None = None,
    *,
    context: str | None = None,
) -> Post:
    payload: dict = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(commits, hint, context)},
        ],
    }
    known_shas = {c.sha.lower() for c in commits if c.sha}
    async with httpx.AsyncClient(timeout=180) as c:
        last_err: Exception | None = None
        for _ in range(3):  # reasoning models occasionally return null/truncated content
            r = await c.post(_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            if r.status_code == 400 and "temperature" in payload and "temperature" in r.text.lower():
                payload = {k: v for k, v in payload.items() if k != "temperature"}
                r = await c.post(_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            r.raise_for_status()
            try:
                content = r.json()["choices"][0]["message"]["content"]
                if not content or not content.strip():
                    raise ValueError("empty content from model")
                return _parse_post(content, known_shas=known_shas or None)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                last_err = e
        raise last_err
