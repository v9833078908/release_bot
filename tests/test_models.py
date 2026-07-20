from app.models import Post


def test_post_defaults():
    p = Post(intro="hi")
    assert p.themes == [] and p.fixes == []
