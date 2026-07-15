from app.models import Post


def test_post_defaults():
    p = Post(intro="hi")
    assert p.features == [] and p.improvements == [] and p.fixes_summary is None
