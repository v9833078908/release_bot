import json

import pytest

from app.generate import generate_draft, regenerate_draft, is_publishable, publish_block_reason
from app.models import Post, Theme
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
    feature_prefix_list = []


async def _fake_llm(*a, **k):
    return Post(themes=[Theme(title="F", body="B")], fixes=["melochi"])


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
        return Post(themes=[Theme(title="NEW", body="b")], fixes=[])

    text = await regenerate_draft(store=store, draft_id=did, settings=Cfg(), llm=llm, hint="короче")
    assert "NEW" in text
    assert "NEW" in store.get_draft(did)["draft_text"]


async def test_preview_to_sha_override_drafts_over_main(store):
    # get_prod_sha == marker would yield no_changes; the main-HEAD override must draft anyway.
    gh = FakeGitHub([("s1", "feat(a): x")])
    res = await generate_draft(trigger="preview", store=store, github=gh,
                               get_prod_sha=_prod("base0"), settings=Cfg(), llm=_fake_llm,
                               to_sha="mainhead")
    assert res["result"] == "drafted"
    assert store.get_draft(res["draft_id"])["to_sha"] == "mainhead"
    assert store.get_marker() == "base0"  # preview never advances the marker


def test_is_publishable_only_when_to_sha_is_current_prod():
    assert is_publishable("abc", "abc") is True
    assert is_publishable("abc", "def") is False
    assert is_publishable("abc", None) is False


def test_publish_block_reason_none_when_equal():
    assert publish_block_reason("abc", "abc") is None


def test_publish_block_reason_message_when_different():
    msg = publish_block_reason("aaaaaaaa11", "bbbbbbbb22")
    assert msg is not None
    assert "aaaaaaaa" in msg and "bbbbbbbb" in msg


def test_publish_block_reason_message_when_prod_none():
    assert publish_block_reason("abc", None) is not None


def test_publish_block_reason_preview_message_when_not_yet_deployed():
    # /preview drafts target main HEAD, which is AHEAD of prod (opposite direction
    # from a stale deploy/manual draft) - the message must not claim "prod moved forward".
    msg = publish_block_reason("aaaaaaaa11", "bbbbbbbb22", trigger="preview")
    assert msg is not None
    assert "прод ушёл вперёд" not in msg
    assert "превью" in msg


async def test_feature_prefix_drafts_via_generate(store):
    gh = FakeGitHub([("s1", "VIP Board: connection gate")])
    cfg = Cfg()
    cfg.feature_prefix_list = ["VIP Board"]
    res = await generate_draft(trigger="deploy", store=store, github=gh,
                               get_prod_sha=_prod("P"), settings=cfg, llm=_fake_llm, to_sha="P")
    assert res["result"] == "drafted"
    assert res["feature_count"] == 1


async def test_no_release_worthy_when_raw_all_noise(store):
    gh = FakeGitHub([("s1", "chore: x"), ("s2", "docs(plan): y")])
    res = await generate_draft(trigger="deploy", store=store, github=gh,
                               get_prod_sha=_prod("P"), settings=Cfg(), llm=_fake_llm, to_sha="P")
    assert res["result"] == "no_release_worthy"
    assert res["raw_count"] == 2
    assert res["from_sha"] == "base0" and res["to_sha"] == "P"
    assert any("chore: x" in d for d in res["dropped"])
    assert store.has_pending() is False
