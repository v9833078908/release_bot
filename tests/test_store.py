import json

import pytest

from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"), initial_marker_sha="base0")


def test_bootstrap_marker(store):
    assert store.get_marker() == "base0"


def test_create_and_get_draft(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="head1", commit_count=2, feature_count=1,
                             raw_commits=[{"sha": "s"}], draft_text="text")
    d = store.get_draft(did)
    assert d["status"] == "pending" and d["feature_count"] == 1
    assert json.loads(d["raw_commits"]) == [{"sha": "s"}]


def test_publish_advances_marker_only_on_pending(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="head1", commit_count=1, feature_count=1,
                             raw_commits=[], draft_text="t")
    assert store.publish(did, to_sha="head1", channel_msg_id=555) is True
    assert store.get_marker() == "head1"
    assert store.publish(did, to_sha="head2", channel_msg_id=1) is False
    assert store.get_marker() == "head1"


def test_skipped_draft_does_not_advance_marker(store):
    store.create_draft(status="skipped", trigger="scheduled", from_sha="base0",
                       to_sha="head9", commit_count=0, feature_count=0,
                       raw_commits=[], draft_text="")
    assert store.get_marker() == "base0"


def test_has_pending(store):
    assert store.has_pending() is False
    store.create_draft(status="pending", trigger="manual", from_sha="base0",
                       to_sha="h", commit_count=1, feature_count=1,
                       raw_commits=[], draft_text="t")
    assert store.has_pending() is True
