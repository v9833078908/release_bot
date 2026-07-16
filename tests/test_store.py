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


def test_claim_reserves_number_then_publish(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="h1", commit_count=1, feature_count=1,
                             raw_commits=[], draft_text="t")
    assert store.next_release_no() == 1
    assert store.claim_for_publish(did) == 1
    d = store.get_draft(did)
    assert d["status"] == "publishing" and d["release_no"] == 1
    assert store.claim_for_publish(did) is None  # already claimed -> no double send
    assert store.publish(did, to_sha="h1", channel_msg_id=7) is True
    assert store.get_draft(did)["release_no"] == 1
    assert store.next_release_no() == 2


def test_unclaim_restores_pending(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="h1", commit_count=1, feature_count=1,
                             raw_commits=[], draft_text="t")
    store.claim_for_publish(did)
    store.unclaim(did)
    assert store.get_draft(did)["status"] == "pending"
    assert store.claim_for_publish(did) == 1


def test_release_no_migrated_on_existing_db(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.execute('CREATE TABLE drafts ('
                'id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, "trigger" TEXT NOT NULL, '
                'from_sha TEXT, to_sha TEXT, commit_count INTEGER, feature_count INTEGER, '
                'raw_commits TEXT, draft_text TEXT, admin_msg_id INTEGER, channel_msg_id INTEGER, '
                'created_at TEXT, updated_at TEXT)')
    con.commit(); con.close()
    s = Store(p, initial_marker_sha="base0")  # migration must ALTER-add release_no
    did = s.create_draft(status="pending", trigger="manual", from_sha="base0", to_sha="h",
                         commit_count=1, feature_count=1, raw_commits=[], draft_text="t")
    assert s.claim_for_publish(did) == 1


def test_cancel_refused_after_claim(store):
    did = store.create_draft(status="pending", trigger="manual", from_sha="base0",
                             to_sha="h1", commit_count=1, feature_count=1,
                             raw_commits=[], draft_text="t")
    assert store.claim_for_publish(did) == 1
    assert store.cancel(did) is False  # cannot cancel a claimed (publishing) draft
    assert store.get_draft(did)["status"] == "publishing"
    assert store.publish(did, to_sha="h1", channel_msg_id=9) is True
    assert store.get_marker() == "h1"
