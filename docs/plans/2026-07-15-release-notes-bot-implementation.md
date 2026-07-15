# Release Notes Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone Telegram bot that drafts Russian release-notes from Game Pulse's deployed git commits, foregrounds important product features (minor technical fixes = one ~5% line), lets an admin review/edit in Telegram, and publishes approved posts to `@game_pulse_whiteboard`.

**Architecture:** One async Python process (aiogram long-polling dispatcher + APScheduler in the same event loop). Pure, unit-tested building blocks (`filter`, `models`, `formatter`, `store`, `github`, `prod`, `llm`, `generate`) wired by `bot`, `scheduler`, `main`. State in SQLite. Release boundary is the confirmed prod SHA read from a `/api/v1/version` endpoint on prod. The `last_published_sha` marker advances only on a successful publish, so skipped cycles accumulate. The LLM returns structured JSON; the formatter builds escaped `parse_mode=HTML`.

**Tech Stack:** Python 3.11, aiogram v3, APScheduler, SQLAlchemy Core + SQLite, httpx, pydantic-settings; pytest + pytest-asyncio + respx.

Design reference: `docs/plans/2026-07-15-release-notes-bot-design.md`.

Project root: `/Users/eli/Documents/PythonProjects/gamedev tools/release_bot`. All paths below are relative to it.

---

## Conventions

- TDD: write the failing test, run it red, implement minimal code, run it green, commit.
- Run tests from the project root with the venv: `.venv/bin/python -m pytest`.
- Post text is sent with **`parse_mode="HTML"`**. Only `formatter` emits HTML tags (`<b>` on header lines); every dynamic field is escaped, so malformed LLM HTML is impossible.
- Content priority: product features first; minor technical fixes are compressed by the LLM into a single `fixes_summary` line (~5% of the post).
- Commit after each task.

---

## Phase 1: Scaffold

### Task 1: Project scaffold and dependencies

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `app/__init__.py`, `tests/__init__.py`, `README.md`

**Step 1: Write `pyproject.toml`**

```toml
[project]
name = "release-bot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "aiogram>=3.13",
    "apscheduler>=3.10",
    "sqlalchemy>=2.0",
    "httpx>=0.27",
    "pydantic-settings>=2.4",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]
```

**Step 2: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
data/
.pytest_cache/
```

**Step 3: Write `.env.example`**

```
RELEASE_BOT_TOKEN=
CHANNEL_ID=@game_pulse_whiteboard
ADMIN_CHAT_ID=
GITHUB_TOKEN=
GITHUB_REPO=herocraft/game_pulse_saas
PROD_VERSION_URL=https://tools.herocraft.com/api/v1/version
OPENROUTER_API_KEY=
LLM_MODEL=google/gemini-2.5-flash
SCHEDULE_CRON=0 12 * * FRI
SCHEDULE_TZ=Europe/Moscow
MIN_FEATURES_TO_PUBLISH=1
INITIAL_MARKER_SHA=
DB_PATH=data/release_bot.db
```

**Step 4: Create empty `app/__init__.py`, `tests/__init__.py`, and a short `README.md`.**

**Step 5: Create venv and install** — `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`

**Step 6: Commit** — `git add -A && git commit -m "chore: scaffold release-bot project"`

---

### Task 2: Config

**Files:** Create `app/config.py`, `tests/test_config.py`

**Step 1: Failing test**

```python
from app.config import Settings

def test_settings_load_from_env(monkeypatch):
    for k, v in {
        "RELEASE_BOT_TOKEN": "t", "CHANNEL_ID": "@c", "ADMIN_CHAT_ID": "42",
        "GITHUB_TOKEN": "g", "GITHUB_REPO": "o/r", "OPENROUTER_API_KEY": "k",
        "INITIAL_MARKER_SHA": "deadbeef",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.admin_chat_id == 42
    assert s.min_features_to_publish == 1
    assert s.schedule_cron == "0 12 * * FRI"
    assert s.prod_version_url.endswith("/api/v1/version")
```

**Step 2: Run red.**

**Step 3: Implement `app/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    release_bot_token: str
    channel_id: str
    admin_chat_id: int
    github_token: str
    github_repo: str
    prod_version_url: str = "https://tools.herocraft.com/api/v1/version"
    openrouter_api_key: str
    llm_model: str = "google/gemini-2.5-flash"
    schedule_cron: str = "0 12 * * FRI"
    schedule_tz: str = "Europe/Moscow"
    min_features_to_publish: int = 1
    initial_marker_sha: str
    db_path: str = "data/release_bot.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Step 4: Run green. Step 5: Commit** — `feat: config via pydantic-settings`.

---

## Phase 2: Pure building blocks (TDD)

### Task 3: Conventional-commit filter

**Files:** Create `app/filter.py`, `tests/test_filter.py`

**Step 1: Failing test**

```python
from app.filter import Commit, parse_commit, is_release_worthy, filter_commits


def test_parse_feat_with_scope():
    c = parse_commit("sha1", "feat(topics): add overview card\n\nbody")
    assert c == Commit("sha1", "feat", "topics", "add overview card", False)


def test_parse_breaking():
    assert parse_commit("s", "fix(api)!: drop legacy field").breaking is True


def test_non_conventional_returns_none():
    assert parse_commit("s", "random message") is None


def test_release_worthy_keeps_feat_fix_perf_drops_others():
    assert is_release_worthy(Commit("s", "feat", "topics", "x", False))
    assert is_release_worthy(Commit("s", "fix", "api", "x", False))
    assert is_release_worthy(Commit("s", "perf", "api", "x", False))
    assert not is_release_worthy(Commit("s", "docs", "plan", "x", False))
    assert not is_release_worthy(Commit("s", "chore", None, "x", False))


def test_release_worthy_drops_noise_scopes():
    assert not is_release_worthy(Commit("s", "feat", "research", "x", False))


def test_filter_commits_end_to_end():
    raw = [
        ("s1", "feat(topics): a"), ("s2", "docs(plan): b"),
        ("s3", "fix(alerts): c"), ("s4", "not conventional"),
        ("s5", "feat(research): internal"),
    ]
    assert [c.sha for c in filter_commits(raw)] == ["s1", "s3"]
```

**Step 2: Run red.**

**Step 3: Implement `app/filter.py`**

```python
import re
from dataclasses import dataclass

_CC = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)

RELEASE_TYPES = {"feat", "fix", "perf"}
NOISE_SCOPES = {"research", "litellm", "plan", "plans", "docs"}


@dataclass
class Commit:
    sha: str
    type: str
    scope: str | None
    subject: str
    breaking: bool


def parse_commit(sha: str, message: str) -> Commit | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    m = _CC.match(lines[0].strip())
    if not m:
        return None
    return Commit(sha, m["type"], m["scope"], m["subject"].strip(), bool(m["breaking"]))


def is_release_worthy(c: Commit) -> bool:
    if c.type not in RELEASE_TYPES:
        return False
    if c.scope and c.scope in NOISE_SCOPES:
        return False
    return True


def filter_commits(raw: list[tuple[str, str]]) -> list[Commit]:
    out: list[Commit] = []
    for sha, message in raw:
        c = parse_commit(sha, message)
        if c and is_release_worthy(c):
            out.append(c)
    return out
```

**Step 4: Run green. Step 5: Commit** — `feat: conventional-commit filter`.

---

### Task 4: Post model

**Files:** Create `app/models.py`, `tests/test_models.py`

**Step 1: Failing test**

```python
from app.models import Post


def test_post_defaults():
    p = Post(intro="hi")
    assert p.features == [] and p.improvements == [] and p.fixes_summary is None
```

**Step 2: Run red.**

**Step 3: Implement `app/models.py`**

```python
from dataclasses import dataclass, field


@dataclass
class Post:
    intro: str
    features: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    fixes_summary: str | None = None
```

**Step 4: Run green. Step 5: Commit** — `feat: Post model`.

---

### Task 5: Formatter (HTML render + split)

**Files:** Create `app/formatter.py`, `tests/test_formatter.py`

**Step 1: Failing test**

```python
from app.formatter import render_html, split_message
from app.models import Post


def test_render_escapes_and_bolds():
    p = Post(intro="A & B", features=["<x> fast"], improvements=[], fixes_summary=None)
    out = render_html(p)
    assert "<b>🚀 Game Pulse — что нового</b>" in out
    assert "A &amp; B" in out
    assert "• &lt;x&gt; fast" in out
    assert "<b>✨ Новое</b>" in out
    assert "💬 Пишите, что улучшить" in out


def test_render_omits_empty_groups_and_fixes():
    out = render_html(Post(intro="i", features=["f"]))
    assert "Улучшения" not in out
    assert "🐞" not in out


def test_render_includes_fixes_summary_line():
    out = render_html(Post(intro="i", features=["f"], fixes_summary="мелкие правки"))
    assert "🐞 мелкие правки" in out


def test_split_on_line_boundaries():
    text = "\n".join(f"line{i}" for i in range(100))
    chunks = split_message(text, limit=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)


def test_short_message_single_chunk():
    assert split_message("hello", limit=100) == ["hello"]
```

**Step 2: Run red.**

**Step 3: Implement `app/formatter.py`**

```python
import html

from app.models import Post

TG_LIMIT = 4096
SPLIT_TARGET = 3800


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def render_html(post: Post) -> str:
    parts = ["<b>🚀 Game Pulse — что нового</b>", "", _esc(post.intro)]
    if post.features:
        parts += ["", "<b>✨ Новое</b>", *[f"• {_esc(x)}" for x in post.features]]
    if post.improvements:
        parts += ["", "<b>⚡ Улучшения</b>", *[f"• {_esc(x)}" for x in post.improvements]]
    if post.fixes_summary:
        parts += ["", f"🐞 {_esc(post.fixes_summary)}"]
    parts += ["", "💬 Пишите, что улучшить"]
    return "\n".join(parts)


def split_message(text: str, limit: int = SPLIT_TARGET) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
```

Note: `<b>` headers are whole lines, so a line-based split never cuts a tag.

**Step 4: Run green. Step 5: Commit** — `feat: HTML formatter + splitter`.

---

### Task 6: SQLite store with marker invariant

**Files:** Create `app/store.py`, `tests/test_store.py`

**Step 1: Failing test**

```python
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
```

**Step 2: Run red.**

**Step 3: Implement `app/store.py`**

```python
import json
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, MetaData, Table, Text, create_engine, insert, select, update,
)

metadata = MetaData()

publish_state = Table(
    "publish_state", metadata,
    Column("id", Integer, primary_key=True),
    Column("last_published_sha", Text),
    Column("last_published_at", Text),
    Column("updated_at", Text),
)

drafts = Table(
    "drafts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("from_sha", Text),
    Column("to_sha", Text),
    Column("commit_count", Integer),
    Column("feature_count", Integer),
    Column("raw_commits", Text),
    Column("draft_text", Text),
    Column("admin_msg_id", Integer),
    Column("channel_msg_id", Integer),
    Column("created_at", Text),
    Column("updated_at", Text),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str, initial_marker_sha: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            if conn.execute(select(publish_state.c.id).where(publish_state.c.id == 1)).first() is None:
                conn.execute(insert(publish_state).values(
                    id=1, last_published_sha=initial_marker_sha,
                    last_published_at=_now(), updated_at=_now()))

    def get_marker(self) -> str:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_published_sha)
                                .where(publish_state.c.id == 1)).scalar_one()

    def get_last_published_at(self) -> str:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_published_at)
                                .where(publish_state.c.id == 1)).scalar_one()

    def has_pending(self) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(select(drafts.c.id)
                                .where(drafts.c.status == "pending")).first() is not None

    def create_draft(self, *, status, trigger, from_sha, to_sha, commit_count,
                     feature_count, raw_commits, draft_text) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(insert(drafts).values(
                status=status, trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                commit_count=commit_count, feature_count=feature_count,
                raw_commits=json.dumps(raw_commits), draft_text=draft_text,
                created_at=_now(), updated_at=_now()))
            return res.inserted_primary_key[0]

    def get_draft(self, draft_id: int) -> dict | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(drafts).where(drafts.c.id == draft_id)).first()
            return dict(row._mapping) if row else None

    def set_admin_msg(self, draft_id: int, msg_id: int) -> None:
        self._patch(draft_id, admin_msg_id=msg_id)

    def set_draft_text(self, draft_id: int, text: str) -> None:
        self._patch(draft_id, draft_text=text)

    def cancel(self, draft_id: int) -> None:
        self._patch(draft_id, status="cancelled")

    def _patch(self, draft_id: int, **values) -> None:
        values["updated_at"] = _now()
        with self.engine.begin() as conn:
            conn.execute(update(drafts).where(drafts.c.id == draft_id).values(**values))

    def publish(self, draft_id: int, *, to_sha: str, channel_msg_id: int) -> bool:
        with self.engine.begin() as conn:
            res = conn.execute(update(drafts)
                               .where(drafts.c.id == draft_id, drafts.c.status == "pending")
                               .values(status="published", channel_msg_id=channel_msg_id,
                                       updated_at=_now()))
            if res.rowcount == 0:
                return False
            conn.execute(update(publish_state).where(publish_state.c.id == 1).values(
                last_published_sha=to_sha, last_published_at=_now(), updated_at=_now()))
            return True
```

**Step 4: Run green. Step 5: Commit** — `feat: sqlite store with publish marker invariant`.

---

### Task 7: GitHub client

**Files:** Create `app/github.py`, `tests/test_github.py`

**Step 1: Failing test**

```python
import httpx
import respx

from app.github import GitHub

API = "https://api.github.com"


@respx.mock
async def test_commits_in_range():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(
        return_value=httpx.Response(200, json={"commits": [
            {"sha": "s1", "commit": {"message": "feat(x): a"}},
            {"sha": "s2", "commit": {"message": "fix(y): b"}},
        ]}))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") == [("s1", "feat(x): a"), ("s2", "fix(y): b")]


@respx.mock
async def test_commits_in_range_missing_base_returns_none():
    respx.get(f"{API}/repos/o/r/compare/base...head").mock(return_value=httpx.Response(404))
    gh = GitHub("tok", "o/r")
    assert await gh.commits_in_range("base", "head") is None
```

**Step 2: Run red.**

**Step 3: Implement `app/github.py`**

```python
import httpx

_BASE = "https://api.github.com"


class GitHub:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def commits_in_range(self, base: str, head: str) -> list[tuple[str, str]] | None:
        out: list[tuple[str, str]] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(f"{_BASE}/repos/{self.repo}/compare/{base}...{head}",
                                headers=self._headers, params={"per_page": 100, "page": page})
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                commits = r.json().get("commits", [])
                out.extend((cm["sha"], cm["commit"]["message"]) for cm in commits)
                if len(commits) < 100:
                    break
                page += 1
        return out

    async def commits_since(self, head: str, since_iso: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                r = await c.get(f"{_BASE}/repos/{self.repo}/commits", headers=self._headers,
                                params={"sha": head, "since": since_iso, "per_page": 100, "page": page})
                r.raise_for_status()
                commits = r.json()
                out.extend((cm["sha"], cm["commit"]["message"]) for cm in commits)
                if len(commits) < 100:
                    break
                page += 1
        return out
```

**Step 4: Run green. Step 5: Commit** — `feat: github commit-range client`.

---

### Task 8: Prod SHA reader

**Files:** Create `app/prod.py`, `tests/test_prod.py`

**Step 1: Failing test**

```python
import httpx
import respx

from app.prod import fetch_prod_sha

URL = "https://tools.herocraft.com/api/v1/version"


@respx.mock
async def test_fetch_prod_sha_ok():
    respx.get(URL).mock(return_value=httpx.Response(200, json={"sha": "abc"}))
    assert await fetch_prod_sha(URL) == "abc"


@respx.mock
async def test_fetch_prod_sha_error_returns_none():
    respx.get(URL).mock(return_value=httpx.Response(502))
    assert await fetch_prod_sha(URL) is None


@respx.mock
async def test_fetch_prod_sha_network_error_returns_none():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    assert await fetch_prod_sha(URL) is None
```

**Step 2: Run red.**

**Step 3: Implement `app/prod.py`**

```python
import httpx


async def fetch_prod_sha(url: str) -> str | None:
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            r = await c.get(url)
        except httpx.HTTPError:
            return None
    if r.status_code != 200:
        return None
    return r.json().get("sha")
```

**Step 4: Run green. Step 5: Commit** — `feat: prod /version SHA reader`.

---

### Task 9: LLM client and prompt

**Files:** Create `app/llm.py`, `prompts/release_notes_ru.md`, `tests/test_llm.py`

**Step 1: Write `prompts/release_notes_ru.md`**

```
Ты — редактор release notes продукта Game Pulse (аналитика отзывов игроков для
игровых студий). На вход — список коммитов (тип, скоуп, тема). Верни СТРОГО JSON.

Приоритеты:
- Главное — важные продуктовые фичи и заметные улучшения для пользователя.
- Мелкие технические правки (fix, perf, внутреннее) НЕ перечисляй по отдельности.
  Сверни ВСЕ такие правки в одно короткое предложение в поле fixes_summary
  (примерно 5% объёма поста). Если таких правок нет — null.
- Ничего не выдумывай. Непонятный или чисто внутренний коммит опусти.
- Русский, дружелюбно и ясно, без маркетингового шума. Переводи техническое в
  пользу для пользователя.
- Не упоминай внутренние имена модулей и скоупов, SHA, номера задач, слова
  «рефакторинг», «chore», «бэкенд», «фронтенд».

Формат ответа (только JSON, без markdown):
{
  "intro": "1-2 предложения о главном за период",
  "features": ["важная фича как польза, одна строка"],
  "improvements": ["заметное улучшение"],
  "fixes_summary": "короткая фраза про мелкие исправления или null"
}
```

**Step 2: Failing test**

```python
import httpx
import respx

from app.filter import Commit
from app.llm import build_user_message, draft_release_notes, load_prompt
from app.models import Post


def test_load_prompt_nonempty():
    assert "Game Pulse" in load_prompt()


def test_build_user_message_lists_commits_and_hint():
    msg = build_user_message([Commit("s", "feat", "topics", "add card", False)], "короче")
    assert "feat" in msg and "add card" in msg and "короче" in msg


@respx.mock
async def test_draft_release_notes_parses_json_to_post():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '{"intro":"i","features":["f"],"improvements":[],"fixes_summary":null}'}}]}))
    out = await draft_release_notes("key", "model", [Commit("s", "feat", "x", "y", False)])
    assert isinstance(out, Post)
    assert out.intro == "i" and out.features == ["f"] and out.fixes_summary is None


@respx.mock
async def test_draft_release_notes_strips_code_fence():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content":
            '```json\n{"intro":"i","features":[],"improvements":[],"fixes_summary":"x"}\n```'}}]}))
    out = await draft_release_notes("key", "model", [])
    assert out.fixes_summary == "x"
```

**Step 3: Run red.**

**Step 4: Implement `app/llm.py`**

```python
import json
from pathlib import Path

import httpx

from app.filter import Commit
from app.models import Post

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "release_notes_ru.md"
_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_message(commits: list[Commit], hint: str | None) -> str:
    lines = [f"- {c.type}({c.scope or '-'}): {c.subject}" for c in commits]
    body = "Коммиты:\n" + "\n".join(lines)
    if hint:
        body += f"\n\nДополнительно от редактора: {hint}"
    return body


def _parse_post(content: str) -> Post:
    content = content.strip()
    if content.startswith("```"):
        content = content[content.find("{"): content.rfind("}") + 1]
    data = json.loads(content)
    return Post(
        intro=data.get("intro", ""),
        features=list(data.get("features") or []),
        improvements=list(data.get("improvements") or []),
        fixes_summary=data.get("fixes_summary"),
    )


async def draft_release_notes(api_key: str, model: str, commits: list[Commit],
                              hint: str | None = None) -> Post:
    payload = {
        "model": model,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(commits, hint)},
        ],
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        r.raise_for_status()
        return _parse_post(r.json()["choices"][0]["message"]["content"])
```

**Step 5: Run green. Step 6: Commit** — `feat: openrouter llm client + prompt (structured)`.

---

## Phase 3: Orchestration

### Task 10: `generate_draft` + `regenerate_draft`

**Files:** Create `app/generate.py`, `tests/test_generate.py`

**Step 1: Failing test**

```python
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
```

**Step 2: Run red.**

**Step 3: Implement `app/generate.py`**

```python
import json
from dataclasses import asdict

from app.filter import Commit, filter_commits
from app.formatter import render_html


async def generate_draft(*, trigger, store, github, get_prod_sha, settings, llm, hint=None) -> dict:
    from_sha = store.get_marker()
    to_sha = await get_prod_sha()
    if to_sha is None:
        return {"result": "no_prod_sha"}
    if to_sha == from_sha:
        return {"result": "no_changes", "commit_count": 0}

    raw = await github.commits_in_range(from_sha, to_sha)
    if raw is None:
        raw = await github.commits_since(to_sha, store.get_last_published_at())

    commits = filter_commits(raw)
    features = [c for c in commits if c.type == "feat"]
    n, fcount = len(commits), len(features)
    raw_dump = [asdict(c) for c in commits]

    if trigger == "scheduled" and fcount < settings.min_features_to_publish:
        store.create_draft(status="skipped", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                           commit_count=n, feature_count=fcount, raw_commits=raw_dump, draft_text="")
        return {"result": "skipped", "commit_count": n, "feature_count": fcount}

    if n == 0:
        return {"result": "no_changes", "commit_count": 0}

    post = await llm(settings.openrouter_api_key, settings.llm_model, commits, hint)
    text = render_html(post)
    draft_id = store.create_draft(status="pending", trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                                  commit_count=n, feature_count=fcount, raw_commits=raw_dump, draft_text=text)
    return {"result": "drafted", "draft_id": draft_id, "commit_count": n,
            "feature_count": fcount, "text": text}


async def regenerate_draft(*, store, draft_id, settings, llm, hint=None) -> str:
    d = store.get_draft(draft_id)
    commits = [Commit(**c) for c in json.loads(d["raw_commits"])]
    post = await llm(settings.openrouter_api_key, settings.llm_model, commits, hint)
    text = render_html(post)
    store.set_draft_text(draft_id, text)
    return text
```

**Step 4: Run green. Step 5: Commit** — `feat: generate + regenerate orchestration`.

---

### Task 11: Bot handlers (aiogram)

**Files:** Create `app/bot.py`. Wiring layer; verify by smoke (Task 15).

**Step 1: Implement `app/bot.py`**

```python
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from app.formatter import split_message
from app.generate import generate_draft, regenerate_draft
from app.llm import draft_release_notes

log = logging.getLogger(__name__)


class EditState(StatesGroup):
    waiting_for_text = State()


def _review_kb(draft_id: int) -> InlineKeyboardMarkup:
    d = str(draft_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{d}"),
        InlineKeyboardButton(text="🔁 Перегенерировать", callback_data=f"rg:{d}"),
    ], [
        InlineKeyboardButton(text="✏️ Правка", callback_data=f"ed:{d}"),
        InlineKeyboardButton(text="🗑 Отмена", callback_data=f"cx:{d}"),
    ]])


async def send_for_review(bot, store, admin_chat_id, draft_id: int, text: str) -> None:
    chunks = split_message(text)
    for chunk in chunks[:-1]:
        await bot.send_message(admin_chat_id, chunk, parse_mode="HTML")
    msg = await bot.send_message(admin_chat_id, chunks[-1], parse_mode="HTML",
                                 reply_markup=_review_kb(draft_id))
    store.set_admin_msg(draft_id, msg.message_id)


def build_dispatcher(bot: Bot, store, settings) -> Dispatcher:
    dp = Dispatcher()

    def _is_admin(chat_id: int) -> bool:
        return chat_id == settings.admin_chat_id

    @dp.message(Command("release_draft"))
    async def cmd_release_draft(message: Message) -> None:
        if not _is_admin(message.chat.id):
            return
        if store.has_pending():
            await message.answer("Уже есть черновик на ревью. Заверши его сначала.")
            return
        res = await generate_draft(trigger="manual", store=store, github=bot._gh,
                                   get_prod_sha=bot._get_prod_sha, settings=settings,
                                   llm=draft_release_notes)
        if res["result"] == "drafted":
            await send_for_review(bot, store, settings.admin_chat_id, res["draft_id"], res["text"])
        elif res["result"] == "no_prod_sha":
            await message.answer("Не удалось получить prod SHA (/version недоступен).")
        elif res["result"] == "no_changes":
            await message.answer("С прошлой публикации нет задеплоенных изменений.")
        else:
            await message.answer(f"Нет релиз-достойных изменений (найдено {res['commit_count']}).")

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not _is_admin(message.chat.id):
            return
        await message.answer(
            f"Маркер: {store.get_marker()}\n"
            f"Последняя публикация: {store.get_last_published_at()}\n"
            f"Черновик на ревью: {'да' if store.has_pending() else 'нет'}")

    @dp.callback_query(F.data.startswith("pub:"))
    async def on_publish(cb: CallbackQuery) -> None:
        did = int(cb.data.split(":")[1])
        d = store.get_draft(did)
        if not d or d["status"] != "pending":
            await cb.answer("Черновик неактуален.")
            return
        first = None
        for chunk in split_message(d["draft_text"]):
            sent = await bot.send_message(settings.channel_id, chunk, parse_mode="HTML")
            first = first or sent
        ok = store.publish(did, to_sha=d["to_sha"], channel_msg_id=first.message_id)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Опубликовано" if ok else "Уже опубликовано")

    @dp.callback_query(F.data.startswith("rg:"))
    async def on_regenerate(cb: CallbackQuery) -> None:
        did = int(cb.data.split(":")[1])
        await cb.answer("Генерирую заново...")
        text = await regenerate_draft(store=store, draft_id=did, settings=settings,
                                      llm=draft_release_notes)
        await send_for_review(bot, store, settings.admin_chat_id, did, text)

    @dp.callback_query(F.data.startswith("cx:"))
    async def on_cancel(cb: CallbackQuery) -> None:
        store.cancel(int(cb.data.split(":")[1]))
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Отменено")

    @dp.callback_query(F.data.startswith("ed:"))
    async def on_edit(cb: CallbackQuery, state: FSMContext) -> None:
        did = int(cb.data.split(":")[1])
        await state.set_state(EditState.waiting_for_text)
        await state.update_data(draft_id=did)
        await cb.answer()
        await cb.message.answer("Пришли новый текст поста ответным сообщением.")

    @dp.message(EditState.waiting_for_text)
    async def on_edit_text(message: Message, state: FSMContext) -> None:
        did = (await state.get_data())["draft_id"]
        escaped = html.escape(message.text or "", quote=False)
        store.set_draft_text(did, escaped)
        await state.clear()
        await send_for_review(bot, store, settings.admin_chat_id, did, escaped)

    return dp
```

**Step 2: Commit** — `feat: aiogram review handlers`.

---

### Task 12: Scheduler

**Files:** Create `app/scheduler.py`

**Step 1: Implement `app/scheduler.py`**

```python
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot import send_for_review
from app.generate import generate_draft
from app.llm import draft_release_notes

log = logging.getLogger(__name__)


def build_scheduler(bot, store, settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.schedule_tz)

    async def job() -> None:
        if store.has_pending():
            log.info("scheduled run skipped: pending draft exists")
            return
        try:
            res = await generate_draft(trigger="scheduled", store=store, github=bot._gh,
                                       get_prod_sha=bot._get_prod_sha, settings=settings,
                                       llm=draft_release_notes)
        except Exception:
            log.exception("scheduled generate_draft failed")
            await bot.send_message(settings.admin_chat_id, "Ошибка при сборке дайджеста, см. логи.")
            return
        if res["result"] == "drafted":
            await send_for_review(bot, store, settings.admin_chat_id, res["draft_id"], res["text"])
        elif res["result"] == "skipped":
            await bot.send_message(
                settings.admin_chat_id,
                f"Пропущено: {res['feature_count']} фич, накоплено с {store.get_last_published_at()}.")
        # no_changes / no_prod_sha: stay silent

    scheduler.add_job(job, CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.schedule_tz),
                      max_instances=1, misfire_grace_time=3600)
    return scheduler
```

**Step 2: Commit** — `feat: weekly digest scheduler`.

---

### Task 13: main entrypoint

**Files:** Create `app/main.py`

**Step 1: Implement `app/main.py`**

```python
import asyncio
import logging

from aiogram import Bot

from app.bot import build_dispatcher
from app.config import get_settings
from app.github import GitHub
from app.prod import fetch_prod_sha
from app.scheduler import build_scheduler
from app.store import Store

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings = get_settings()
    bot = Bot(settings.release_bot_token)
    bot._gh = GitHub(settings.github_token, settings.github_repo)
    bot._get_prod_sha = lambda: fetch_prod_sha(settings.prod_version_url)
    store = Store(settings.db_path, settings.initial_marker_sha)

    await bot.delete_webhook(drop_pending_updates=False)  # ensure polling, no webhook conflict

    dp = build_dispatcher(bot, store, settings)
    scheduler = build_scheduler(bot, store, settings)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Commit** — `feat: main entrypoint (polling + scheduler)`.

---

### Task 14: End-to-end dry run

**Files:** Create `scripts/dry_run.py`

**Step 1: Implement `scripts/dry_run.py`** — generate a draft from the real repo range and print rendered HTML (no Telegram send):

```python
import asyncio

from app.config import get_settings
from app.generate import generate_draft
from app.github import GitHub
from app.llm import draft_release_notes
from app.prod import fetch_prod_sha
from app.store import Store


async def main() -> None:
    s = get_settings()
    gh = GitHub(s.github_token, s.github_repo)
    store = Store("data/dry_run.db", s.initial_marker_sha)
    res = await generate_draft(trigger="manual", store=store, github=gh,
                               get_prod_sha=lambda: fetch_prod_sha(s.prod_version_url),
                               settings=s, llm=draft_release_notes)
    print(res.get("result"))
    print(res.get("text", ""))


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2:** Run with a real `.env` (needs `GITHUB_TOKEN`, `OPENROUTER_API_KEY`, `INITIAL_MARKER_SHA` = an older real SHA). Until GP `/version` exists, temporarily point `PROD_VERSION_URL` at a stub returning a known SHA, or export a fake by setting `INITIAL_MARKER_SHA` older and pointing `PROD_VERSION_URL` at any `{"sha": "<recent-sha>"}`.

Run: `.venv/bin/python scripts/dry_run.py`
Expected: prints `drafted` and a Russian post led by ✨ features with a single 🐞 line.

**Step 3: Commit** — `chore: dry-run script`.

---

## Phase 4: Packaging and deploy

### Task 15: Docker + compose + live smoke

**Files:** Create `Dockerfile`, `docker-compose.yml`, `scripts/redeploy.sh`

**Step 1: `Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
COPY prompts ./prompts
CMD ["python", "-m", "app.main"]
```

**Step 2: `docker-compose.yml`**

```yaml
services:
  release-bot:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

No published ports (long polling only).

**Step 3: `scripts/redeploy.sh`** (runs on the VPS in `/opt/release_bot`):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/release_bot
git fetch --prune origin main
git reset --hard origin/main
sudo docker compose up --build -d
sudo docker compose ps
```

**Step 4: Live smoke against a PRIVATE test channel.** Point `CHANNEL_ID` at a throwaway channel where the test bot is admin, run `.venv/bin/python -m app.main`, send `/release_draft`, press Опубликовать, confirm the HTML post lands and `/status` shows the advanced marker. Then exercise Перегенерировать and Правка once each.

**Step 5: Commit** — `chore: docker + compose + redeploy`.

---

### Task 16: Add `/version` endpoint to Game Pulse (cross-project)

**Files (in `game_pulse_saas`):**
- Modify: `backend/Dockerfile`, `infra/docker-compose.prod.yml`, `backend/app/main.py`, `scripts/redeploy_prod.sh`

This is the ONLY change to Game Pulse. It exposes the running image's git SHA so the bot's release boundary reflects what is actually live. No caddy change (the route lives under `/api/*`, already proxied — avoids touching `test_caddy_retry_config.py`).

**Step 1:** `backend/Dockerfile` — near the END (after deps install / COPY, so the dependency layer cache is preserved):

```dockerfile
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
```

**Step 2:** `infra/docker-compose.prod.yml` — give the backend build the arg. Change the shared `x-backend` build block from `build: ../backend` to:

```yaml
  build:
    context: ../backend
    args:
      GIT_SHA: ${GIT_SHA:-unknown}
```

**Step 3:** `backend/app/main.py` — beside the existing `healthz` route:

```python
import os

@app.get(f"{runtime_settings.api_v1_prefix}/version", tags=["system"])
async def version() -> dict[str, str]:
    return {"sha": os.getenv("GIT_SHA", "unknown")}
```

**Step 4:** `scripts/redeploy_prod.sh` — export the SHA for the build. Change both `up --build` invocations (the main one at line ~56 and the rollback ones) so compose interpolates `GIT_SHA`. For the success path use `$new_sha`; the rollback path may leave `${GIT_SHA:-unknown}` (the rebuilt prev image simply reports `unknown` or you may set it to `$prev_sha`). Minimal safe form for the main build:

```bash
if ! GIT_SHA="$new_sha" "${compose[@]}" up --build -d --remove-orphans --wait --wait-timeout "$WAIT_TIMEOUT"; then
```

No changes to the rollback control flow are required for correctness: `/version` reports whatever image is actually running.

**Step 5:** Verify locally: `docker build --build-arg GIT_SHA=test -t gp backend && docker run --rm gp python -c "import os;print(os.getenv('GIT_SHA'))"` → prints `test`.

**Step 6: Commit in the game_pulse_saas repo** — `feat(deploy): expose running git SHA at /api/v1/version` — follow that repo's git workflow (push `main`). Game Pulse deploy is a separate, explicitly-approved step; do NOT deploy as part of this task.

---

### Task 17: README + one-time setup

**Files:** Modify `README.md`

Document: create the bot via BotFather; add it as admin of `@game_pulse_whiteboard` with post rights; the bot calls `delete_webhook` on boot; set `INITIAL_MARKER_SHA` to the current prod SHA at first launch; ship the `/version` endpoint to Game Pulse and deploy it once (so `/version` returns a real SHA); issue a GitHub fine-grained PAT with `contents:read` on `game_pulse_saas`; run exactly ONE instance; `docker compose up --build -d`.

**Commit** — `docs: setup and operations README`.

---

## Phase 5: Verification

### Task 18: Full test pass + live smoke

**Step 1:** `.venv/bin/python -m pytest -v` → all green (config, filter, models, formatter, store, github, prod, llm, generate).

**Step 2:** Dry run (Task 14) against the real repo → sane Russian draft: features first, one 🐞 line.

**Step 3:** Live smoke against a private test channel (Task 15 Step 4): `/release_draft` → Опубликовать → HTML post lands → `/status` marker advanced. Regenerate and Edit once each.

**Step 4:** Once GP `/version` is deployed, set `PROD_VERSION_URL` to prod, point `CHANNEL_ID` at `@game_pulse_whiteboard`, confirm the bot is channel admin, and hand off to normal operation (weekly schedule + manual `/release_draft`).

---

## Execution Handoff

After this plan is saved, choose execution:

1. **Subagent-Driven (this session)** — dispatch a fresh subagent per task, review between tasks.
2. **Parallel Session (separate)** — new session with `executing-plans`, batch execution with checkpoints.
