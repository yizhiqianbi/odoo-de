"""OpenRouter chat completions."""
import asyncio
import json
import logging

import httpx

from . import config

log = logging.getLogger(__name__)
http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", timeout=120)


async def chat(messages, tools=None, model=None, temperature=0.3):
    body = {"model": model or config.MODEL, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    for attempt in range(3):
        try:
            res = await http.post("/chat/completions", json=body, headers={
                "Authorization": f"Bearer {config.OPENROUTER_KEY}", "X-Title": "odoo-de trade chat"})
            if res.status_code < 500 and res.status_code != 429:
                res.raise_for_status()
                data = res.json()
                if "choices" in data:
                    return data["choices"][0]["message"]
                log.warning("openrouter returned no choices: %s", str(data)[:300])
        except httpx.TransportError as e:
            log.warning("openrouter transport error: %s", e)
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("OpenRouter 连续三次没有返回结果")


def parse_json(text):
    """The first JSON object in a model reply, or None."""
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None
