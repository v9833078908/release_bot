from app.formatter import render_html, split_message
from app.models import Post, Theme


def test_render_escapes_and_bolds():
    p = Post(themes=[Theme(title="<x> fast", body="<b>ody</b> & more")])
    out = render_html(p)
    assert out.splitlines()[0] == "<b>🚀 Game Pulse — что нового</b>"
    assert "<b>&lt;x&gt; fast</b>" in out
    assert "&lt;b&gt;ody&lt;/b&gt; &amp; more" in out


def test_render_omits_empty_themes_and_fixes():
    out = render_html(Post(themes=[Theme(title="T", body="B")]))
    assert "🐞" not in out
    assert "Исправления" not in out


def test_render_includes_fixes_as_bullets():
    out = render_html(Post(themes=[], fixes=["правка один", "правка два"]))
    assert "<b>🐞 Исправления</b>" in out
    assert "• правка один" in out
    assert "• правка два" in out


def test_split_on_line_boundaries():
    text = "\n".join(f"line{i}" for i in range(100))
    chunks = split_message(text, limit=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)


def test_short_message_single_chunk():
    assert split_message("hello", limit=100) == ["hello"]


def test_finalize_publish_adds_number_and_footer():
    from app.formatter import finalize_publish
    base = render_html(Post(themes=[Theme(title="T", body="B")]))
    out = finalize_publish(base, 2, "3388b82c0273890458427b55466763d5d5d5603f", "16.07.2026")
    assert out.splitlines()[0] == "<b>🚀 Game Pulse — что нового · #2</b>"
    assert "<i>сборка 3388b82c · 16.07.2026</i>" in out
    assert out.count("#2") == 1
