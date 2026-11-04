"""Live connections. Internal events never reach customers: filtering happens here, on the server."""
import asyncio


class Hub:
    def __init__(self):
        self.conns = {}  # websocket -> user

    def add(self, ws, user):
        self.conns[ws] = user

    def remove(self, ws):
        self.conns.pop(ws, None)

    async def send(self, user_ids, event, internal=False):
        targets = [(ws, user) for ws, user in self.conns.items()
                   if user["id"] in user_ids and (not internal or user["role"] != "customer")]
        results = await asyncio.gather(*(ws.send_json(for_viewer(event, user)) for ws, user in targets),
                                       return_exceptions=True)
        for (ws, _), result in zip(targets, results):
            if isinstance(result, Exception):
                self.remove(ws)


def for_viewer(event, user):
    """The customer gets group messages without the internal bookkeeping kept in meta."""
    if user["role"] == "customer" and "message" in event:
        return {**event, "message": public_message(event["message"])}
    return event


def public_message(message):
    return {k: v for k, v in message.items() if k != "meta"}
