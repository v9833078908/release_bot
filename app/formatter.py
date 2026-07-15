import html

from app.models import Post

TG_LIMIT = 4096
SPLIT_TARGET = 3800


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def render_html(post: Post) -> str:
    parts = ["<b>🚀 Game Pulse — что нового</b>", "", _esc(post.intro)]
    if post.features:
        parts += ["", "<b>✨ Новое</b>", *[f"• {_esc(x)}" for x in post.features]]
    if post.improvements:
        parts += ["", "<b>⚡ Улучшения</b>", *[f"• {_esc(x)}" for x in post.improvements]]
    if post.fixes_summary:
        parts += ["", f"🐞 {_esc(post.fixes_summary)}"]
    return "\n".join(parts)


def split_message(text: str, limit: int = SPLIT_TARGET) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
