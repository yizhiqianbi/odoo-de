"""Screenshots of the chat as a given user, through headless Chromium's DevTools protocol.

usage: chat/venv/bin/python scripts/chat_screenshot.py USERNAME|none OUT.png [WIDTH HEIGHT [PATH [JS]]]
JS runs in the page after it has loaded, e.g. to click something before the shot.
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

import httpx
import websockets

CHROME = "/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
BASE = os.environ.get("CHAT_BASE", "http://127.0.0.1:21047")
role, out = sys.argv[1], sys.argv[2]
width, height = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1440, 900)
token = "" if role == "none" else httpx.post(f"{BASE}/api/login", json={"username": role}).json()["token"]

proc = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9333",
                         f"--window-size={width},{height}", "--user-data-dir=/tmp/chat-shot-profile", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    for _ in range(50):
        try:
            targets = httpx.get("http://127.0.0.1:9333/json", trust_env=False).json()
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        seq = 0

        async def cdp(method, **params):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == seq:
                    return msg.get("result", {})

        await cdp("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 700)
        await cdp("Page.navigate", url=BASE + (sys.argv[5] if len(sys.argv) > 5 else ""))
        await asyncio.sleep(1.5)
        if token:
            await cdp("Runtime.evaluate", expression=f"sessionStorage.setItem('token', {json.dumps(token)}); location.reload()")
        await asyncio.sleep(3)
        if len(sys.argv) > 6:
            await cdp("Runtime.evaluate", expression=sys.argv[6])
            await asyncio.sleep(2)
        shot = await cdp("Page.captureScreenshot", format="png")
        open(out, "wb").write(base64.b64decode(shot["data"]))


try:
    asyncio.run(main())
finally:
    proc.kill()
print(out)
