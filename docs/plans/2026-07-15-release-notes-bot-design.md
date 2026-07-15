# Release Notes Bot — Design

Date: 2026-07-15
Status: Approved (design phase). Implementation plan follows via `writing-plans`.

## 1. Purpose

A standalone bot that turns Game Pulse's shipped changes into human-language
Russian release-notes posts and publishes them to the public Telegram channel
`@game_pulse_whiteboard`. It drafts automatically from git history, a human
reviews/edits the draft in Telegram, and only approved posts go to the channel.

The bot lives in its own project at
`/Users/eli/Documents/PythonProjects/gamedev tools/release_bot` (dev) and
`/opt/release_bot` (prod), fully isolated from `game_pulse_saas`.

## 2. Settled decisions

| Dimension | Decision |
|---|---|
| Content source | Hybrid: auto-draft from git commits + manual edit/approval |
| Review UI | Telegram-native: draft sent to admin DM with inline buttons |
| Trigger | Scheduled digest (configurable cron), plus manual `/release_draft` |
| Infra | Full isolation: own docker-compose, SQLite, own scheduler |
| Git access | GitHub REST API, read-only fine-grained PAT |
| Bot identity | Dedicated new bot + long polling (`getUpdates`), no webhook |
| Release boundary | Confirmed production SHA via a moving `prod` git tag |
| Noise filter | conventional-commit type (`feat`/`fix`/`perf`) + LLM + human review |
| Stack | aiogram v3 + APScheduler + SQLite (SQLAlchemy Core) + httpx |

## 3. Architecture and data flow

One Python process in one container. aiogram dispatcher (long polling) and an
`AsyncIOScheduler` share a single asyncio event loop.

```
scheduler (weekly cron) ─┐
/release_draft (manual) ─┴─> generate_draft
  -> GitHub API: commits in range last_published_sha .. prod_deployed_sha
  -> filter (conventional-commit type whitelist + scope-noise blacklist)
  -> LLM: group + humanize into Russian post (prompt file)
  -> SQLite draft (status=pending)
  -> DM admin: post text + inline buttons

buttons:
  Publish       -> sendMessage to channel -> advance marker -> status=published
  Regenerate    -> LLM again on same cached commit set (optional hint)
  Edit (reply)  -> admin reply text fully replaces draft_text
  Cancel        -> status=cancelled
```

### Modules (`app/`)

- `config.py` — pydantic-settings, reads `.env`.
- `bot.py` — aiogram Dispatcher: `/release_draft`, `/status`, callback handlers, FSM for reply-edit.
- `scheduler.py` — `AsyncIOScheduler`, cron trigger from config.
- `github.py` — httpx REST client: commit range, `prod` tag SHA, pagination.
- `filter.py` — conventional-commit parsing, type whitelist, scope blacklist.
- `llm.py` — slim OpenRouter client (httpx), prompt from `prompts/release_notes_ru.md`.
- `store.py` — SQLite (SQLAlchemy Core): `publish_state`, `drafts`.
- `formatter.py` — HTML post assembly, 4096-char split, escaping.
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
| `PROD_REF` | ref that marks prod boundary | `prod` |
| `OPENROUTER_API_KEY` | LLM | `sk-or-...` |
| `LLM_MODEL` | drafter model | `google/gemini-2.5-flash` |
| `SCHEDULE_CRON` | configurable auto-publish schedule | `0 12 * * FRI` |
| `SCHEDULE_TZ` | timezone | `Europe/Moscow` |
| `MIN_UPDATES_TO_PUBLISH` | threshold; below it a scheduled run skips | `3` |
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
  status         TEXT   -- pending | approved | published | cancelled | skipped
  trigger        TEXT   -- scheduled | manual
  from_sha       TEXT   -- range base (= last_published_sha at generation)
  to_sha         TEXT   -- prod_deployed_sha at generation
  commit_count   INTEGER
  raw_commits    TEXT   -- JSON of filtered commits (for regenerate w/o refetch)
  draft_text     TEXT   -- current post text (edited by reply/regenerate)
  admin_msg_id   INTEGER -- review message id
  channel_msg_id INTEGER -- published post id (nullable)
  created_at     TEXT
  updated_at     TEXT
```

### Release boundary (critical invariant)

The digest range is `last_published_sha .. prod_deployed_sha`, NOT `main`/HEAD.
Rationale: every change is pushed to `main` immediately, but prod deploy is a
separate, explicitly-approved step; reading to HEAD would announce undeployed
changes. `prod_deployed_sha` is read from the moving `prod` git tag via the
GitHub API.

`last_published_sha` advances ONLY after a successful `sendMessage` to the
channel. Skipped cycles never advance it, so skipped changes accumulate and the
next digest covers the full backlog since the last publish.

### `generate_draft(trigger)` logic

1. `from_sha = publish_state.last_published_sha`; `to_sha = prod tag SHA` (GitHub API).
2. Fetch commits `from_sha..to_sha`; filter by type (`feat`/`fix`/`perf`), drop scope noise.
3. `n = len(release_worthy)`.
4. If `trigger == scheduled` and `n < MIN_UPDATES_TO_PUBLISH`:
   write `draft(status=skipped, commit_count=n)`, **do not touch marker**, quiet
   admin note ("skipped: N updates, accumulating since {last_published_at}"). Exit.
5. Else LLM -> draft -> `draft(status=pending)` -> DM admin with buttons.
6. `/release_draft` (manual) ignores the threshold (force).
7. Publish button -> `sendMessage` -> `last_published_sha = to_sha`,
   `last_published_at = now`, `draft.status = published`.

## 5. Prod SHA mechanism (moving `prod` tag)

`redeploy_prod.sh` in `game_pulse_saas` tags the deployed SHA on success and
re-tags on rollback:

- On successful smoke test: `git tag -f prod $new_sha && git push -f origin prod`.
- On rollback paths: tag `prod` to `prev_sha`.

The bot reads it via the GitHub API: `GET /repos/{owner}/{repo}/commits/prod`.
This is the only touch to `game_pulse_saas` (2-3 lines in deploy tooling, not app
code), reuses the chosen GitHub API access, adds no endpoint, and is
rollback-safe.

## 6. LLM prompt, post structure, format

### Prompt (`prompts/release_notes_ru.md`)

File-based (per project convention, never inline). Input: filtered commits
(`type`, `scope`, `subject`; no SHA/author/tickets). System-prompt rules:

- Russian only, friendly and clear; no marketing fluff.
- Translate technical changes into user value.
- Forbidden: internal module/scope names, SHAs, tickets, words like
  "рефакторинг/chore/бэкенд".
- Never invent — only what commits state; drop unclear/internal commits
  (e.g. `feat(research)`).
- Group and de-duplicate related commits into one bullet.

### Post structure (only non-empty groups)

```
🚀 Game Pulse — что нового
<1-2 sentence human intro about the main thing this period>

✨ Новое
• <user benefit, one line>

⚡ Улучшения
• ...

🐞 Исправления
• ...

💬 Пишите, что улучшить: <CTA / link>
```

Type mapping: `feat` -> ✨/⚡, `fix` -> 🐞, `perf` -> ⚡.

### Format / length

- `parse_mode=HTML`; all dynamic text escaped.
- Telegram 4096-char limit: `formatter.py` splits at group/line boundaries into
  1-3 messages (first carries the header).

### Regenerate / edit

- Regenerate: same commit set from `raw_commits` (no refetch/cost), optional hint
  from admin reply ("shorter", "add X").
- Reply-edit: admin text fully replaces `draft_text` (published verbatim).

## 7. Conflicts and isolation (summary)

By construction there are no server conflicts:

| Potential conflict | Status | Why |
|---|---|---|
| `@game_pulse_alert_bot` webhook | None | Dedicated bot + long polling; `deleteWebhook` once on the new bot. Run exactly ONE instance (a second `getUpdates` consumer would conflict). |
| Ports (8081/8001/5433/6379) | None | No inbound port (polling). No caddy/ingress changes. |
| Postgres/Redis | None | Nothing shared; own SQLite on a volume. GP migrations/restarts do not affect the bot. |
| Deploy | Isolated | Own compose + `up`. Only GP touch is the `prod` tag line. |
| Secrets | Via env | Own bot token, read-only GitHub PAT (`contents:read`), OpenRouter key. Separate `.env`. |
| Rate limits | Ample | GitHub 5000/hr, LLM ~1 call/week, Telegram single messages. |

## 8. Edge cases

1. Few/zero updates -> skip, marker held, quiet admin note; next cycle catches up.
2. No deploy since last publish -> `prod == last_published` -> 0 commits -> skip.
3. `prod` tag not yet created -> warn admin + skip; bootstrap via `INITIAL_MARKER_SHA`.
4. `from_sha` no longer in history (rebase/force-push `main`, rare) -> fallback to
   `since=last_published_at` by date, log warning.
5. Restart during a `pending` draft -> draft persisted in SQLite; `callback_data`
   carries `draft_id`, buttons still work after restart; updates pressed during
   downtime are delivered from Telegram's queue (~24h) on reconnect.
6. Double trigger/publish -> APScheduler `max_instances=1` + `misfire_grace_time`;
   `pending->published` transition is transactional; a scheduled run with a live
   `pending` draft does not create a second one.
7. LLM / channel-send failure -> marker NOT advanced, error to admin, retry via
   `/release_draft`.
8. Post > 4096 -> split at group/line boundaries.
9. Non-conventional commit -> dropped by default (not user-facing); add manually
   via reply-edit if needed.
10. Secret/internal text in a commit -> manual review gate + guardrail prompt +
    type filter.

## 9. Deployment layout

```
release_bot/
  app/  (config.py bot.py scheduler.py github.py filter.py llm.py store.py formatter.py main.py)
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
rights -> `deleteWebhook` on the new bot -> set `INITIAL_MARKER_SHA` -> add the
`prod` tag lines to `game_pulse_saas/scripts/redeploy_prod.sh` -> issue a GitHub
fine-grained PAT (`contents:read` on the one repo).

## 10. Verification

- Unit: conventional-commit parser + filter; `formatter` (split/escape); GitHub
  range; threshold/skip logic; the invariant "marker advances only on publish".
- Smoke: dry-run `generate` over a real repo range -> print draft to console;
  run the bot against a private test channel before pointing at
  `@game_pulse_whiteboard`.
