"""HTTP and WebSocket API. Who may see and do what is checked here, per request, from the login token."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, odoo_proxy
from .errors import ChatError
from .hub import public_message
from .playbooks import PLAYBOOKS, labels
from .protocol import LEVELS, RESOURCES
from .service import Service, party_lang

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
STATIC = Path(__file__).resolve().parent.parent / "static"
svc: Service = None


@asynccontextmanager
async def lifespan(app):
    global svc
    svc = Service()
    task = asyncio.create_task(svc.loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/split")
def split():
    return FileResponse(STATIC / "split.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/desk")
def desk_page():
    return RedirectResponse("/odoo/discuss")


@app.post("/api/assistant/ping")
def assistant_ping():
    """Odoo says someone wrote to the digital employee; the inbox is read now instead of at the next poll."""
    svc.desk.wake.set()
    return {"ok": True}


def current_user(authorization: str = Header(default="")):
    user = svc.store.user_by_token(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "只有管理员能做这件事")
    return user


def member_group(group_id, user):
    group = svc.store.group(group_id)
    if not group or (user["role"] != "admin" and user["id"] not in svc.store.members(group)):
        raise HTTPException(404, "没有这个群")
    return group


def sales_group(group_id, user):
    group = member_group(group_id, user)
    if user["role"] == "customer":
        raise HTTPException(403, "只有公司内部的人能做这件事")
    return group


async def guarded(coro):
    try:
        return await coro
    except ChatError as e:
        raise HTTPException(400, str(e)) from e


class Login(BaseModel):
    username: str


class Say(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    internal: bool = False


class DraftAction(BaseModel):
    send: bool
    body: str | None = Field(default=None, max_length=4000)
    note: str = ""


class Decision(BaseModel):
    approve: bool
    choice: str | None = None
    note: str = ""


class Settings(BaseModel):
    ai_auto: bool | None = None
    sim_customer: bool | None = None
    chase_minutes: int | None = Field(default=None, ge=1, le=10080)


class Task(BaseModel):
    agent: str
    body: str = Field(min_length=1, max_length=4000)


class Grant(BaseModel):
    subject: str
    verb: Literal["ask", "task", "read"]
    target: str
    max_level: int = Field(ge=0, le=3)


class AgentEdit(BaseModel):
    clearance: int | None = Field(default=None, ge=0, le=3)
    enabled: bool | None = None


@app.get("/api/identities")
def identities():
    return svc.store.users()


@app.post("/api/odoo-sso")
async def odoo_sso(request: Request):
    """The chat inside an Odoo form: whoever is signed in to Odoo in this browser is who is in the chat."""
    session = request.cookies.get("session_id")
    if not session:
        raise HTTPException(401, "请先登录 Odoo")
    try:
        async with httpx.AsyncClient(base_url=config.ODOO_URL, timeout=15) as client:
            res = await client.post("/web/session/get_session_info", cookies={"session_id": session},
                                    json={"jsonrpc": "2.0", "method": "call", "params": {}})
        info = res.json().get("result") or {}
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, "连不上 Odoo") from e
    if not info.get("uid") or not info.get("username"):
        raise HTTPException(401, "Odoo 登录已过期，请刷新页面")
    user = svc.store.user_by_username(config.LOGIN_TO_USERNAME.get(info["username"], info["username"]))
    if not user or user["role"] == "customer":
        raise HTTPException(403, "这个 Odoo 账号在群聊里没有身份")
    return {"token": svc.store.issue_token(user["id"]), "user": user}


@app.post("/api/login")
def login(body: Login):
    user = svc.store.user_by_username(body.username)
    if not user:
        raise HTTPException(404, "没有这个身份")
    return {"token": svc.store.issue_token(user["id"]), "user": user}


@app.get("/api/me")
def me(user=Depends(current_user)):
    assistant = svc.store.agent(f"assistant:{user['username']}")
    lang = "zh"
    if user["role"] == "customer":
        groups = svc.store.groups_for(user)
        lang = party_lang(svc.ctx.get(groups[0]["id"]) or {}) if groups else "en"
    return {**user, "assistant": assistant and assistant["key"], "lang": lang}


@app.get("/api/groups")
def groups(user=Depends(current_user)):
    internal = user["role"] != "customer"
    return [{"id": g["id"], "title": g["title"], "order_name": g["order_name"], "playbook": g["playbook"],
             "res_model": g["res_model"], "res_id": g["res_id"],
             "labels": labels(g["playbook"]), "customer": svc.store.user(g["customer_id"])["name"],
             "sales": [s["name"] for s in svc.store.salespeople(g)],
             **(svc.group_summary(g) if internal else {})}
            for g in svc.store.groups_for(user)]


@app.get("/api/groups/{group_id}")
def group_detail(group_id: int, user=Depends(current_user)):
    group = member_group(group_id, user)
    internal = user["role"] != "customer"
    messages = svc.store.messages(group_id, internal=internal)
    return {
        "messages": messages if internal else [public_message(m) for m in messages],
        "panel": svc.panel(group) if internal else None,
        "header": svc.customer_header(group),
    }


@app.post("/api/groups/{group_id}/messages")
async def say(group_id: int, body: Say, user=Depends(current_user)):
    group = member_group(group_id, user)
    if user["role"] == "customer":
        await svc.customer_says(group, body.body.strip())
    else:
        await svc.sales_says(sales_group(group_id, user), user, body.body.strip(), body.internal)
    return {"ok": True}


@app.post("/api/groups/{group_id}/drafts/{message_id}")
async def draft(group_id: int, message_id: int, body: DraftAction, user=Depends(current_user)):
    group = sales_group(group_id, user)
    if body.send:
        await guarded(svc.send_draft(group, user, message_id, body.body))
    else:
        await guarded(svc.reject_draft(group, user, message_id, body.note.strip()))
    return {"ok": True}


@app.post("/api/groups/{group_id}/decisions/{message_id}")
async def decide(group_id: int, message_id: int, body: Decision, user=Depends(current_user)):
    group = sales_group(group_id, user)
    if user["role"] != "sales":
        raise HTTPException(403, "流程上的决定要由负责人本人在 Odoo 里留痕")
    await guarded(svc.decide(group, user, message_id, body.approve, body.choice, body.note.strip()))
    return {"ok": True}


@app.post("/api/groups/{group_id}/proposals/{message_id}")
async def confirm_proposal(group_id: int, message_id: int, body: Decision, user=Depends(current_user)):
    group = sales_group(group_id, user)
    if user["role"] != "sales":
        raise HTTPException(403, "Odoo 里的动作要由负责人本人确认")
    await guarded(svc.confirm_proposal(group, user, message_id, body.approve, body.note.strip()))
    return {"ok": True}


@app.post("/api/groups/{group_id}/settings")
async def settings(group_id: int, body: Settings, user=Depends(current_user)):
    group = sales_group(group_id, user)
    values = {k: int(v) for k, v in body.model_dump(exclude_none=True).items()}
    svc.store.update_group(group_id, **values)
    await svc.push_panel(group)
    return {"ok": True}


@app.post("/api/groups/{group_id}/nudge")
async def nudge(group_id: int, user=Depends(current_user)):
    sales_group(group_id, user)
    names = labels(svc.store.group(group_id)["playbook"])
    svc.trigger(group_id, f"{names['owner']}让你现在主动检查一遍这个事项：有什么该推进、该问{names['party']}、"
                          f"该提醒{names['owner']}的，就去做。", explicit=True)
    return {"ok": True}


# the salesperson's own assistant agent

@app.get("/api/assistant")
def assistant(user=Depends(current_user)):
    key = f"assistant:{user['username']}"
    if not svc.store.agent(key):
        raise HTTPException(404, "你没有助理 agent")
    envelopes = {e["id"]: e for e in svc.store.envelopes(f"user:{user['id']}", limit=100)
                 + svc.store.envelopes(f"agent:{key}", limit=100)}
    envelopes = [envelopes[i] for i in sorted(envelopes)]
    return {"agent": key, "status": svc.status.get(key), "envelopes": envelopes,
            "names": names({p for e in envelopes for p in (e["sender"], e["recipient"])} | {f"agent:{key}"})}


def names(principals=None):
    """Display names for principals, so envelopes read as people and agents rather than keys."""
    everyone = {**{f"agent:{a['key']}": a["name"] for a in svc.store.agents()},
                **{f"user:{u['id']}": u["name"] for u in svc.store.users()}}
    return everyone if principals is None else {p: everyone[p] for p in principals if p in everyone}


@app.post("/api/assistant")
async def assistant_task(body: Task, user=Depends(current_user)):
    if body.agent != f"assistant:{user['username']}" and user["role"] != "admin":
        raise HTTPException(403, "你只能给自己的助理下任务")
    return await guarded(svc.human_to_agent(user, body.agent, body.body.strip()))


# the administrator's console

@app.get("/api/admin/overview")
def overview(user=Depends(admin_user)):
    return {"agents": [{**a, "status": svc.status.get(a["key"])} for a in svc.store.agents()],
            "grants": svc.store.grants(), "envelopes": svc.store.envelopes(limit=300),
            "users": svc.store.users(), "levels": LEVELS, "names": names(),
            "groups": [{"customer_key": g["customer_key"], "order_name": g["order_name"], "title": g["title"],
                        "playbook": g["playbook"],
                        "sales": [s["username"] for s in svc.store.salespeople(g)]} for g in svc.store.all_groups()],
            "resources": {k: {"level": level, "per_customer": per_customer, "title": title}
                          for k, (level, per_customer, title) in RESOURCES.items()}}


@app.post("/api/admin/grants")
async def add_grant(body: Grant, user=Depends(admin_user)):
    if not svc.store.agent(body.subject.removeprefix("agent:")):
        raise HTTPException(400, "授权对象必须是一个 agent")
    svc.store.grant(body.subject.removeprefix("agent:"), body.verb, body.target, body.max_level, source=user["username"])
    await svc.push_agent_event({"type": "grants"})
    return {"ok": True}


@app.delete("/api/admin/grants/{grant_id}")
async def revoke_grant(grant_id: int, user=Depends(admin_user)):
    svc.store.revoke(grant_id)
    await svc.push_agent_event({"type": "grants"})
    return {"ok": True}


@app.post("/api/admin/agents/{key:path}")
async def edit_agent(key: str, body: AgentEdit, user=Depends(admin_user)):
    if not svc.store.agent(key):
        raise HTTPException(404, "没有这个 agent")
    values = {k: int(v) for k, v in body.model_dump(exclude_none=True).items()}
    svc.store.update_agent(key, **values)
    await svc.push_agent_event({"type": "grants"})
    return {"ok": True}


@app.post("/api/admin/task")
async def admin_task(body: Task, user=Depends(admin_user)):
    return await guarded(svc.human_to_agent(user, body.agent, body.body.strip()))


@app.websocket("/websocket")
async def odoo_websocket(websocket: WebSocket):
    await odoo_proxy.forward_websocket(websocket)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
               include_in_schema=False)
async def odoo(request: Request, path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    return await odoo_proxy.forward(request, path)


@app.websocket("/ws")
async def ws(websocket: WebSocket, token: str = ""):
    user = svc.store.user_by_token(token)
    if not user:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    svc.hub.add(websocket, user)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        svc.hub.remove(websocket)
