# Release Bot - Deployment Plan

Date: 2026-07-15
Repo: `git@github.com:v9833078908/release_bot.git`
Prod path: `/opt/release_bot` on the same VPS as Game Pulse (`dev01@<VPS_HOST>`).

## 1. Goal and isolation

Run `release_bot` as its own always-on service on the VPS, **fully separate from
`game_pulse_saas`**:

- Own GitHub repo (`v9833078908/release_bot`), own checkout at `/opt/release_bot`.
- Own `docker compose` project, one service (`release-bot`), **no published
  ports** (long polling only), own SQLite volume (`./data`).
- No shared Postgres/Redis/caddy. Nothing in the Game Pulse stack is touched.
- The only runtime couplings are outbound HTTP: GitHub API, OpenRouter, the
  Telegram Bot API, and Game Pulse's public `GET /api/v1/version`.

Deploy uses the same mechanism as Game Pulse (`sshpass` + `ssh` -> a repo-local
`redeploy.sh` that does `git reset --hard` + `docker compose up --build`), but
simpler: no migrations, no healthcheck HTTP port, no ingress.

## 2. Prerequisites

Already true:
- Code is on `origin/main` (`v9833078908/release_bot`).
- Bot is created (`@gamepulse_poster_bot`), admin of `@game_pulse_whiteboard`,
  admin has pressed Start (verified live).
- VPS credentials live in **`release_bot/.env.local`** (`VPS_HOST`, `VPS_USER`,
  `VPS_PORT`, `VPS_PASSWORD`) - same VPS as Game Pulse. Copy from
  `.env.local.example`; never commit it (gitignored).
- Local `.env` has all runtime secrets (`RELEASE_BOT_TOKEN`, `GITHUB_TOKEN`,
  `OPENROUTER_API_KEY`, `CHANNEL_ID`, `ADMIN_CHAT_ID`, `INITIAL_MARKER_SHA`, ...).

Needed once:
- The VPS's SSH key added as a **read-only Deploy Key** on the `release_bot`
  repo (Settings -> Deploy keys), so `dev01` can `git clone/fetch` it. (Game
  Pulse already wired the equivalent for its repo.)
- `sshpass` on the operator laptop (`brew install sshpass`).

## 3. Artifacts in the repo (added with this plan)

- `scripts/ship.sh` - local trigger: validates clean tree, `git push origin
  main`, then `sshpass ssh` into the VPS to run `redeploy.sh`. `--no-push` to
  deploy current `origin/main` only.
- `scripts/redeploy.sh` - runs on the VPS in `/opt/release_bot`: `git fetch` +
  `reset --hard origin/main`, `docker compose up --build -d`, waits for the
  "Run polling" log (fails on `getUpdates 409` or no-polling), `ps`, prune.
- `.env.local.example` - template for the VPS deploy creds.
- `docker-compose.yml`, `Dockerfile` - already present (one service, no ports,
  `./data` volume, `restart: unless-stopped`).

## 4. One-time VPS setup (gated - run on approval)

Run from the operator laptop unless noted.

1. **Deploy key**: on the VPS, `cat ~/.ssh/id_ed25519.pub` (the key `dev01`
   already uses for GitHub); add it as a read-only Deploy Key on the
   `release_bot` repo. Verify: `ssh -T git@github.com` on the VPS.
2. **Clone** (on VPS): `sudo mkdir -p /opt/release_bot && sudo chown dev01:dev01
   /opt/release_bot && git clone git@github.com:v9833078908/release_bot.git
   /opt/release_bot`.
3. **Provision `.env`** (on VPS): create `/opt/release_bot/.env` with the same
   contents as the local `.env` (it is gitignored, so not in the clone). Copy it
   over with `scp -P <VPS_PORT> .env dev01@<VPS_HOST>:/opt/release_bot/.env`
   (from the laptop). Set `INITIAL_MARKER_SHA` per section 7.
4. **First bring-up** (on VPS): `cd /opt/release_bot && bash scripts/redeploy.sh`.
   Confirms the bot reaches polling.

## 5. Ongoing deploy (gated - run on approval)

From the laptop, in `release_bot/`:

```
scripts/ship.sh            # push main + redeploy on VPS
scripts/ship.sh --no-push  # redeploy current origin/main only
```

`redeploy.sh` on the VPS is idempotent: hard-reset to `origin/main`, rebuild,
restart the single container, verify polling.

## 6. Verification

- `redeploy.sh` fails the deploy if the container does not log `Run polling`
  within ~40s, or if it sees `Conflict: terminated by other getUpdates` (two
  instances on one token).
- Manual: `sudo docker compose -f /opt/release_bot/docker-compose.yml logs
  --tail 50 release-bot` shows `Run polling for bot @gamepulse_poster_bot`.
- In Telegram: `/status` (from the admin) returns marker + pending state;
  `/release_draft` produces a draft once Game Pulse `/version` is live.

## 7. Operational notes (must-read)

- **Single instance.** Long polling means exactly one `getUpdates` consumer.
  Before deploying to the VPS, **stop any local instance** (the dev bot run).
  Never run local + VPS on the same token simultaneously (Telegram 409).
- **SQLite marker handoff.** A fresh VPS `./data` bootstraps the marker from
  `INITIAL_MARKER_SHA`. To avoid re-announcing the already-published weekly post,
  set `INITIAL_MARKER_SHA` on the VPS to the **last published SHA** (the `main`
  HEAD at publish time). Alternatively, `scp` the local
  `data/release_bot.db` to `/opt/release_bot/data/` so the VPS inherits the exact
  marker and draft history. `git reset --hard` never touches the untracked
  `./data`, so the DB survives redeploys.
- **Prod `/version` dependency.** Scheduled/manual digests need Game Pulse's
  `GET /api/v1/version`. Until it is deployed to prod it returns 404 and the bot
  reports "prod SHA unavailable". Deploying that endpoint is a separate,
  explicitly-approved Game Pulse deploy.
- **Backups.** The only state is `/opt/release_bot/data/release_bot.db`. Back it
  up if publish history matters.
- **Secrets.** `/opt/release_bot/.env` holds the bot token, GitHub PAT, and
  OpenRouter key. `chmod 600`. Never commit; rotate via re-`scp`.

## 8. Rollback

`git reset --hard <prev_sha>` in `/opt/release_bot` + `bash scripts/redeploy.sh`.
No schema/state migration to reverse (SQLite schema is create-if-absent and
additive). The `./data` DB is untouched by code rollback.
