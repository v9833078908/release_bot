from app.models import Post


def test_post_defaults():
    p = Post()
    assert p.themes == [] and p.fixes == []
