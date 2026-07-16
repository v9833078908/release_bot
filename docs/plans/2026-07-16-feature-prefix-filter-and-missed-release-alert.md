# Feature-Prefix Filter + Missed-Release Alert — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the two problems the first live deploy exposed:

1. **Filter/reality mismatch (silent miss).** Game Pulse writes user-facing feature
   commits as `VIP Board: ...`, not conventional `feat(...): ...`. `filter.py`'s
   conventional-commit filter drops them, so real features never draft. Fix: a
   **configurable feature-prefix allowlist** that promotes matching non-conventional
   subjects to `feat` — without letting generic non-conventional noise through.
2. **Silent all-filtered deploy.** When a deploy's `marker..prod` range has commits
   but **zero** pass the filter, `run_deploy_poll` advances `last_seen` and sends
   nothing — no signal to the admin. Fix: a **safety-net admin DM** ("N commits
   deployed, 0 release-worthy — review?") whenever a deploy range is non-empty but
   filters to zero.

Both are independently valuable and shippable in order (Fix 1 = Tasks 1-3, Fix 2 =
Tasks 4-5). Fix 1 also enables recovery of the two already-missed VIP Board features
via a manual `/release_draft` after rollout (range still covers them; `marker`
unmoved).

**Non-goals:** auto-publish, retroactive rewrite of git history, changing the
`marker`-advances-only-on-publish invariant, touching the LLM prompt, multi-tenant
config. The safety-net DM goes to the **admin only** (never the public channel), so
the design-doc "secret/internal text -> manual review gate" rule is preserved.

**Tech Stack:** Python 3.11, aiogram v3, APScheduler 3 (IntervalTrigger), SQLAlchemy
Core + SQLite, httpx, pydantic-settings; pytest (`asyncio_mode=auto`) + respx.

---

## Context for the implementer

Current behavior (verified against live prod on 2026-07-16):

- `app/filter.py` — `_CC` regex requires `type(scope)!: subject` with
  `type ∈ {feat, fix, perf}`; `parse_commit` returns `None` on anything else;
  `filter_commits(raw)` keeps only release-worthy parsed commits. `VIP Board: x`
  fails `_CC` (space before `:`) -> dropped.
- `app/generate.py::generate_draft` — collapses **both** the empty-range case
  (`to_sha == from_sha`, line 39-40) and the filtered-to-zero case (`n == 0`,
  line 56-57) into `{"result": "no_changes", "commit_count": 0}`.
- `app/scheduler.py::run_deploy_poll` — on `drafted` calls `send_review` (rolls back
  the draft + re-raises on send failure so `last_seen` is untouched and the next tick
  retries); on every non-`drafted` result it just advances `last_seen`. `deploy_job`
  wraps it and logs `deploy_poll tick failed` on exception.
- `app/bot.py` — `cmd_release_draft` (manual) and `cmd_preview` switch on the result
  string; both currently map `no_changes` to a text reply. There are **no unit tests
  for bot handlers** (verify manually).
- `app/config.py::Settings` — pydantic-settings; env-driven; `feature_prefix_list`
  does not exist yet.
- Tests: `tests/test_filter.py`, `tests/test_generate.py` (duck-typed `Cfg`),
  `tests/test_deploy_poll.py` (duck-typed `Settings` + `make()` helper returning
  `(store, gh, get_prod, send, sent, prod)`), `tests/test_config.py`.

Ground-truth range that exposed the bug: `marker 3388b82c .. prod fe41c999`, 9 raw
commits, 0 release-worthy; the two dropped user features are
`VIP Board: state-driven devtodev activation/import panel` and
`VIP Board: connection-loading gate + freshness info/action popover`. The 7 others
are genuine noise (`build(deploy)`, `chore(deploy)`, `docs(deploy)`, and one
non-conventional ops-script commit `Add prod DB retention prune script` — which MUST
stay dropped; that is why a blanket "promote all non-conventional" is wrong and an
explicit allowlist is required).

---

## Task 1: `feature_prefixes` config + `feature_prefix_list`

**Files:** `app/config.py`, `tests/test_config.py`

**Step 1 (test-first)** — add to `test_settings_load_from_env`:
```python
assert s.feature_prefix_list == ["VIP Board"]          # default
```
and a new test:
```python
def test_feature_prefixes_parsed_from_env(monkeypatch):
    for k, v in {  # minimal required env
        "RELEASE_BOT_TOKEN": "t", "CHANNEL_ID": "@c", "ADMIN_CHAT_ID": "42",
        "GITHUB_TOKEN": "g", "GITHUB_REPO": "o/r", "OPENROUTER_API_KEY": "k",
        "INITIAL_MARKER_SHA": "deadbeef", "FEATURE_PREFIXES": "VIP Board, Live Ops",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.feature_prefix_list == ["VIP Board", "Live Ops"]
```

**Step 2 — implement** in `Settings`:
```python
feature_prefixes: str = "VIP Board"   # comma-separated; non-conventional subjects
                                      # starting "<prefix>:" are promoted to feat

@property
def feature_prefix_list(self) -> list[str]:
    return [p.strip() for p in self.feature_prefixes.split(",") if p.strip()]
```
Default is `"VIP Board"` because this bot is single-purpose for Game Pulse — it works
on redeploy with no prod-env edit, and is still overridable via `FEATURE_PREFIXES`.

**Acceptance:** `pytest tests/test_config.py` green.
**Commit:** `feat(config): feature_prefixes allowlist for non-conventional feature commits`

---

## Task 2: prefix promotion in `filter.py`

**Files:** `app/filter.py`, `tests/test_filter.py`

**Step 1 (test-first)** — add:
```python
def test_feature_prefix_promoted_to_feat():
    c = parse_commit("s", "VIP Board: connection-loading gate", feature_prefixes=("VIP Board",))
    assert c == Commit("s", "feat", "VIP Board", "connection-loading gate", False)

def test_feature_prefix_case_insensitive():
    assert parse_commit("s", "vip board: x", feature_prefixes=("VIP Board",)).type == "feat"

def test_feature_prefix_requires_colon():
    assert parse_commit("s", "VIP Board without colon", feature_prefixes=("VIP Board",)) is None

def test_feature_prefix_empty_subject_dropped():
    assert parse_commit("s", "VIP Board:   ", feature_prefixes=("VIP Board",)) is None

def test_non_prefixed_non_conventional_still_dropped():
    assert parse_commit("s", "Add prod DB retention prune script", feature_prefixes=("VIP Board",)) is None

def test_conventional_takes_precedence_over_prefix():
    c = parse_commit("s", "feat(topics): a", feature_prefixes=("feat",))
    assert c == Commit("s", "feat", "topics", "a", False)   # regex path, not prefix path

def test_filter_commits_promotes_prefix_and_keeps_dropping_noise():
    raw = [("s1", "VIP Board: gate"), ("s2", "chore: x"), ("s3", "Add script")]
    assert [c.sha for c in filter_commits(raw, feature_prefixes=("VIP Board",))] == ["s1"]
```
(Existing `test_filter.py` tests call `filter_commits(raw)` / `parse_commit(...)` with
no prefixes -> default `()` -> unchanged; do not edit them.)

**Step 2 — implement** (new keyword param, default `()` = today's behavior):
```python
def parse_commit(sha: str, message: str, feature_prefixes: tuple[str, ...] = ()) -> Commit | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    m = _CC.match(first)
    if m:
        return Commit(sha, m["type"], m["scope"], m["subject"].strip(), bool(m["breaking"]))
    for prefix in feature_prefixes:
        p = prefix.strip()
        if p and first[: len(p) + 1].lower() == (p + ":").lower():
            subject = first[len(p) + 1:].strip()
            if subject:
                return Commit(sha, "feat", p, subject, False)
    return None


def filter_commits(raw, feature_prefixes: tuple[str, ...] = ()) -> list[Commit]:
    out: list[Commit] = []
    for sha, message in raw:
        c = parse_commit(sha, message, feature_prefixes)
        if c and is_release_worthy(c):
            out.append(c)
    return out
```
Notes: conventional parse wins (prefix path only runs when `_CC` fails). Synthetic
`feat` with `scope=<prefix>` passes `is_release_worthy` (`feat ∈ RELEASE_TYPES`, and a
multi-word prefix never collides with the lowercase single-word `NOISE_SCOPES`).

**Acceptance:** `pytest tests/test_filter.py` green.
**Commit:** `feat(filter): promote allowlisted non-conventional prefixes to feat`

---

## Task 3: thread prefixes through `generate_draft`

**Files:** `app/generate.py`, `tests/test_generate.py`, `tests/test_deploy_poll.py`

**Cross-test ripple (do in THIS task):** `generate_draft` will start reading
`settings.feature_prefix_list`. Every duck-typed settings object that reaches
`generate_draft` must define it, or those suites `AttributeError`:
- `tests/test_generate.py::Cfg` -> add `feature_prefix_list = []`
- `tests/test_deploy_poll.py::Settings` -> add `feature_prefix_list = []`

**Step 1 (test-first)** — in `test_generate.py`:
```python
async def test_feature_prefix_drafts_via_generate(store):
    gh = FakeGitHub([("s1", "VIP Board: connection gate")])
    cfg = Cfg(); cfg.feature_prefix_list = ["VIP Board"]
    res = await generate_draft(trigger="deploy", store=store, github=gh,
                               get_prod_sha=_prod("P"), settings=cfg, llm=_fake_llm, to_sha="P")
    assert res["result"] == "drafted"
    assert res["feature_count"] == 1
```

**Step 2 — implement** the single call site in `generate_draft`:
```python
commits = filter_commits(raw, tuple(settings.feature_prefix_list))
```
(`regenerate_draft` reads cached `raw_commits` from the DB and never calls
`filter_commits` — unaffected. `dry_run.py` and any other `filter_commits(raw)` caller
keep the default `()` — unaffected.)

**Acceptance:** `pytest tests/test_generate.py tests/test_deploy_poll.py` green.
**Commit:** `feat(generate): apply feature-prefix allowlist to deploy/manual drafts`

> **Fix 1 complete after Task 3.** After rollout, `/release_draft` will draft the two
> missed VIP Board features (range `3388b82c..prod` still covers them), and future
> deploys auto-draft prefixed features.

---

## Task 4: distinguish `no_release_worthy` + safety-net `notify`

**Files:** `app/generate.py`, `app/scheduler.py`, `tests/test_generate.py`,
`tests/test_deploy_poll.py`

This task changes behavior that `test_deploy_poll.py::test_noise_only_deploy_sets_cursor_no_send`
currently asserts (silent). Update that test in the same commit so the suite stays green.

**Step 1 (test-first).**

`test_generate.py` — new result for filtered-to-zero-with-raw:
```python
async def test_no_release_worthy_when_raw_all_noise(store):
    gh = FakeGitHub([("s1", "chore: x"), ("s2", "docs(plan): y")])
    res = await generate_draft(trigger="deploy", store=store, github=gh,
                               get_prod_sha=_prod("P"), settings=Cfg(), llm=_fake_llm, to_sha="P")
    assert res["result"] == "no_release_worthy"
    assert res["raw_count"] == 2
    assert res["from_sha"] == "base0" and res["to_sha"] == "P"
    assert any("chore: x" in d for d in res["dropped"])
    assert store.has_pending() is False          # no draft created
```
(`test_no_changes_when_prod_equals_marker` stays — empty range is still `no_changes`.)

`test_deploy_poll.py`:
- Extend `make()` to also build a notify capture and return it:
  ```python
  notes = []
  async def notify(text):
      notes.append(text)
  return store, gh, get_prod, send, sent, prod, notify, notes
  ```
  Update **all** existing unpackings to the 8-tuple (mechanical; use `*_` for the
  trailing two where unused). Pass `notify=notify` to every `run_deploy_poll(...)` call
  (see Task 5's signature).
- Rewrite the noise-only test:
  ```python
  async def test_noise_only_deploy_notifies_and_sets_cursor(tmp_path):
      store, gh, get_prod, send, sent, _, notify, notes = make(tmp_path, [("s1", "chore: x")], "A")
      res = await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                  settings=Settings(), llm=fake_llm, send_review=send, notify=notify)
      assert res == "no_release_worthy"
      assert sent == []                       # no draft/review
      assert len(notes) == 1                  # admin heads-up sent
      assert store.get_last_seen_prod_sha() == "A"
  ```
- Add notify-failure semantics (must NOT advance cursor — re-notify next tick):
  ```python
  async def test_notify_failure_leaves_cursor(tmp_path):
      store, gh, get_prod, send, sent, _, _, _ = make(tmp_path, [("s1", "chore: x")], "A")
      async def boom(text): raise RuntimeError("tg down")
      with pytest.raises(RuntimeError):
          await run_deploy_poll(store=store, github=gh, get_prod_sha=get_prod,
                                settings=Settings(), llm=fake_llm, send_review=send, notify=boom)
      assert store.get_last_seen_prod_sha() is None
  ```

**Step 2 — implement `generate.py`** (replace the `if n == 0:` branch, keep the
`scheduled`+`min_features` skip branch above it unchanged):
```python
if n == 0:
    if raw:
        dropped = [((m.splitlines() or [""])[0]).strip()[:100] for _, m in raw][:15]
        return {"result": "no_release_worthy", "commit_count": 0, "raw_count": len(raw),
                "dropped": dropped, "from_sha": from_sha, "to_sha": to_sha}
    return {"result": "no_changes", "commit_count": 0}
```

**Step 3 — implement `scheduler.py`** (`run_deploy_poll` gains required `notify`;
`deploy_job` wires a plain-text admin sender):
```python
async def run_deploy_poll(*, store, github, get_prod_sha, settings, llm, send_review, notify) -> str:
    prod = await get_prod_sha()
    if prod is None:
        return "no_prod_sha"
    if prod == store.get_last_seen_prod_sha():
        return "already_seen"
    if store.has_pending():
        return "pending_exists"
    res = await generate_draft(trigger="deploy", store=store, github=github,
                               get_prod_sha=get_prod_sha, settings=settings, llm=llm, to_sha=prod)
    if res["result"] == "drafted":
        try:
            await send_review(res["draft_id"], res["text"])
        except Exception:
            store.cancel(res["draft_id"])
            raise
    elif res["result"] == "no_release_worthy":
        await notify(_format_missed(res))     # raises -> last_seen NOT advanced -> retry next tick
    store.set_last_seen_prod_sha(prod)
    return res["result"]


def _format_missed(res) -> str:
    head = (f"\u26a0\ufe0f Задеплоено {res['raw_count']} коммит(ов) "
            f"({res['from_sha'][:8]}..{res['to_sha'][:8]}), релиз-достойных — 0.\n"
            "Возможно, есть пользовательские изменения без conventional-commit префикса "
            "(или нужен новый FEATURE_PREFIXES). Проверь: /release_draft.\n\nКоммиты:")
    return head + "\n" + "\n".join(f"\u2022 {s}" for s in res["dropped"])
```
`deploy_job`:
```python
async def _notify(text):
    await bot.send_message(settings.admin_chat_id, text)   # plain text, admin DM only
...
await run_deploy_poll(..., send_review=_send, notify=_notify)
```

**Acceptance:** `pytest tests/test_generate.py tests/test_deploy_poll.py` green.
**Commit:** `feat(scheduler): alert admin when a deploy range filters to zero release-worthy`

---

## Task 5: manual/preview messaging for `no_release_worthy`

**Files:** `app/bot.py` (no bot unit tests exist — verify by reading + manual smoke)

In `cmd_release_draft`, replace the current `no_changes` handling with two cases:
```python
elif res["result"] == "no_changes":
    await message.answer("С прошлой публикации нет задеплоенных изменений.")
elif res["result"] == "no_release_worthy":
    await message.answer(
        f"Задеплоено {res['raw_count']} коммит(ов), но релиз-достойных нет. "
        "Нужен conventional-commit префикс (feat/fix/perf) или запись в FEATURE_PREFIXES.")
```
Mirror the `no_release_worthy` branch in `cmd_preview` (use `res['raw_count']`; keep its
existing `no_changes` -> "main не опережает маркер" text). The dead `else`
`commit_count` branch can stay or be folded — do not expand scope.

**Acceptance:** handlers reference `res['raw_count']` only under `no_release_worthy`;
manual read confirms no `KeyError` on any result path.
**Commit:** `feat(bot): accurate manual/preview reply when range filters to zero`

---

## Task 6: docs + env template

**Files:** `README.md`, `docs/plans/2026-07-15-release-notes-bot-design.md`,
`.env.example`

- `README.md`: document `FEATURE_PREFIXES` (what it promotes, default `VIP Board`) and
  the new safety-net admin DM on all-filtered deploys.
- Design doc line ~258 ("Non-conventional commit -> dropped by default"): amend to note
  the allowlist promotion + the missed-release admin alert (the original
  conventional-only assumption did not hold for Game Pulse's real commit style).
- `.env.example`: add `FEATURE_PREFIXES=VIP Board` with a one-line comment.

**Commit:** `docs: feature-prefix allowlist + missed-release alert`

---

## Rollout (GATED — do NOT run without explicit deploy approval)

1. `bash scripts/ship.sh --no-push` (push already done per Git workflow) -> VPS
   `redeploy.sh` rebuilds; `.env`/`./data` preserved. Default `FEATURE_PREFIXES=VIP Board`
   is baked in, so no prod-env edit is required (set it in `/opt/release_bot/.env` only
   to change it).
2. **Recover the two missed features** (admin action, in the bot chat): send
   `/release_draft`. Range `3388b82c..fe41c999` is unchanged (marker unmoved), the two
   `VIP Board:` commits now promote to `feat`, so it drafts -> review -> publish. This
   is the human-approved publish path (no auto-publish).
3. Verify Fix 2 is wired: `deploy_job` ticks stay clean in logs; the safety-net path is
   covered by unit tests (an all-noise real deploy would DM the admin instead of going
   silent).

| Task | Adds | Files | Live effect |
|------|------|-------|-------------|
| 1 | `feature_prefixes` config | `config.py`, `test_config.py` | none until deploy |
| 2 | prefix -> feat promotion | `filter.py`, `test_filter.py` | none until deploy |
| 3 | thread prefixes into draft | `generate.py`, `test_generate.py`, `test_deploy_poll.py` | none until deploy |
| 4 | `no_release_worthy` + notify | `generate.py`, `scheduler.py`, tests | admin DM on all-filtered deploy |
| 5 | manual/preview messaging | `bot.py` | accurate `/release_draft` reply |
| 6 | docs + env template | `README.md`, design doc, `.env.example` | docs only |
| Rollout | push + redeploy + recover | - | GATED on approval |

Principles: DRY (one allowlist, one `filter_commits` call site; `_format_missed`
centralizes the alert), YAGNI (explicit prefix allowlist, not a fuzzy heuristic; no
auto-publish), TDD (test-first each task), frequent commits (one per task), clean
cutover (new `notify` is required — no silent no-op default, mirroring the very
silent-miss bug being fixed).
