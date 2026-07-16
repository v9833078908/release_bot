# Release Bot

Standalone Telegram bot that drafts Russian release-notes posts from Game
Pulse's deployed git history and publishes approved drafts to
`@game_pulse_whiteboard`. Fully isolated from `game_pulse_saas` (own repo,
own SQLite, own scheduler).

Design: `docs/plans/2026-07-15-release-notes-bot-design.md`
Implementation plan: `docs/plans/2026-07-15-release-notes-bot-implementation.md`

## Dev setup

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in tokens/keys
```

## Test

```
.venv/bin/python -m pytest -v
```

## One-time setup

1. Create the bot via [@BotFather](https://t.me/BotFather); save the token
   into `RELEASE_BOT_TOKEN`.
2. Add the bot as admin of `@game_pulse_whiteboard` with post rights.
3. The bot calls `delete_webhook` on boot, so it is safe to run with long
   polling even if a webhook was ever set previously.
4. Ship the `/api/v1/version` endpoint to `game_pulse_saas` and deploy it once
   (part of this plan, `game_pulse_saas` repo) so `/version` returns a real SHA.
5. Set `INITIAL_MARKER_SHA` in `.env` to the current prod SHA (read it from
   `PROD_VERSION_URL` once step 4 is deployed) so the first digest only
   covers changes from that point forward.
6. Issue a GitHub fine-grained PAT with `contents:read` on `game_pulse_saas`;
   save it into `GITHUB_TOKEN`.
7. Fill in `OPENROUTER_API_KEY`, `ADMIN_CHAT_ID` (your Telegram user/chat id),
   and the rest of `.env` from `.env.example`.

## Running

Run **exactly ONE** instance — a second long-polling consumer on the same
bot token will conflict with `getUpdates`.

```
docker compose up --build -d
```

No inbound ports are published (long polling only). SQLite state lives on the
`./data` volume. Logs: `docker compose logs -f release-bot`.

### Commands

Registered in the Telegram command menu (`setMyCommands`) on boot.

- `/release_draft` — generate a draft now over the deployed range
  (marker..prod), ignoring the `MIN_FEATURES_TO_PUBLISH` gate.
- `/preview` — draft over `marker..origin/main` HEAD: changes that are in
  `main` but not yet on prod. Review-only — Publish stays blocked until those
  changes are actually deployed (a draft is publishable only when its target
  commit equals the current prod SHA).
- `/status` — show the current marker, last publish time, and whether a draft
  is pending review.

Scheduled digests run on `SCHEDULE_CRON`/`SCHEDULE_TZ` automatically.

### Versioning

Published posts carry a sequential release number and a build stamp. At publish
the bot atomically reserves the next number (`store.claim_for_publish`,
`pending -> publishing`) *before* the channel send, injects ` · #N` into the
header, and appends an `<i>сборка <sha8> · DD.MM.YYYY</i>` footer (`sha` = the
prod SHA the draft targets). The number is `MAX(release_no over published) + 1`,
stored per draft, so cancelled or send-failed publishes never burn a number.
Numbering starts from the first published digest (`#1`).

### Prod deploy

On the VPS, checkout this repo at `/opt/release_bot`, populate `.env`, then
`scripts/redeploy.sh` (fetch + hard reset to `origin/main` + rebuild).
