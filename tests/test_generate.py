import json

import pytest

from app.generate import generate_draft, regenerate_draft
from app.models import Post
from app.store import Store


class FakeGitHub:
    def __init__(self, commits):
        self._commits = commits

    async def commits_in_range(self, base, head):
        return self._commits


class Cfg:
    min_features_to_publish = 1
    openrouter_api_key = "k"
    llm_model = "m"


async def _fake_llm(*a, **k):
    return Post(intro="I", features=["F"], improvements=[], fixes_summary="melochi")


def _prod(sha):
    async def _get():
        return sha
    return _get


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"), initial_marker_sha="base0")


async def test_no_prod_sha(store):
    res = await generate_draft(trigger="scheduled", store=store, github=FakeGitHub([]),
                               get_prod_sha=_prod(None), settings=Cfg(), llm=_fake_llm)
    assert res["result"] == "no_prod_sha"
    assert store.get_marker() == "base0"


async def test_no_changes_when_prod_equals_marker(store):
    res = await generate_draft(trigger="scheduled", store=store, github=FakeGitHub([]),
                               get_prod_sha=_prod("base0"), settings=Cfg(), llm=_fake_llm)
    assert res["result"] == "no_changes"


async def test_scheduled_below_feature_threshold_skips(store):
    gh = FakeGitHub([("s1", "fix(x): a")])  # a fix, zero features
    res = await generate_draft(trigger="scheduled", store=store, github=gh,
                               get_prod_sha=_prod("head1"), settings=Cfg(), llm=_fake_llm)
    assert res["result"] == "skipped"
    assert res["feature_count"] == 0
    assert store.get_marker() == "base0"  # accumulates


async def test_manual_ignores_threshold_and_drafts(store):
    gh = FakeGitHub([("s1", "fix(x): a")])
    res = await generate_draft(trigger="manual", store=store, github=gh,
                               get_prod_sha=_prod("head1"), settings=Cfg(), llm=_fake_llm)
    assert res["result"] == "drafted"
    assert "🚀 Game Pulse" in res["text"]
    assert store.has_pending() is True


async def test_scheduled_with_feature_drafts(store):
    gh = FakeGitHub([("s1", "feat(a): x"), ("s2", "fix(b): y")])
    res = await generate_draft(trigger="scheduled", store=store, github=gh,
                               get_prod_sha=_prod("head1"), settings=Cfg(), llm=_fake_llm)
    assert res["result"] == "drafted"
    assert res["feature_count"] == 1


async def test_regenerate_reuses_cached_commits(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="head1", commit_count=1, feature_count=1,
                             raw_commits=[{"sha": "s", "type": "feat", "scope": "x",
                                           "subject": "y", "breaking": False}],
                             draft_text="old")

    async def llm(_key, _model, commits, hint=None):
        assert commits[0].subject == "y" and hint == "короче"
        return Post(intro="NEW", features=[], improvements=[], fixes_summary=None)

    text = await regenerate_draft(store=store, draft_id=did, settings=Cfg(), llm=llm, hint="короче")
    assert "NEW" in text
    assert "NEW" in store.get_draft(did)["draft_text"]
