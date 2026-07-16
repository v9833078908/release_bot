# Deploy-Triggered Release Posts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** After each successful Game Pulse prod deploy, the bot auto-generates a release-notes draft and sends it to the admin for approval (no auto-publish); if the admin cancels, the next deploy re-drafts over the accumulated range.

**Architecture:** An APScheduler interval job polls `/api/v1/version` every few minutes. A new `last_seen_prod_sha` cursor in `publish_state` makes the poll idempotent (react once per deploy SHA), independent of the publish marker (which still moves only on real publish, so a cancelled draft's commits are re-included on the next deploy). The existing manual-approval path (`send_for_review` + the checkmark callback) is reused unchanged. The published marker keeps owning the release-note range.

**Tech Stack:** Python 3.11, aiogram 3, APScheduler 3, SQLAlchemy 2.0 Core, SQLite, httpx, pytest (`asyncio_mode=auto`).

---

## Context for the implementer (read first)

You have zero prior context, so here is the mental model:

- The bot lives in its own repo at `/Users/eli/Documents/PythonProjects/gamedev tools/release_bot`, runs on a VPS via Telegram long-polling, and posts Russian release notes about the **Game Pulse** product to a channel.
- `/api/v1/version` returns the git SHA currently **live and healthy** on Game Pulse prod. It advances only after a successful deploy (the deploy script waits for health + version checks). So "prod SHA changed" == "a deploy succeeded". That is our deploy signal.
- Two persistent cursors in the `publish_state` table (single row, `id=1`):
  - `last_published_sha` (the **marker**): start of the commit range for the next post. Advances **only** when a draft is actually published.
  - `last_seen_prod_sha` (**new in this plan**): "which deploy SHA have I already reacted to?". Pure idempotency; never gates the range.
- `generate_draft(...)` (in `app/generate.py`) always builds the range `marker..prod`, returns `{"result": "no_changes"}` when the range is empty/only-noise, and `{"result": "drafted", ...}` otherwise. It persists a `pending` draft row **before** it is sent for review.
- Publishing requires the human checkmark (`on_publish` in `app/bot.py`). `on_publish` refuses to publish unless `draft.to_sha == current prod_sha` (so the footer never names a build that is no longer live).

### Two behaviors you must preserve (do not "fix" them away)

1. **Approval and cancel are NOT symmetric after a second deploy.** If deploy B lands while draft A (targeting SHA_a) is still awaiting review, A can no longer be approved - `on_publish` blocks it because `A.to_sha (SHA_a) != prod (SHA_b)`. The admin must **cancel** A; then the poller builds the combined `marker..SHA_b` draft. This is correct and intentional. Task 2 only makes the *message* accurate; it does not remove the guard.
2. **The marker freezes on cancel, so ranges accumulate.** Because cancel does not publish, the marker stays put, and the next deploy's `marker..prod` range naturally includes the cancelled commits. This is exactly the "collect the cancelled changes too" behavior the user asked for - it needs no special code.

### Idempotency rule the poll implements

React only when `prod != last_seen_prod_sha` AND `not has_pending()`. Advance `last_seen_prod_sha = prod` **only after a durable outcome** for that SHA:
- `drafted` -> only after `send_for_review` succeeded;
- `no_changes` -> immediately (nothing to post for this SHA; do not re-check it every tick).
On any failure (flaky `/version`, LLM error, or review-send failure) leave `last_seen` unchanged so the next tick retries. Because `generate_draft` persists the `pending` draft before sending, a send failure must also roll that draft **out of** `pending` (else `has_pending()` wedges every future tick).

### Verification commands (used throughout)

- Single file: `cd "/Users/eli/Documents/PythonProjects/gamedev tools/release_bot" && .venv/bin/python -m pytest tests/<file>.py -v`
- Full suite: `cd "/Users/eli/Documents/PythonProjects/gamedev tools/release_bot" && .venv/bin/python -m pytest -q`

All commands run from the repo root with the existing `.venv`.

---

## Task 1: `last_seen_prod_sha` column + migration + accessors

**Files:**
- Modify: `app/store.py` (table def ~11-17; `__init__` migration ~47-50; accessors after `get_last_published_at` ~65)
- Test: `tests/test_store.py`

**Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_last_seen_prod_sha_default_none(store):
    assert store.get_last_seen_prod_sha() is None


def test_set_and_get_last_seen_prod_sha(store):
    store.set_last_seen_prod_sha("abc123")
    assert store.get_last_seen_prod_sha() == "abc123"


def test_last_seen_prod_sha_migrated_on_old_db(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE publish_state (id INTEGER PRIMARY KEY, "
        "last_published_sha TEXT, last_published_at TEXT, updated_at TEXT)")
    con.execute("INSERT INTO publish_state (id, last_published_sha) VALUES (1, 'base0')")
    con.commit()
    con.close()
    s = Store(p, initial_marker_sha="ignored")
    assert s.get_last_seen_prod_sha() is None
    s.set_last_seen_prod_sha("deadbeef")
    assert s.get_last_seen_prod_sha() == "deadbeef"
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v -k last_seen`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'get_last_seen_prod_sha'`.

**Step 3: Add the column to the table definition**

In `app/store.py`, add one line to the `publish_state` table (currently lines 11-17) so it reads:

```python
publish_state = Table(
    "publish_state", metadata,
    Column("id", Integer, primary_key=True),
    Column("last_published_sha", Text),
    Column("last_published_at", Text),
    Column("last_seen_prod_sha", Text),
    Column("updated_at", Text),
)
```

**Step 4: Add the boot migration**

In `Store.__init__`, right after the existing `drafts` migration block (the `if "release_no" not in cols:` block ending ~line 50) and before the seed-insert block, add:

```python
        with self.engine.begin() as conn:
            pcols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(publish_state)")}
            if "last_seen_prod_sha" not in pcols:
                conn.exec_driver_sql("ALTER TABLE publish_state ADD COLUMN last_seen_prod_sha TEXT")
```

**Step 5: Add the accessors**

In `app/store.py`, immediately after the `get_last_published_at` method (ends ~line 65), add:

```python
    def get_last_seen_prod_sha(self) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_seen_prod_sha)
                                .where(publish_state.c.id == 1)).scalar_one()

    def set_last_seen_prod_sha(self, sha: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(publish_state)
                         .where(publish_state.c.id == 1)
                         .values(last_seen_prod_sha=sha, updated_at=_now()))
```

(`select` and `update` are already imported at the top of `store.py`.)

**Step 6: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (all existing + 3 new).

**Step 7: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat(store): add last_seen_prod_sha cursor + boot migration"
```

---

## Task 2: Accurate publish-block message (`publish_block_reason`)

The current `on_publish` shows "changes not yet on prod (preview)" whenever `to_sha != prod`. In deploy-driven mode a second deploy during review makes the *stale* case (draft target now behind prod) common, and that message is misleading. Extract a testable helper that reports the real reason, and wire it in.

**Files:**
- Modify: `app/generate.py` (add helper after `is_publishable`, ~line 10)
- Modify: `app/bot.py` (import line ~13; `on_publish` lines 120-125)
- Test: `tests/test_generate.py` (create if absent, else append)

**Step 1: Write the failing tests**

Create/append `tests/test_generate.py`:

```python
from app.generate import publish_block_reason


def test_publish_block_reason_none_when_equal():
    assert publish_block_reason("abc", "abc") is None


def test_publish_block_reason_message_when_different():
    msg = publish_block_reason("aaaaaaaa11", "bbbbbbbb22")
    assert msg is not None
    assert "aaaaaaaa" in msg and "bbbbbbbb" in msg


def test_publish_block_reason_message_when_prod_none():
    assert publish_block_reason("abc", None) is not None
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_generate.py -v`
Expected: FAIL with `ImportError: cannot import name 'publish_block_reason'`.

**Step 3: Add the helper (reuses `is_publishable`, DRY)**

In `app/generate.py`, directly after the `is_publishable` function (ends ~line 10), add:

```python
def publish_block_reason(draft_to_sha: str, prod_sha: str | None) -> str | None:
    """None if the draft is publishable; otherwise a human message saying why not."""
    if prod_sha is None:
        return "Не могу получить текущий прод-SHA, попробуй позже."
    if is_publishable(draft_to_sha, prod_sha):
        return None
    return (f"Нельзя опубликовать: цель черновика {draft_to_sha[:8]} != текущий прод "
            f"{prod_sha[:8]}. Прод ушёл вперёд - отмени черновик, бот соберёт новый "
            f"по полному диапазону.")
```

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_generate.py -v`
Expected: PASS.

**Step 5: Wire it into `on_publish`**

In `app/bot.py`, update the generate import (line ~13) to drop the now-unused `is_publishable` and add the new helper:

```python
from app.generate import generate_draft, regenerate_draft, publish_block_reason
```

Then replace `on_publish` lines 120-125 (the `prod_sha = ...` fetch through the `return`) with:

```python
        prod_sha = await bot._get_prod_sha()
        reason = publish_block_reason(d["to_sha"], prod_sha)
        if reason is not None:
            await cb.answer(reason, show_alert=True)
            return
```

**Step 6: Run the full suite (regression check on bot import + handlers)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no import errors; `is_publishable` still defined in `generate.py` and used by the helper).

**Step 7: Commit**

```bash
git add app/generate.py app/bot.py tests/test_generate.py
git commit -m "feat(publish): accurate stale-vs-preview block message"
```

---

## Task 3: `run_deploy_poll` (one idempotent poll tick)

Module-level async function with injected dependencies (mirrors how `generate_draft` takes its collaborators), so it is fully unit-testable without aiogram. It encodes the idempotency rule and the send-failure rollback.

**Files:**
- Modify: `app/scheduler.py` (add function after imports, before `build_scheduler`)
- Test: `tests/test_deploy_poll.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_deploy_poll.py`:

```python
import pytest

from app.models import Post
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


async def fake_llm(api_key, model, commits, hint):
    return Post(intro="i", features=["f"], improvements=[], fixes_summary=None)


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
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "drafted"
    assert len(sent) == 1
    assert store.get_last_seen_prod_sha() == "A"


async def test_already_seen_does_nothing(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    store.set_last_seen_prod_sha("A")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "already_seen"
    assert sent == []


async def test_pending_blocks_new_draft(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], "A")
    store.create_draft(status="pending", trigger="manual", from_sha="M0", to_sha="Z",
                       commit_count=1, feature_count=1, raw_commits=[], draft_text="t")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "pending_exists"
    assert sent == []


async def test_no_prod_sha_leaves_cursor(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "feat: x")], None)
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "no_prod_sha"
    assert store.get_last_seen_prod_sha() is None


async def test_noise_only_deploy_sets_cursor_no_send(tmp_path):
    store, gh, get_prod, send, sent, _ = make(tmp_path, [("s1", "chore: x")], "A")
    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "no_changes"
    assert sent == []
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
                              settings=Settings(), llm=fake_llm, send_review=flaky_send)
    assert store.get_last_seen_prod_sha() is None   # cursor NOT advanced
    assert store.has_pending() is False             # draft rolled out of pending

    res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=flaky_send)
    assert res == "drafted"
    assert store.get_last_seen_prod_sha() == "A"


async def test_cancel_accumulates_range_on_next_deploy(tmp_path):
    store, ghA, get_prod, send, sent, prod = make(tmp_path, [("s1", "feat: a")], "A")

    res = await run_deploy_poll(store=store, github=ghA, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "drafted"
    assert store.cancel(sent[-1]) is True           # human cancels A; marker frozen at M0

    res = await run_deploy_poll(store=store, github=ghA, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "already_seen"                    # not resurrected on same SHA

    prod["sha"] = "B"
    ghB = FakeGitHub([("s1", "feat: a"), ("s2", "feat: b")])
    res = await run_deploy_poll(store=store, github=ghB, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send)
    assert res == "drafted"
    d = store.get_draft(sent[-1])
    assert d["from_sha"] == "M0" and d["to_sha"] == "B"
    assert d["commit_count"] == 2                    # union: both commits included
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deploy_poll.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_deploy_poll'`.

**Step 3: Implement `run_deploy_poll`**

In `app/scheduler.py`, add this module-level function after the imports and before `def build_scheduler` (line ~13):

```python
async def run_deploy_poll(*, store, github, get_prod_sha, settings, llm, send_review) -> str:
    """One poll tick. Returns a disposition string; raises on generate/send failure
    so the caller logs and retries next tick (last_seen left unchanged)."""
    prod = await get_prod_sha()
    if prod is None:
        return "no_prod_sha"
    if prod == store.get_last_seen_prod_sha():
        return "already_seen"
    if store.has_pending():
        return "pending_exists"
    res = await generate_draft(trigger="deploy", store=store, github=github,
                               get_prod_sha=get_prod_sha, settings=settings, llm=llm,
                               to_sha=prod)
    if res["result"] == "drafted":
        try:
            await send_review(res["draft_id"], res["text"])
        except Exception:
            store.cancel(res["draft_id"])   # roll out of pending so the next poll retries
            raise
    store.set_last_seen_prod_sha(prod)       # durable outcome reached for this SHA
    return res["result"]
```

Notes for the implementer:
- Passing `to_sha=prod` means `generate_draft` uses the SHA we already fetched (one `/version` call, no race).
- `trigger="deploy"` intentionally does **not** hit the `min_features` gate (that gate is `scheduled`-only in `generate.py`), so any release-worthy commit produces a draft for the human to judge.
- `store.cancel` only cancels a `pending` draft and returns a bool; the just-created draft is `pending`, so it succeeds.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_deploy_poll.py -v`
Expected: PASS (all 7).

**Step 5: Commit**

```bash
git add app/scheduler.py tests/test_deploy_poll.py
git commit -m "feat(scheduler): run_deploy_poll idempotent tick + send-failure rollback"
```

---

## Task 4: Register the interval job + config knob

**Files:**
- Modify: `app/config.py` (add setting after `db_path`, ~line 21)
- Modify: `app/scheduler.py` (import + register interval job in `build_scheduler`)
- Test: `tests/test_deploy_poll.py` (append a registration test)

**Step 1: Write the failing test**

Append to `tests/test_deploy_poll.py`:

```python
def test_build_scheduler_registers_cron_and_interval_jobs():
    from app.scheduler import build_scheduler

    class S:
        schedule_cron = "0 12 * * FRI"
        schedule_tz = "Europe/Moscow"
        deploy_poll_seconds = 180
        admin_chat_id = 1

    sched = build_scheduler(bot=object(), store=object(), settings=S())
    assert len(sched.get_jobs()) == 2
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_deploy_poll.py -v -k build_scheduler`
Expected: FAIL - `assert 1 == 2` (only the cron job is registered today).

**Step 3: Add the config setting**

In `app/config.py`, add after `db_path` (line ~21):

```python
    deploy_poll_seconds: int = 180
```

**Step 4: Add the interval import**

In `app/scheduler.py`, add below the existing `CronTrigger` import (line ~4):

```python
from apscheduler.triggers.interval import IntervalTrigger
```

**Step 5: Register the interval job**

In `build_scheduler`, after the existing `scheduler.add_job(job, CronTrigger...)` call (lines ~36-37), add:

```python
    async def deploy_job() -> None:
        async def _send(did, text):
            await send_for_review(bot, store, settings.admin_chat_id, did, text)
        try:
            await run_deploy_poll(store=store, github=bot._gh, get_prod_sha=bot._get_prod_sha,
                                  settings=settings, llm=draft_release_notes, send_review=_send)
        except Exception:
            log.exception("deploy_poll tick failed")

    scheduler.add_job(deploy_job, IntervalTrigger(seconds=settings.deploy_poll_seconds),
                      max_instances=1, coalesce=True)
```

The weekly cron job stays as-is (a backstop that catches a deploy the poll may have missed). It does not touch `last_seen_prod_sha`; the two coexist through `has_pending()`.

**Step 6: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_deploy_poll.py -v`
Expected: PASS.

**Step 7: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tasks green).

**Step 8: Commit**

```bash
git add app/config.py app/scheduler.py tests/test_deploy_poll.py
git commit -m "feat(scheduler): poll /version on an interval and auto-draft on deploy"
```

---

## Task 5: Document the behavior in the README

**Files:**
- Modify: `README.md` (add a subsection under the existing command/versioning docs)

**Step 1: Add the section**

Insert after the `### Versioning` section (before `### Prod deploy`):

```markdown
### Deploy-triggered drafts

An interval job polls `/api/v1/version` every `DEPLOY_POLL_SECONDS` (default 180s).
When prod's SHA advances (a successful deploy), the bot drafts release notes over
`marker..prod` and sends them to the admin for approval - it never auto-publishes.

- `last_seen_prod_sha` (in `publish_state`) is the idempotency cursor: the poll
  reacts once per deploy SHA. It advances only after a durable outcome (draft
  delivered, or nothing to post). Flaky `/version`, LLM errors, and review-send
  failures leave it unchanged so the next tick retries.
- The publish **marker** advances only on real publish. So if you **cancel** a
  draft, its commits are re-included in the next deploy's draft (the range keeps
  growing until you approve one).
- After a second deploy, an older pending draft can no longer be approved (its
  target build is no longer live). Cancel it; the poll rebuilds the combined
  range. This guard keeps the build footer honest.
- The weekly `SCHEDULE_CRON` job remains as a backstop.
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document deploy-triggered drafts + last_seen cursor"
```

---

## Rollout (GATED - requires explicit deploy approval; do NOT run without it)

Per project rules, deploying to prod needs a fresh go-ahead. When approved:

**R1. Push all commits**

```bash
git push origin main
```

**R2. Redeploy on the VPS** (fetch + reset origin/main + rebuild; the boot migration auto-adds `last_seen_prod_sha`):

```bash
scripts/ship.sh --no-push   # commits are already pushed in R1
```

Confirm the log reaches `OK: polling` and `deployed <sha>`.

**R3. Seed `last_seen_prod_sha` on prod so the first tick is a clean no-op**

Rationale: on a fresh column `last_seen_prod_sha` is NULL, so the first poll would react to the current prod SHA. Seeding it to the current `/version` SHA makes the bot wait for the *next* deploy. This is safe here only because the current `marker..prod` range holds no release-worthy commits (verify below). If that range DID hold release-worthy commits, skip the seed and let the first poll draft them.

Verify the range is empty of release-worthy commits, then seed (run against the prod container DB, mirroring how prior ops were done):

```
# inside the release-bot container on the VPS
python -c "import asyncio, sqlite3; from app.config import get_settings; \
from app.prod import fetch_prod_sha; \
s=get_settings(); \
print('prod', asyncio.run(fetch_prod_sha(s.prod_version_url))); \
con=sqlite3.connect(s.db_path); \
print('marker', con.execute('select last_published_sha from publish_state').fetchone()); \
print('last_seen', con.execute('select last_seen_prod_sha from publish_state').fetchone())"
```

If the marker..prod range is confirmed non-release-worthy (only `build(deploy)`/docs/chore), seed:

```
python -c "import asyncio, sqlite3; from app.config import get_settings; \
from app.prod import fetch_prod_sha; \
s=get_settings(); sha=asyncio.run(fetch_prod_sha(s.prod_version_url)); \
con=sqlite3.connect(s.db_path); \
con.execute('update publish_state set last_seen_prod_sha=? where id=1',(sha,)); \
con.commit(); print('seeded last_seen =', sha)"
```

**R4. Verify end to end**

- `docker compose logs -f release-bot` shows the interval job running without errors.
- On the next real Game Pulse deploy, a draft appears in the admin chat within `DEPLOY_POLL_SECONDS`. Approving posts it as the next `#N`; cancelling folds its range into the following deploy's draft.

---

## Task summary

| Task | Scope | Files | Prod impact |
|------|-------|-------|-------------|
| 1 | `last_seen_prod_sha` cursor + migration | `store.py`, `test_store.py` | migration auto-runs at boot |
| 2 | accurate stale/preview publish message | `generate.py`, `bot.py`, `test_generate.py` | none until deploy |
| 3 | `run_deploy_poll` tick logic | `scheduler.py`, `test_deploy_poll.py` | none until deploy |
| 4 | interval job + config knob | `config.py`, `scheduler.py`, `test_deploy_poll.py` | poll starts at boot |
| 5 | README | `README.md` | docs only |
| Rollout | push + redeploy + seed cursor | - | GATED on approval |

DRY (helper reuses `is_publishable`; range logic reused from `generate_draft`), YAGNI (no auto-publish, no undo window, no per-SHA log - one cursor), TDD (test-first each task), frequent commits (one per task).
