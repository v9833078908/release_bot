import json
from pathlib import Path

import httpx

from app.filter import Commit
from app.models import Post

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
    if content.startswith("```"):
        content = content[content.find("{"): content.rfind("}") + 1]
    data = json.loads(content)
    return Post(
        intro=data.get("intro", ""),
        features=list(data.get("features") or []),
        improvements=list(data.get("improvements") or []),
        fixes_summary=data.get("fixes_summary"),
    )


async def draft_release_notes(api_key: str, model: str, commits: list[Commit],
                              hint: str | None = None) -> Post:
    payload = {
        "model": model,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(commits, hint)},
        ],
    }
    async with httpx.AsyncClient(timeout=120) as c:
        last_err: Exception | None = None
        for _ in range(2):  # one retry: the LLM occasionally returns truncated/invalid JSON
            r = await c.post(_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            r.raise_for_status()
            try:
                return _parse_post(r.json()["choices"][0]["message"]["content"])
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                last_err = e
        raise last_err
