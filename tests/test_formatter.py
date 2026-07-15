from app.formatter import render_html, split_message
from app.models import Post


def test_render_escapes_and_bolds():
    p = Post(intro="A & B", features=["<x> fast"], improvements=[], fixes_summary=None)
    out = render_html(p)
    assert "<b>🚀 Game Pulse — что нового</b>" in out
    assert "A &amp; B" in out
    assert "• &lt;x&gt; fast" in out
    assert "<b>✨ Новое</b>" in out
    assert "Пишите, что улучшить" not in out


def test_render_omits_empty_groups_and_fixes():
    out = render_html(Post(intro="i", features=["f"]))
    assert "Улучшения" not in out
    assert "🐞" not in out


def test_render_includes_fixes_summary_line():
    out = render_html(Post(intro="i", features=["f"], fixes_summary="мелкие правки"))
    assert "🐞 мелкие правки" in out


def test_split_on_line_boundaries():
    text = "\n".join(f"line{i}" for i in range(100))
    chunks = split_message(text, limit=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)


def test_short_message_single_chunk():
    assert split_message("hello", limit=100) == ["hello"]
