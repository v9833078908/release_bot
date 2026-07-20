import json
from pathlib import Path

import httpx

from app.filter import Commit
from app.models import Post, Theme

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "release_notes_ru.md"
_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_message(commits: list[Commit], hint: str | None) -> str:
    lines = [f"- {c.type}({c.scope or '-'}): {c.subject}" for c in commits]
    body = "Commits:\n" + "\n".join(lines)
    if hint:
        body += f"\n\nAdditional note from the editor: {hint}"
    return body


def _parse_post(content: str) -> Post:
    content = content.strip()
    if not (content.startswith("{") and content.endswith("}")):
        s, e = content.find("{"), content.rfind("}")  # tolerate code fences / prose around the JSON
        if s != -1 and e > s:
            content = content[s:e + 1]
    data = json.loads(content)
    themes = [
        Theme(title=(t.get("title") or "").strip(), body=(t.get("body") or "").strip())
        for t in (data.get("themes") or [])
        if (t.get("title") or t.get("body"))
    ]
    return Post(
        themes=themes,
        fixes=[f.strip() for f in (data.get("fixes") or []) if f and f.strip()],
    )


async def draft_release_notes(api_key: str, model: str, commits: list[Commit],
                              hint: str | None = None) -> Post:
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(commits, hint)},
        ],
    }
    async with httpx.AsyncClient(timeout=180) as c:
        last_err: Exception | None = None
        for _ in range(3):  # reasoning models occasionally return null/truncated content
            r = await c.post(_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            r.raise_for_status()
            try:
                content = r.json()["choices"][0]["message"]["content"]
                if not content or not content.strip():
                    raise ValueError("empty content from model")
                return _parse_post(content)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                last_err = e
        raise last_err
