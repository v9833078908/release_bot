#!/usr/bin/env bash
# Local "push + deploy" one-shot for release_bot (separate from Game Pulse).
#
# Pushes current branch to origin/main, then SSHs into the VPS and runs
# /opt/release_bot/scripts/redeploy.sh (git reset + docker compose up --build).
#
# Reads VPS_HOST/VPS_USER/VPS_PORT/VPS_PASSWORD from .env.local (same VPS as
# Game Pulse; the bot lives separately at /opt/release_bot).
# Requires: sshpass (`brew install sshpass`).
#
# Usage:
#   scripts/ship.sh              # push main, then redeploy
#   scripts/ship.sh --no-push    # redeploy current origin/main without pushing
#
# NOTE: long polling allows exactly ONE instance. Stop any local bot run before
# deploying to the VPS, or Telegram returns getUpdates 409.

set -euo pipefail

cd "$(dirname "$0")/.."

DO_PUSH=1
[ "${1:-}" = "--no-push" ] && DO_PUSH=0

[ -f .env.local ] || { echo "missing .env.local (copy .env.local.example, fill VPS_*)"; exit 1; }
command -v sshpass >/dev/null || { echo "sshpass not installed (brew install sshpass)"; exit 1; }

read_kv() {
    local file="$1" key="$2"
    grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | sed -E "s/^${key}=//; s/^[\"']//; s/[\"']$//"
}

VPS_HOST=$(read_kv .env.local VPS_HOST)
VPS_USER=$(read_kv .env.local VPS_USER)
VPS_PORT=$(read_kv .env.local VPS_PORT)
VPS_PASSWORD=$(read_kv .env.local VPS_PASSWORD)
for v in VPS_HOST VPS_USER VPS_PORT VPS_PASSWORD; do
    [ -n "${!v}" ] || { echo "missing $v in .env.local"; exit 1; }
done

if [ "$DO_PUSH" = 1 ]; then
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERROR: working tree is dirty. commit or stash first." >&2
        git status --short
        exit 1
    fi
    echo "==> git push origin main"
    git push origin main
fi

export SSHPASS="$VPS_PASSWORD"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ServerAliveInterval=20 -p "$VPS_PORT")

echo "==> ssh $VPS_USER@$VPS_HOST -> /opt/release_bot/scripts/redeploy.sh"
sshpass -e ssh "${SSH_OPTS[@]}" "$VPS_USER@$VPS_HOST" 'bash /opt/release_bot/scripts/redeploy.sh'
