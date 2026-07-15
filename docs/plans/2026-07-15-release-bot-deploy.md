# Release Bot - Deployment Plan (self-contained handoff)

Date: 2026-07-15
Repo: `git@github.com:v9833078908/release_bot.git` (branch `main`)
Prod target: `/opt/release_bot` on the same VPS as Game Pulse (`dev01@<VPS_HOST>`).

> This document is written for a fresh agent with no prior context. It captures
> everything needed to deploy `release_bot` from scratch, cleanly and correctly.
> Architecture detail lives in `2026-07-15-release-notes-bot-design.md` and
> `2026-07-15-release-notes-bot-implementation.md` (same folder); read those for
> the "why". This doc is the "how to deploy".

---

## 0. What this bot is (grounding)

`release_bot` is a standalone Telegram bot that drafts Russian release-notes
from Game Pulse's git history and publishes admin-approved posts to the public
channel `@game_pulse_whiteboard`. It is a **separate project** from
`game_pulse_saas` (own repo, own SQLite, own scheduler, own container).

Flow: on a weekly cron (or manual `/release_draft`), it reads the commit range
`last_published_sha .. prod_sha`, filters to conventional-commit
`feat`/`fix`/`perf` (dropping doc/chore/noise scopes), asks an LLM (OpenRouter)
for a structured Russian post (features first, minor fixes folded into one ~5%
line), renders escaped `parse_mode=HTML`, DMs it to the admin with inline
buttons (Publish / Regenerate / Edit / Cancel), and only on **Publish** sends it
to the channel and advances the marker. `prod_sha` is read from Game Pulse's
`GET /api/v1/version` so the bot never announces undeployed changes.

Stack: Python 3.11, aiogram v3 (long polling), APScheduler, SQLAlchemy Core +
SQLite, httpx, pydantic-settings. One process, one container, no inbound ports.

### Current state (verified this session)

- Code complete and on `origin/main`. Full unit suite: **34 passing**.
- `docker build` succeeds; container imports and finds the prompt file.
- Live end-to-end verified: real GitHub token + real OpenRouter key produced a
  correct Russian draft from a real commit range; LLM prompt is English, output
  Russian; the flaky-JSON retry and graceful handler-error paths work.
- Telegram verified: bot `@gamepulse_poster_bot` token valid; admin (`ADMIN_CHAT_ID`)
  has pressed Start (review DM delivered); bot **is** admin of the channel.
- Bot process verified to boot cleanly (delete_webhook -> polling -> scheduler).

### Not done yet (intentional / dependencies)

- **Not deployed to the VPS** (this plan does that).
- Game Pulse `GET /api/v1/version` is **not yet deployed to prod** (returns 404).
  The endpoint's code is merged to Game Pulse `main` but a prod deploy is a
  separate, explicitly-approved step. Until it is live, scheduled/manual digests
  report "prod SHA unavailable". This bot's deploy does **not** require it; it
  just can't produce automatic digests until `/version` is up.

---

## 1. Goal and isolation guarantees

Run `release_bot` as an always-on, self-healing service on the VPS, fully
isolated from Game Pulse:

- Own GitHub repo, own checkout at `/opt/release_bot`.
- Own `docker compose` project, one service (`release-bot`), **no published
  ports** (long polling), own SQLite volume (`./data`), `restart: unless-stopped`.
- No shared Postgres / Redis / caddy. The Game Pulse stack is never touched.
- Only runtime couplings are outbound HTTPS: GitHub API, OpenRouter, Telegram
  Bot API, and Game Pulse's public `/api/v1/version`.

Deploy mechanism mirrors Game Pulse (`sshpass` + `ssh` -> repo-local
`redeploy.sh` that does `git reset --hard` + `docker compose up --build`), minus
migrations, HTTP healthcheck, and ingress.

---

## 2. Prerequisites

Already true (verified):
- Code on `origin/main` (`v9833078908/release_bot`).
- Bot created (`@gamepulse_poster_bot`), admin of `@game_pulse_whiteboard`, and
  the admin chat has started the bot.
- Local `release_bot/.env` holds all runtime secrets (see §4 for the key list).
- VPS credentials are in `game_pulse_saas/.env.local` (`VPS_HOST`, `VPS_USER=dev01`,
  `VPS_PORT`, `VPS_PASSWORD`) - same VPS. Copy these into `release_bot/.env.local`
  (template `.env.local.example`; gitignored, never commit).

Needed once, on the VPS:
- Nothing GitHub-side for cloning: `release_bot` is a **public** repo, so the
  VPS clones read-only over HTTPS (§5.1) - no key, no GitHub UI step. (Only if
  it is later made **private** do you need a dedicated deploy key; never reuse
  the single-repo Game Pulse key. See the §5.1 fallback.)
- `sshpass` on the operator laptop (`brew install sshpass`).
- Docker + docker compose on the VPS (present; `dev01` has NOPASSWD sudo for
  docker, per the Game Pulse setup).

---

## 3. Repo artifacts (already committed)

- `scripts/ship.sh` - laptop trigger: validate clean tree, `git push origin
  main`, then `sshpass ssh` into the VPS and run `redeploy.sh`. `--no-push`
  deploys current `origin/main` without pushing.
- `scripts/redeploy.sh [ref]` - runs on the VPS in `/opt/release_bot`. No arg:
  `git fetch` + `reset --hard origin/main`. With a `<sha|ref>` arg: `reset
  --hard` to that pinned commit **without** fetching (rollback; §9). Then
  `docker compose up --build -d --remove-orphans`, waits for the `Run polling`
  log (fails on `getUpdates 409` or no-polling within ~40s), `ps`, prune.
  Idempotent.
- `docker-compose.yml` - one service `release-bot`, `build: .`, `env_file: .env`,
  `./data:/app/data` volume, `restart: unless-stopped`, no ports, json-file log
  rotation (10m x3).
- `Dockerfile` - `python:3.11-slim`, installs the package, copies `app/` +
  `prompts/`, `CMD ["python","-m","app.main"]`.
- `.env.local.example` - VPS deploy-cred template for `ship.sh`.

---

## 4. Runtime secrets: `/opt/release_bot/.env`

The container reads `.env` (gitignored, not in the clone). It must contain the
same values as the local `release_bot/.env`. Keys (from `app/config.py`):

| Key | Meaning |
|---|---|
| `RELEASE_BOT_TOKEN` | BotFather token for `@gamepulse_poster_bot` |
| `CHANNEL_ID` | `@game_pulse_whiteboard` |
| `ADMIN_CHAT_ID` | admin Telegram chat id (draft review DM) |
| `GITHUB_TOKEN` | fine-grained PAT, `contents:read` on `v9833078908/game_pulse` |
| `GITHUB_REPO` | `v9833078908/game_pulse` |
| `PROD_VERSION_URL` | `https://tools.herocraft.com/api/v1/version` |
| `OPENROUTER_API_KEY` | LLM key |
| `LLM_MODEL` | `google/gemini-2.5-flash` |
| `SCHEDULE_CRON` | `0 12 * * FRI` |
| `SCHEDULE_TZ` | `Europe/Moscow` |
| `MIN_FEATURES_TO_PUBLISH` | `1` |
| `INITIAL_MARKER_SHA` | bootstrap marker; see §7 (marker handoff) |
| `DB_PATH` | `data/release_bot.db` |

Provision it by copying the local file:
`scp -P <VPS_PORT> .env dev01@<VPS_HOST>:/opt/release_bot/.env`, then
`chmod 600 /opt/release_bot/.env` on the VPS.

---

## 5. One-time VPS bootstrap

### 5.1 Create the repo dir and clone (HTTPS - public repo)

`v9833078908/release_bot` is a **public** repo, so the VPS clones it read-only
over HTTPS with no deploy key and no GitHub-UI step. `redeploy.sh` only ever
runs `git fetch origin` / `git reset --hard`, which need no auth against a
public origin.

On the VPS, as `dev01`:

```bash
sudo mkdir -p /opt/release_bot && sudo chown dev01:dev01 /opt/release_bot
git clone https://github.com/v9833078908/release_bot.git /opt/release_bot
```

The clone's `origin` is the HTTPS URL, so `redeploy.sh`'s `git fetch origin` /
`git reset --hard origin/main` work unauthenticated. The laptop keeps its own
`git@github.com:...` origin and pushes with the operator's account key.

#### Fallback: dedicated deploy key (ONLY if the repo is made private)

If `release_bot` is ever switched to private, HTTPS fetch begins failing and the
VPS origin must move to SSH with a **dedicated** deploy key (GitHub deploy keys
are single-repo, so the Game Pulse key cannot be reused). On the VPS:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/release_bot_deploy -N "" -C "release_bot-deploy@$(hostname)"
cat ~/.ssh/release_bot_deploy.pub   # add read-only under repo Settings -> Deploy keys
```

Append to `~/.ssh/config` (`IdentitiesOnly yes` stops SSH offering the Game
Pulse key first, which would auth as the wrong repo):

```
Host github-release-bot
    HostName github.com
    User git
    IdentityFile ~/.ssh/release_bot_deploy
    IdentitiesOnly yes
```

Then point origin at the alias and verify:

```bash
git -C /opt/release_bot remote set-url origin git@github-release-bot:v9833078908/release_bot.git
ssh -T git@github-release-bot   # "Hi v9833078908/release_bot! ..."
```

### 5.2 Provision `.env` and first bring-up

```bash
# from the laptop:
scp -P <VPS_PORT> .env dev01@<VPS_HOST>:/opt/release_bot/.env
# on the VPS:
chmod 600 /opt/release_bot/.env
cd /opt/release_bot && bash scripts/redeploy.sh
```

`redeploy.sh` confirms the container reaches `Run polling`.

---

## 6. Ongoing deploy

From the laptop, in `release_bot/` (needs `release_bot/.env.local` with `VPS_*`):

```bash
scripts/ship.sh             # push main, then redeploy on VPS
scripts/ship.sh --no-push   # redeploy current origin/main only
```

---

## 7. Operational notes (must-read)

- **Single instance.** Long polling = exactly one `getUpdates` consumer. Never
  run a local/dev instance and the VPS instance on the same token at once
  (Telegram `409 Conflict`). `redeploy.sh` detects and fails on this.
- **SQLite marker handoff.** A fresh VPS `./data` bootstraps the marker from
  `INITIAL_MARKER_SHA` (used only when the DB row is absent). To avoid
  re-announcing an already-published post:
  - set `INITIAL_MARKER_SHA` on the VPS to the **last published SHA** (the
    `main` HEAD at publish time), **or**
  - `scp` the local `data/release_bot.db` to `/opt/release_bot/data/` to inherit
    the exact marker + draft history.
  `git reset --hard` never touches the untracked `./data`, so the DB survives
  every redeploy.
- **Prod `/version` dependency.** Automatic and manual digests need Game Pulse's
  `GET /api/v1/version`. Until it is deployed to prod it returns 404 and the bot
  replies "prod SHA unavailable". Deploying that endpoint is a separate,
  explicitly-approved Game Pulse deploy (code already on Game Pulse `main`).
- **Secrets.** `/opt/release_bot/.env` holds the bot token, GitHub PAT, and
  OpenRouter key. `chmod 600`; never commit; rotate by re-`scp`.
- **Backups.** The only durable state is `/opt/release_bot/data/release_bot.db`.
  Back it up if publish history matters.
- **Logs.** `sudo docker compose -f /opt/release_bot/docker-compose.yml logs
  --tail 100 release-bot`. Rotation is configured in the compose file.

---

## 8. Verification

- `redeploy.sh` fails the deploy unless the container logs `Run polling` within
  ~40s, and fails immediately on `Conflict: terminated by other getUpdates`.
- `sudo docker compose -f /opt/release_bot/docker-compose.yml ps` shows the
  service `running`.
- In Telegram, from the admin: `/status` returns marker + last-publish + pending
  state. Once Game Pulse `/version` is live, `/release_draft` produces a draft;
  Publish posts to `@game_pulse_whiteboard` and advances the marker.

---

## 9. Rollback

`redeploy.sh` takes an optional ref/SHA. With no arg it deploys the latest
`origin/main`; with a ref it `reset --hard`s to exactly that commit **without**
fetching, so a rollback sticks:

```bash
cd /opt/release_bot
bash scripts/redeploy.sh <prev_sha>   # the prev_sha from the "$prev_sha -> $new_sha" line
```

(The earlier two-step `git reset --hard <sha>` then arg-less `redeploy.sh` did
**not** roll back: the arg-less path re-fetches and resets to `origin/main`,
immediately discarding the checkout.) No schema/state migration to reverse
(SQLite schema is create-if-absent and additive); the `./data` DB is untouched
by a code rollback.

---

## 10. Deploy checklist (for the executing agent)

1. [ ] `brew install sshpass` on the laptop; create `release_bot/.env.local`
   from `.env.local.example` with the `VPS_*` values from
   `game_pulse_saas/.env.local`.
2. [ ] On the VPS: `sudo mkdir -p /opt/release_bot && sudo chown dev01:dev01
   /opt/release_bot` (public repo - no deploy key/alias needed; see the §5.1
   fallback only if it is ever made private).
3. [ ] Clone over HTTPS to `/opt/release_bot`:
   `git clone https://github.com/v9833078908/release_bot.git /opt/release_bot`.
4. [ ] `scp` local `.env` -> `/opt/release_bot/.env`; `chmod 600`; set
   `INITIAL_MARKER_SHA` per §7.
5. [ ] Ensure no other instance is polling the token (stop any dev run).
6. [ ] `cd /opt/release_bot && bash scripts/redeploy.sh`; confirm `Run polling`.
7. [ ] In Telegram: `/status` responds.
8. [ ] Thereafter deploy with `scripts/ship.sh` from the laptop.
9. [ ] (Separate, gated) Deploy Game Pulse `/api/v1/version` to prod to enable
   automatic digests.
```
