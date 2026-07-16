# Release Notes Bot — Design

Date: 2026-07-15
Status: Approved (design phase). Implementation plan: `2026-07-15-release-notes-bot-implementation.md`.

## 1. Purpose

A standalone bot that turns Game Pulse's shipped changes into human-language
Russian release-notes posts and publishes them to the public Telegram channel
`@game_pulse_whiteboard`. It **primarily announces important product features**;
minor technical fixes are compressed into a single short line (~5% of post
volume). The bot drafts automatically from git history, a human reviews/edits the
draft in Telegram, and only approved posts go to the channel.

The bot lives in its own project at
`/Users/eli/Documents/PythonProjects/gamedev tools/release_bot` (dev) and
`/opt/release_bot` (prod), fully isolated from `game_pulse_saas`.

## 2. Settled decisions

| Dimension | Decision |
|---|---|
| Content source | Hybrid: auto-draft from git commits + manual edit/approval |
| Content priority | Product features first; minor technical fixes = one line, ~5% of volume |
| Review UI | Telegram-native: draft sent to admin DM with inline buttons |
| Trigger | Scheduled digest (configurable cron), plus manual `/release_draft` |
| Infra | Full isolation: own docker-compose, SQLite, own scheduler |
| Git access | GitHub REST API, read-only fine-grained PAT (commit ranges) |
| Bot identity | Dedicated new bot + long polling (`getUpdates`), no webhook |
| Release boundary | Confirmed production SHA via a `/api/v1/version` endpoint on prod |
| Noise filter | conventional-commit type pre-filter, then LLM importance ranking, then human review |
| Post format | LLM returns structured JSON; formatter builds escaped `parse_mode=HTML` |
| Stack | aiogram v3 + APScheduler + SQLite (SQLAlchemy Core) + httpx |

## 3. Architecture and data flow

One Python process in one container. aiogram dispatcher (long polling) and an
`AsyncIOScheduler` share a single asyncio event loop.

```
scheduler (weekly cron) ─┐
/release_draft (manual) ─┴─> generate_draft
  -> prod SHA from GET https://tools.herocraft.com/api/v1/version
  -> GitHub API: commits in range last_published_sha .. prod_sha
  -> filter (conventional-commit type whitelist + scope-noise blacklist)
  -> LLM: structured JSON {intro, features, improvements, fixes_summary}
  -> formatter: escaped HTML post
  -> SQLite draft (status=pending)
  -> DM admin: post text + inline buttons

buttons:
  Publish       -> sendMessage(HTML) to channel -> advance marker -> published
  Regenerate    -> LLM again on same cached commit set (optional hint)
  Edit (reply)  -> admin reply text (escaped) replaces draft_text
  Cancel        -> status=cancelled
```

### Modules (`app/`)

- `config.py` — pydantic-settings, reads `.env`.
- `models.py` — `Post` dataclass (LLM structured output).
- `bot.py` — aiogram Dispatcher: `/release_draft`, `/status`, callback handlers, FSM for reply-edit.
- `scheduler.py` — `AsyncIOScheduler`, cron trigger from config.
- `github.py` — httpx REST client: commit range, pagination.
- `prod.py` — httpx GET of the prod `/version` endpoint -> prod SHA.
- `filter.py` — conventional-commit parsing, type whitelist, scope blacklist.
- `llm.py` — slim OpenRouter client (httpx), prompt from `prompts/release_notes_ru.md`, JSON -> `Post`.
- `formatter.py` — `Post` -> escaped HTML, 4096-char split.
- `store.py` — SQLite (SQLAlchemy Core): `publish_state`, `drafts`.
- `main.py` — boots polling + scheduler in one loop.

## 4. Data, config, skip/accumulate logic

### Config (`.env`)

| Key | Purpose | Example |
|---|---|---|
| `RELEASE_BOT_TOKEN` | dedicated bot token | `123:ABC` |
| `CHANNEL_ID` | target channel | `@game_pulse_whiteboard` |
| `ADMIN_CHAT_ID` | where drafts are reviewed | `12345678` |
| `GITHUB_TOKEN` | fine-grained PAT, `contents:read` | `github_pat_...` |
| `GITHUB_REPO` | `owner/repo` | `herocraft/game_pulse_saas` |
| `PROD_VERSION_URL` | prod SHA endpoint | `https://tools.herocraft.com/api/v1/version` |
| `OPENROUTER_API_KEY` | LLM | `sk-or-...` |
| `LLM_MODEL` | drafter model | `google/gemini-2.5-flash` |
| `SCHEDULE_CRON` | configurable auto-publish schedule | `0 12 * * FRI` |
| `SCHEDULE_TZ` | timezone | `Europe/Moscow` |
| `MIN_FEATURES_TO_PUBLISH` | scheduled runs need >= this many `feat` commits | `1` |
| `INITIAL_MARKER_SHA` | bootstrap start point | `<sha>` |

`SCHEDULE_CRON` is standard cron (APScheduler), so the auto-publish date/time is
configurable without code changes.

### SQLite schema (SQLAlchemy Core)

```
publish_state
  id                 INTEGER PK (single row, id=1)
  last_published_sha TEXT   -- marker; advances ONLY on publish
  last_published_at  TEXT   -- ISO ts
  updated_at         TEXT

drafts
  id             INTEGER PK
  status         TEXT   -- pending | published | cancelled | skipped
  trigger        TEXT   -- scheduled | manual
  from_sha       TEXT   -- range base (= last_published_sha at generation)
  to_sha         TEXT   -- prod_sha at generation
  commit_count   INTEGER
  feature_count  INTEGER
  raw_commits    TEXT   -- JSON of filtered commits (for regenerate w/o refetch)
  draft_text     TEXT   -- current rendered HTML post
  admin_msg_id   INTEGER
  channel_msg_id INTEGER
  created_at     TEXT
  updated_at     TEXT
```

### Release boundary (critical invariant)

The digest range is `last_published_sha .. prod_sha`, NOT `main`/HEAD.
Rationale: every change is pushed to `main` immediately, but prod deploy is a
separate, explicitly-approved step; reading to HEAD would announce undeployed
changes. `prod_sha` is the SHA of the actually-running prod image (Section 5).

`last_published_sha` advances ONLY after a successful `sendMessage` to the
channel. Skipped cycles never advance it, so skipped changes accumulate and the
next published digest covers the full backlog since the last publish.

### Content priority and the 5% rule

- The digest leads with **important product features** (`feat`) and notable
  improvements. These are the bulk of every post.
- **Minor technical fixes** (`fix`, `perf`, minor changes) are NEVER listed
  individually. The LLM folds them into a single short `fixes_summary` line
  (~5% of the post), or omits them entirely.
- The scheduled trigger fires only when there are enough product features
  (`feature_count >= MIN_FEATURES_TO_PUBLISH`). A window with only fixes and no
  features is skipped and accumulates; those fixes ride along as the one-line
  summary of the next feature-driven post. Manual `/release_draft` ignores this
  gate (force).

### `generate_draft(trigger)` logic

1. `from_sha = publish_state.last_published_sha`; `to_sha = prod_sha` (via `/version`).
2. If `to_sha is None` (endpoint unreachable) -> report to admin, exit, marker untouched.
3. If `to_sha == from_sha` -> no deployed changes -> exit.
4. Fetch commits `from_sha..to_sha`; filter by type (`feat`/`fix`/`perf`), drop scope noise.
5. `commits`, `features = [feat only]`.
6. If `trigger == scheduled` and `len(features) < MIN_FEATURES_TO_PUBLISH`:
   write `draft(status=skipped)`, **do not touch marker**, quiet admin note. Exit.
7. If no commits at all -> exit (no changes).
8. LLM -> `Post` -> formatter -> `draft(status=pending)` -> DM admin with buttons.
9. Publish button -> `sendMessage(HTML)` -> `last_published_sha = to_sha`,
   `last_published_at = now`, `draft.status = published`.

## 5. Prod SHA mechanism (`/version` endpoint)

The prod stack exposes the SHA of the running image; the bot reads it over HTTP.
This is correct by construction: the endpoint can only report what is actually
running, so it never certifies an undeployed SHA and needs zero rollback
bookkeeping (a rolled-back stack serves the previous image's SHA).

Changes in `game_pulse_saas` (all small, in safe/testable places; no caddy change
because `/api/*` is already proxied, avoiding the `test_caddy_retry_config.py`
contract):

- `backend/Dockerfile`: `ARG GIT_SHA=unknown` + `ENV GIT_SHA=$GIT_SHA`, placed
  **near the end** so it does not bust the dependency layer cache.
- `infra/docker-compose.prod.yml`: pass `args: {GIT_SHA: ${GIT_SHA:-unknown}}` to
  the backend build.
- `backend/app/main.py`: add `GET {api_v1_prefix}/version -> {"sha": GIT_SHA}`.
- `scripts/redeploy_prod.sh`: run compose with `GIT_SHA="$new_sha"` in the
  environment for the build.

Note: the endpoint discloses the commit SHA of a private repo (low sensitivity).
It can be gated behind an auth header later if desired; default open.

## 6. LLM prompt, post structure, format

### Prompt (`prompts/release_notes_ru.md`)

File-based (per project convention, never inline). Input: filtered commits
(`type`, `scope`, `subject`; no SHA/author/tickets). Output: **strict JSON**.
Rules:

- Russian only, friendly and clear; no marketing fluff.
- Lead with important product features / user-facing improvements.
- Minor technical fixes: never list individually — fold ALL of them into one
  short `fixes_summary` sentence (~5% of the post) or `null` if none.
- Translate technical changes into user value; never invent; drop unclear or
  purely internal commits.
- Forbidden: internal module/scope names, SHAs, tickets, "рефакторинг/chore/бэкенд".

Response JSON shape:

```json
{
  "intro": "1-2 sentences on the main thing this period",
  "features": ["important feature as user benefit, one line"],
  "improvements": ["notable improvement"],
  "fixes_summary": "short line about minor fixes, or null"
}
```

### Rendered post (formatter builds it, only non-empty groups)

```
🚀 Game Pulse — что нового        (bold)
<intro>

✨ Новое                          (bold)
• <feature>

⚡ Улучшения                       (bold)
• <improvement>

🐞 <fixes_summary>                 (one line ~5%)

💬 Пишите, что улучшить
```

The formatter emits the only HTML tags (`<b>` on header lines) and escapes every
dynamic field, so malformed HTML from the LLM is impossible. Sent with
`parse_mode=HTML`. Splitting is line-based; `<b>` headers are whole lines, so a
split never cuts a tag.

### Regenerate / edit

- Regenerate: same commit set from `raw_commits` (no refetch/cost), optional hint
  from admin reply ("shorter", "add X").
- Reply-edit: admin text is escaped and replaces `draft_text` (published as HTML-safe text).

## 7. Conflicts and isolation (summary)

By construction there are no server conflicts:

| Potential conflict | Status | Why |
|---|---|---|
| `@game_pulse_alert_bot` webhook | None | Dedicated bot + long polling; `deleteWebhook` once on the new bot. Run exactly ONE instance (a second `getUpdates` consumer would conflict). |
| Ports (8081/8001/5433/6379) | None | No inbound port (polling). No caddy/ingress changes. |
| Postgres/Redis | None | Nothing shared; own SQLite on a volume. |
| Deploy | Isolated | Own compose + `up`. GP touches are the `/version` plumbing only. |
| Secrets | Via env | Own bot token, read-only GitHub PAT, OpenRouter key. |
| Rate limits | Ample | GitHub 5000/hr, LLM ~1 call/week, Telegram single messages. |

## 8. Edge cases

1. Few/zero features on a scheduled run -> skip, marker held, quiet admin note; next cycle catches up.
2. No deploy since last publish -> `prod_sha == last_published` -> exit.
3. `/version` unreachable -> report to admin + exit; marker untouched.
4. Window has only fixes, no features -> scheduled skip/accumulate; manual `/release_draft` forces a post (mostly a `fixes_summary`).
5. `from_sha` no longer in history (rebase/force-push `main`, rare) -> fallback to `since=last_published_at` by date, log warning.
6. Restart during a `pending` draft -> draft persisted; `callback_data` carries `draft_id`, buttons work after restart; downtime updates delivered from Telegram's queue (~24h) on reconnect.
7. Double trigger/publish -> APScheduler `max_instances=1` + `misfire_grace_time`; `pending->published` transition is transactional; a scheduled run with a live `pending` draft does not create a second one.
8. LLM / channel-send failure -> marker NOT advanced, error to admin, retry via `/release_draft`.
9. Post > 4096 -> split at group/line boundaries.
10. Non-conventional commit -> dropped by default, EXCEPT subjects matching a `FEATURE_PREFIXES` entry (e.g. `VIP Board:`), promoted to `feat` (added 2026-07-16 - real Game Pulse features use these prefixes, not `feat:`). If a deploy range has commits but zero release-worthy, the admin gets a heads-up DM instead of silence.
11. Secret/internal text in a commit -> manual review gate + guardrail prompt + type filter.

## 9. Deployment layout

```
release_bot/
  app/  (config.py models.py bot.py scheduler.py github.py prod.py filter.py llm.py store.py formatter.py main.py)
  prompts/release_notes_ru.md
  tests/
  Dockerfile
  docker-compose.yml      # one service release-bot, volume for SQLite, no ports, restart: unless-stopped
  .env.example
  pyproject.toml
  README.md
  scripts/redeploy.sh
```

Dev: `/Users/eli/Documents/PythonProjects/gamedev tools/release_bot`.
Prod: `/opt/release_bot`, `docker compose up --build -d`. Separate from GP deploy.

### One-time setup

Create the bot via BotFather -> add as admin of `@game_pulse_whiteboard` with post
rights -> the bot calls `delete_webhook` on boot -> set `INITIAL_MARKER_SHA` to the
current prod SHA -> add the `/version` plumbing to `game_pulse_saas` -> issue a
GitHub fine-grained PAT (`contents:read` on the one repo).

## 10. Verification

- Unit: conventional-commit parser + filter; `formatter` (HTML render + escape + split);
  GitHub range; threshold/skip logic; the invariant "marker advances only on publish".
- Smoke: dry-run `generate` over a real repo range -> print draft to console;
  run the bot against a private test channel before pointing at `@game_pulse_whiteboard`.
