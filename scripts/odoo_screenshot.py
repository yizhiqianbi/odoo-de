"""Screenshots of Odoo as a given person, through headless Chromium. The password is read from the logins file, never printed.

usage: chat/venv/bin/python scripts/odoo_screenshot.py LOGIN OUT.png PATH [WIDTH HEIGHT [JS]]
PATH is under the chat's proxy, e.g. /odoo/action-work_case.work_workbench_action
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
LOGINS = "/home/hubin/odoo-de/secrets/odoo_logins.txt"
login, out, path = sys.argv[1], sys.argv[2], sys.argv[3]
width, height = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (1440, 900)
script = sys.argv[6] if len(sys.argv) > 6 else None

password = dict(line.split("\t")[:2] for line in open(LOGINS).read().splitlines() if line)[login]
http = httpx.Client(base_url=BASE, trust_env=False, timeout=60)
res = http.post("/web/session/authenticate", json={"jsonrpc": "2.0", "method": "call",
                                                   "params": {"db": "de", "login": login, "password": password}})
if res.json().get("error"):
    sys.exit(f"login failed for {login}: {res.json()['error'].get('message')}")
session = http.cookies.get("session_id")

proc = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9334",
                         f"--window-size={width},{height}", "--user-data-dir=/tmp/odoo-shot-profile", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    for _ in range(50):
        try:
            targets = httpx.get("http://127.0.0.1:9334/json", trust_env=False).json()
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        seq = 0
        errors = []

        async def cdp(method, **params):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("method") == "Runtime.exceptionThrown":
                    errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description", "")[:600])
                if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                    errors.append(" ".join(str(a.get("value") or a.get("description", "")) for a in msg["params"]["args"])[:600])
                if msg.get("method") == "Log.entryAdded" and msg["params"]["entry"]["level"] == "error":
                    errors.append(msg["params"]["entry"]["text"][:300] + " " + msg["params"]["entry"].get("url", ""))
                if msg.get("id") == seq:
                    return msg.get("result", {})

        await cdp("Runtime.enable")
        await cdp("Log.enable")
        await cdp("Network.enable")
        await cdp("Network.setCookie", name="session_id", value=session, url=BASE, path="/")
        await cdp("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 700)
        await cdp("Page.navigate", url=BASE + path)
        for _ in range(int(os.environ.get("WAIT", "12"))):
            await asyncio.sleep(1)
            await cdp("Runtime.evaluate", expression="1")
        if script:
            await cdp("Runtime.evaluate", expression=script, awaitPromise=True)
            await asyncio.sleep(4)
        body = await cdp("Runtime.evaluate", expression="location.href + ' | ' + document.body.className + ' | ' + document.body.innerText.slice(0, 300)")
        print("page:", body.get("result", {}).get("value", "").replace("\n", " / "))
        shot = await cdp("Page.captureScreenshot", format="png")
        open(out, "wb").write(base64.b64decode(shot["data"]))
        for e in errors:
            print("page error:", e)


try:
    asyncio.run(main())
finally:
    proc.kill()
print(out)
