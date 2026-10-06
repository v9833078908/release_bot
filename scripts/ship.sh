#!/usr/bin/env bash
# Local "push + deploy" one-shot for release_bot (separate from Game Pulse).
#
# Pushes current branch to origin/main, then SSHs into the VPS and runs
# /opt/release_bot/scripts/redeploy.sh (git reset + docker compose up --build).
#
# Reads VPS_HOST/VPS_USER/VPS_PORT/VPS_PASSWORD from .env.local (same VPS as
# Game Pulse; the bot lives separately at /opt/release_bot).
# Key auth: VPS_SSH_IDENTITY_FILE (recommended); password auth uses sshpass.
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

read_kv() {
    local file="$1" key="$2"
    grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | sed -E "s/^${key}=//; s/^[\"']//; s/[\"']$//" || true
}

VPS_HOST=$(read_kv .env.local VPS_HOST)
VPS_USER=$(read_kv .env.local VPS_USER)
VPS_PORT=$(read_kv .env.local VPS_PORT)
VPS_PASSWORD=$(read_kv .env.local VPS_PASSWORD)
VPS_SSH_IDENTITY_FILE=$(read_kv .env.local VPS_SSH_IDENTITY_FILE)
for v in VPS_HOST VPS_USER VPS_PORT; do
    [ -n "${!v}" ] || { echo "missing $v in .env.local"; exit 1; }
done

# Production moved with Game Pulse; stale laptop access must never deploy old VPS.
if [ "$VPS_HOST" != 162.55.137.149 ] || [ "$VPS_USER" != dev01 ] || [ "$VPS_PORT" != 1996 ]; then
    echo "ERROR: production target mismatch: expected dev01@162.55.137.149:1996. Update .env.local." >&2
    exit 1
fi
case "$VPS_SSH_IDENTITY_FILE" in "~/"*) VPS_SSH_IDENTITY_FILE="$HOME/${VPS_SSH_IDENTITY_FILE#\~/}" ;; esac
if [ -n "$VPS_SSH_IDENTITY_FILE" ]; then
    [ -r "$VPS_SSH_IDENTITY_FILE" ] || { echo "VPS_SSH_IDENTITY_FILE not readable: $VPS_SSH_IDENTITY_FILE" >&2; exit 1; }
else
    command -v sshpass >/dev/null || { echo "sshpass not installed and no VPS_SSH_IDENTITY_FILE" >&2; exit 1; }
    [ -n "$VPS_PASSWORD" ] || { echo "missing VPS_PASSWORD in .env.local" >&2; exit 1; }
fi

if [ "$DO_PUSH" = 1 ]; then
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERROR: working tree is dirty. commit or stash first." >&2
        git status --short
        exit 1
    fi
    echo "==> git push origin main"
    git push origin main
fi

SSH_OPTS=(-o StrictHostKeyChecking=no -o ServerAliveInterval=20 -p "$VPS_PORT")

echo "==> ssh $VPS_USER@$VPS_HOST -> /opt/release_bot/scripts/redeploy.sh"
if [ -n "$VPS_SSH_IDENTITY_FILE" ]; then
    ssh "${SSH_OPTS[@]}" -i "$VPS_SSH_IDENTITY_FILE" -o IdentitiesOnly=yes -o BatchMode=yes "$VPS_USER@$VPS_HOST" 'bash /opt/release_bot/scripts/redeploy.sh'
else
    export SSHPASS="$VPS_PASSWORD"
    sshpass -e ssh "${SSH_OPTS[@]}" "$VPS_USER@$VPS_HOST" 'bash /opt/release_bot/scripts/redeploy.sh'
fi
