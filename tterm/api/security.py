"""What keeps the page private to this computer while there is no sign-in.

Until stage 2 anyone who can talk to the page acts as its owner, and from
0.0.3 the owner has root on the connected machines. Listening on 127.0.0.1
is not enough on its own, because a browser can be talked into reaching it:

* DNS rebinding. A site makes its own name resolve to 127.0.0.1 and then
  talks to us as if it were us. The Host header gives it away, so we answer
  only to localhost and 127.0.0.1 with our own port.
* Cross-site requests. Any page in any tab can send a POST to
  http://localhost:8090. A request that changes something must carry a
  header a cross-site page cannot set without asking us first, and we never
  agree; an Origin, when the browser sends one, must be ours.
* WebSockets, from 0.0.4: browsers always send Origin there, and it must be
  ours.
"""
from __future__ import annotations

from typing import Awaitable, Callable

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: The header the page puts on every request that changes something.
REQUEST_HEADER = "x-tterm"

PAGE_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self'; "
     b"font-src 'self'; img-src 'self' data:; connect-src 'self'; "
     b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
     b"object-src 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy",
     b"camera=(), microphone=(), geolocation=(), usb=(), payment=()"),
]

#: The machines' side serves no pages at all.
CONNECT_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])}


class AddHeaders:
    """Puts the given headers on every HTTP response, unless already set."""

    def __init__(self, app: ASGIApp, headers: list[tuple[bytes, bytes]]) -> None:
        self.app = app
        self.headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k, v) for k, v in self.headers if k not in present]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class LocalOnly:
    """Lets through only what comes from the page itself, on this computer."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        if headers.get("host", "").lower() not in self.hosts:
            await self._refuse(scope, send, 421,
                               f"Open this page as http://localhost:{self.port}")
            return
        origin = headers.get("origin")
        if scope["type"] == "websocket":
            if origin not in self.origins:
                await self._refuse(scope, send, 403, "Not from this page")
                return
        elif scope["method"] not in SAFE_METHODS:
            if headers.get(REQUEST_HEADER) != "1" or (
                    origin is not None and origin not in self.origins):
                await self._refuse(scope, send, 403, "Not from this page")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, text: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = (text + "\n").encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store")],
        })
        await send({"type": "http.response.body", "body": body})
