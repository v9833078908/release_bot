import pytest

from app.models import Post, Theme
from app.store import Store
from app.scheduler import run_deploy_poll


class FakeGitHub:
    def __init__(self, commits):
        self._commits = commits  # list[(sha, message)]

    async def commits_in_range(self, base, head):
        return list(self._commits)

    async def commits_since(self, head, since_iso):
        return list(self._commits)


class Settings:  # duck-typed; only fields generate_draft reads
    openrouter_api_key = "k"
    llm_model = "m"
    min_features_to_publish = 1
    feature_prefix_list = []


async def fake_llm(api_key, model, commits, hint):
    return Post(themes=[Theme(title="f", body="b")], fixes=[])


async def _noop(text):
    pass


def make(tmp_path, commits, sha):
    store = Store(str(tmp_path / "t.db"), initial_marker_sha="M0")
    gh = FakeGitHub(commits)
    prod = {"sha": sha}
    sent = []

    async def get_prod():
        return prod["sha"]

    async def send(did, text):
        sent.append(did)

    return store, gh, get_prod, send, sent, prod


async def test_new_deploy_drafts_and_sets_last_seen(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "drafted"
    assert len(sent) == 1
    assert store.get_last_seen_prod_sha() == "A"


async def test_already_seen_does_nothing(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    store.set_last_seen_prod_sha("A")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "already_seen"
    assert sent == []


async def test_pending_for_current_prod_blocks(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    store.create_draft(status="pending", trigger="manual", from_sha="M0", to_sha="A",
                       commit_count=1, feature_count=1, raw_commits=[], draft_text="t")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "pending_exists"
    assert sent == []


async def test_stale_pending_is_superseded(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    stale = store.create_draft(status="pending", trigger="manual", from_sha="M0", to_sha="Z",
                               commit_count=1, feature_count=1, raw_commits=[], draft_text="t")
    disabled = []

    async def disable_review(msg_id):
        disabled.append(msg_id)

    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send,
                                notify=_noop, disable_review=disable_review)
    assert res == "drafted"
    assert store.get_draft(stale)["status"] == "cancelled"   # stale draft superseded, not blocking
    assert sent and sent[-1] != stale                        # fresh draft sent for review
    assert store.get_last_seen_prod_sha() == "A"
    assert len(disabled) == 1                                # stale review buttons stripped


async def test_no_prod_sha_leaves_cursor(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], None)
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "no_prod_sha"
    assert store.get_last_seen_prod_sha() is None


async def test_noise_only_deploy_notifies_and_sets_cursor(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "chore: x")], "A")
    notes = []

    async def notify(text):
        notes.append(text)

    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=notify)
    assert res == "no_release_worthy"
    assert sent == []
    assert len(notes) == 1
    assert store.get_last_seen_prod_sha() == "A"


async def test_send_failure_rolls_back_and_retries(tmp_path):
    store, gh, get_prod, _, _, _ = make(tmp_path, [("s1", "feat: x")], "A")
    calls = {"n": 0}

    async def flaky_send(did, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("telegram down")

    with pytest.raises(RuntimeError):
        await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                              settings=Settings(), llm=fake_llm, send_review=flaky_send, notify=_noop)
    assert store.get_last_seen_prod_sha() is None   # cursor NOT advanced
    assert store.has_pending() is False             # draft rolled out of pending

    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=flaky_send, notify=_noop)
    assert res == "drafted"
    assert store.get_last_seen_prod_sha() == "A"


async def test_cancel_accumulates_range_on_next_deploy(tmp_path):
    store, ghA, get_prod, send, sent, prod = make(tmp_path, [("s1", "feat: a")], "A")

    res = await run_deploy_poll(store=store, github=ghA, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "drafted"
    assert store.cancel(sent[-1]) is True           # human cancels A; marker frozen at M0

    res = await run_deploy_poll(store=store, github=ghA, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "already_seen"                    # not resurrected on same SHA

    prod["sha"] = "B"
    ghB = FakeGitHub([("s1", "feat: a"), ("s2", "feat: b")])
    res = await run_deploy_poll(store=store, github=ghB, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=_noop)
    assert res == "drafted"
    d = store.get_draft(sent[-1])
    assert d["from_sha"] == "M0" and d["to_sha"] == "B"
    assert d["commit_count"] == 2                    # union: both commits included


def test_build_scheduler_registers_only_the_deploy_poll():
    from apscheduler.triggers.interval import IntervalTrigger
    from app.scheduler import build_scheduler

    class S:
        schedule_tz = "Europe/Moscow"
        deploy_poll_seconds = 180

    sched = build_scheduler(bot=object(), store=object(), settings=S())
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    assert isinstance(jobs[0].trigger, IntervalTrigger)


async def test_notify_failure_leaves_cursor(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "chore: x")], "A")

    async def boom(text):
        raise RuntimeError("tg down")

    with pytest.raises(RuntimeError):
        await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                              settings=Settings(), llm=fake_llm, send_review=send, notify=boom)
    assert store.get_last_seen_prod_sha() is None


async def test_no_user_facing_deploy_notifies_and_sets_cursor(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: internal digest")], "A")
    notes = []

    async def notify(text):
        notes.append(text)

    async def empty_llm(api_key, model, commits, hint):
        return Post()

    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=empty_llm, send_review=send, notify=notify)
    assert res == "no_user_facing"
    assert sent == []                                 # no header-only shell sent for review
    assert len(notes) == 1                            # admin told the deploy had nothing user-facing
    assert store.get_last_seen_prod_sha() == "A"      # durable outcome; poll won't re-fire
