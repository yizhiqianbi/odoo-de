"""Serves the Odoo web client through the chat's port, so one exposed port carries both.

Odoo itself stays on 127.0.0.1. Every path the chat does not use is forwarded unchanged, with the browser's Host
and cookies, so Odoo's own login, sessions and access rules apply exactly as if it were reached directly.
"""
import asyncio
from http.cookiejar import CookieJar

import httpx
import websockets
from fastapi import Request, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from . import config

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
       "upgrade", "content-length"}


class NoCookies(CookieJar):
    """The proxy is shared by every browser: it must never keep one browser's Odoo session and replay it for another."""

    def set_cookie(self, cookie):
        pass

    def extract_cookies(self, response, request):
        pass


http = httpx.AsyncClient(base_url=config.ODOO_URL, timeout=httpx.Timeout(300, connect=10), follow_redirects=False,
                         cookies=NoCookies())


def forward_headers(headers, client):
    out = [(k, v) for k, v in headers.items() if k.lower() not in HOP]
    out.append(("x-forwarded-for", client))
    return out


async def forward(request: Request, path: str):
    body = await request.body()  # whole body, so Odoo gets a Content-Length instead of chunks on a reused connection
    upstream = http.build_request(
        request.method, "/" + path, params=request.url.query or None, content=body or None,
        headers=forward_headers(request.headers, request.client.host if request.client else ""))
    try:
        res = await http.send(upstream, stream=True)
    except httpx.TransportError:
        return StreamingResponse(iter([b"Odoo is not reachable right now."]), status_code=502)
    headers = [(k, v) for k, v in res.headers.multi_items() if k.lower() not in HOP]
    response = StreamingResponse(res.aiter_raw(), status_code=res.status_code, background=BackgroundTask(res.aclose))
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
    return response


async def forward_websocket(ws: WebSocket):
    url = config.ODOO_URL.replace("http", "ws", 1) + "/websocket" + (f"?{ws.url.query}" if ws.url.query else "")
    headers = [(k, v) for k, v in ws.headers.items() if k.lower() == "cookie"]
    await ws.accept()
    try:
        async with websockets.connect(url, additional_headers=headers, max_size=None,
                                      user_agent_header=ws.headers.get("user-agent"),
                                      origin=ws.headers.get("origin")) as upstream:
            async def browser_to_odoo():
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        await upstream.close()
                        return
                    await upstream.send(message.get("text") if message.get("text") is not None else message["bytes"])

            async def odoo_to_browser():
                async for message in upstream:
                    await (ws.send_text(message) if isinstance(message, str) else ws.send_bytes(message))

            done, pending = await asyncio.wait([asyncio.create_task(browser_to_odoo()), asyncio.create_task(odoo_to_browser())],
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except (WebSocketDisconnect, websockets.WebSocketException, OSError):
        pass
    finally:
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
