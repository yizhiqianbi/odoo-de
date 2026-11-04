#!/usr/bin/env bash
# Runs the trade SOP digital employee once on one order (or on every order it can move, without an argument).
# The DSH sandbox cannot start in this container, so the agent runs unsandboxed as the unprivileged
# user sopbot, which can read only its own Odoo API key, never odoo.conf or secrets/.
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(id -un)" != sopbot ]; then
  exec runuser -u sopbot -- "$PWD/run.sh" "$@"
fi
export HOME=/home/sopbot
export PATH=/opt/dsh-node/bin:$PATH
export DSH_HOME=$HOME/dsh-home
export DSH_PERMISSION_MODE=danger-full-access
export SOP_KEY_FILE=$HOME/.trade_bot_api_key
. "$HOME/.dsh.env"
PATCH=$PWD/openrouter.patch.yml
TASK="跟进订单 ${1:-（先用 ./sop open 列出可推进的订单，逐个处理）}"
if [ ! -d "$DSH_HOME/profiles/trade-sop" ]; then
  exec dsh trade-sop --from-default-profile headless --patch "$PATCH" "$TASK"
fi
exec dsh trade-sop --patch "$PATCH" "$TASK"
