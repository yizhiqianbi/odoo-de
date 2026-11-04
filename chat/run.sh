#!/usr/bin/env bash
# Starts the group chat service as the unprivileged user tradechat, which holds only the chat's own keys.
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(id -un)" != tradechat ]; then
  exec runuser -u tradechat -- "$PWD/run.sh" "$@"
fi
export CHAT_HOME=/home/tradechat
. "$CHAT_HOME/openrouter.env"
venv/bin/python seed.py
exec venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8090}" --no-access-log --proxy-headers
