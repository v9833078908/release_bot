import html

from app.models import Post

TG_LIMIT = 4096
SPLIT_TARGET = 3800


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def render_html(post: Post) -> str:
    parts = ["<b>🚀 Game Pulse — что нового</b>", "", _esc(post.intro)]
    for t in post.themes:
        parts += ["", f"<b>{_esc(t.title)}</b>", _esc(t.body)]
    if post.fixes:
        parts += ["", "<b>🐞 Исправления</b>", *[f"• {_esc(x)}" for x in post.fixes]]
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


def finalize_publish(text: str, release_no: int, build_sha: str, when: str) -> str:
    lines = text.split("\n")
    if lines and lines[0].endswith("</b>"):
        lines[0] = lines[0][:-4] + f" · #{release_no}</b>"
    return "\n".join(lines) + f"\n\n<i>сборка {build_sha[:8]} · {when}</i>"
