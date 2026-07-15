#!/usr/bin/env bash
# Idempotent redeploy. Runs ON the VPS in /opt/release_bot.
#
#   1. With no arg: fetch latest origin/main and hard-reset to it. With a
#      ref/SHA arg: reset to that pinned ref WITHOUT fetching (rollback). Either
#      way untracked ./data and .env are preserved (SQLite state + secrets).
#   2. Rebuild + restart the single release-bot container.
#   3. Verify the bot reaches Telegram long polling (fails on getUpdates 409 or
#      no-polling within ~40s).
#   4. Prune dangling images.
#
# Usage (on the VPS):
#   bash scripts/redeploy.sh            # deploy latest origin/main
#   bash scripts/redeploy.sh <sha|ref>  # pin/rollback to a specific commit
# Trigger from your laptop with:  scripts/ship.sh
# Run as the login user (dev01); sudo is used only for docker compose.

set -euo pipefail

REPO_DIR="/opt/release_bot"
cd "$REPO_DIR"

[ -d .git ] || { echo "ERROR: $REPO_DIR is not a git checkout." >&2; exit 1; }
[ -f .env ] || { echo "ERROR: $REPO_DIR/.env is missing (provision it once)." >&2; exit 1; }

compose=(sudo docker compose)

echo "==> resolve target ref"
prev_sha=$(git rev-parse --short HEAD)
ref="${1:-}"
if [ -z "$ref" ]; then
    echo "    forward deploy: fetch origin/main"
    git fetch --prune origin main
    ref="origin/main"
else
    echo "    pinned deploy/rollback: $ref (no fetch)"
    git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null || {
        echo "ERROR: ref '$ref' not found locally; fetch it first." >&2
        exit 1
    }
fi
git reset --hard "$ref"
new_sha=$(git rev-parse --short HEAD)
echo "    $prev_sha -> $new_sha"

echo "==> build + up (single instance, long polling, no ports)"
"${compose[@]}" up --build -d --remove-orphans

echo "==> wait for Telegram polling"
ok=0
for _ in $(seq 1 20); do
    logs=$("${compose[@]}" logs --tail 60 release-bot 2>/dev/null || true)
    if echo "$logs" | grep -q "Conflict: terminated by other getUpdates"; then
        echo "ERROR: another instance is polling this bot token (getUpdates 409)." >&2
        echo "$logs" | tail -20 >&2
        exit 1
    fi
    if echo "$logs" | grep -qE "Run polling|Start polling"; then
        ok=1
        break
    fi
    sleep 2
done
if [ "$ok" != 1 ]; then
    echo "ERROR: bot did not reach polling within ~40s. Recent logs:" >&2
    "${compose[@]}" logs --tail 40 release-bot >&2
    exit 1
fi
echo "    OK: polling"

echo "==> stack state"
"${compose[@]}" ps

echo "==> prune dangling images"
sudo docker image prune -f >/dev/null

echo "==> deployed $new_sha"
