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
