#!/usr/bin/env bash
set -euo pipefail
cd /opt/release_bot
git fetch --prune origin main
git reset --hard origin/main
sudo docker compose up --build -d
sudo docker compose ps
