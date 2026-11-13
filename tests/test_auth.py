import asyncio

from phonectl.server import _BearerAuth


async def _app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _status(header: bytes | None, method: str = "POST") -> int:
    sent = []

    async def send(msg):
        sent.append(msg)

    headers = [(b"authorization", header)] if header is not None else []
    asyncio.run(_BearerAuth(_app, "secret")({"type": "http", "method": method, "headers": headers}, None, send))
    return sent[0]["status"]


def test_accepts_the_token():
    assert _status(b"Bearer secret") == 200


def test_rejects_missing_or_wrong_token():
    assert _status(None) == 401
    assert _status(b"Bearer nope") == 401
    assert _status(b"Basic secret") == 401


def test_refuses_the_get_stream():
    # A held GET stream is what made clients drop the server when it restarted.
    assert _status(b"Bearer secret", "GET") == 405
    assert _status(None, "GET") == 401
