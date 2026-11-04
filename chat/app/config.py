import os
from pathlib import Path

HOME = Path(os.environ.get("CHAT_HOME", "/home/tradechat"))
DB_PATH = HOME / "chat.db"
BOT_KEY_FILE = HOME / "bot_api_key"

ODOO_URL = os.environ.get("ODOO_URL", "http://127.0.0.1:8069")
ODOO_DB = os.environ.get("ODOO_DB", "de")

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("CHAT_MODEL", "deepseek/deepseek-v4.1-flash-1017")
REVIEW_MODEL = os.environ.get("CHAT_REVIEW_MODEL", MODEL)

TICK_SECONDS = 20
AI_MAX_ROUNDS = 8
# Customer-visible AI messages beyond this rate are held as drafts: (count, seconds).
AI_PUBLIC_LIMIT = (4, 600)
SIM_REPLY_LIMIT = (8, 3600)
SIM_REPLY_DELAY = 6
HISTORY_LIMIT = 40
# Odoo logins whose chat username differs
LOGIN_TO_USERNAME = {"admin": "mitchell"}
# chat users whose Odoo login differs; the chat's own admin reads its reports as Odoo's admin
USERNAME_TO_LOGIN = {"mitchell": "admin", "admin": "admin"}

ASSISTANT_KEY_FILE = HOME / "keys" / "assistant.key"
DESK_POLL_SECONDS = 3
