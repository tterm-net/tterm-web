"""The page's one socket: every terminal the page shows goes through it.

The page opens a single WebSocket and attaches terminals to it, each on a
channel number the page picks. Output comes as binary frames, everything
else as JSON text:

    page → core   {"op": "attach", "ch": 1, "id": "<terminal>", "after": 41,
                   "cols": 120, "rows": 40, "focus": true}
                  {"op": "resize", "ch": 1, "cols": 100, "rows": 30}
                  {"op": "focus" | "pause" | "resume" | "detach" | "close",
                   "ch": 1}
                  binary: 0x02, channel (4 bytes), keystrokes
    core → page   {"op": "attached", "ch": 1, "terminal": {...}, "resumed": true}
                  {"op": "reset", "ch": 1, "seq": 40}   then a full replay,
                  {"op": "replayed", "ch": 1, "seq": 97}  and its end
                  {"op": "ev", "ch": 1, "seq": 52, "t": "P" | "C", ...}
                  {"op": "state", "ch": 1, "terminal": {...}}
                  {"op": "gone", "ch": 1, "terminal": {...} | null}
                  {"op": "error", "message": "..."}
                  binary: 0x01, channel (4 bytes), number (8 bytes), output

"after" is the number of the last output the page has; the core sends what
follows, or, if that is no longer kept or the page has nothing, a reset and
everything kept. The page pauses a channel when it has more on its hands
than it can draw, and resumes it when it catches up. "focus" makes this
page the one that types into the terminal (see Watcher).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from starlette.websockets import WebSocket, WebSocketDisconnect

from ..core.terminals import INPUT, Terminals, Watcher

log = logging.getLogger("tterm.ws")

#: Channels one page may hold at once.
MAX_CHANNELS = 64


class PageSocket:
    def __init__(self, websocket: WebSocket, terminals: Terminals) -> None:
        self.ws = websocket
        self.terminals = terminals
        self.watchers: dict[int, Watcher] = {}
        self._lock = asyncio.Lock()
        self._closing: set[asyncio.Task] = set()

    async def send(self, message: bytes | str) -> None:
        async with self._lock:
            if isinstance(message, bytes):
                await self.ws.send_bytes(message)
            else:
                await self.ws.send_text(message)

    async def json(self, message: dict) -> None:
        await self.send(json.dumps(message, ensure_ascii=False,
                                   separators=(",", ":")))

    async def run(self) -> None:
        await self.ws.accept()
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    self._input(message["bytes"])
                elif message.get("text") is not None:
                    await self._command(message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            await asyncio.gather(*(w.stop() for w in self.watchers.values()),
                                 return_exceptions=True)
            self.watchers.clear()

    def _input(self, frame: bytes) -> None:
        if len(frame) < 5 or frame[0] != INPUT:
            return
        watcher = self.watchers.get(int.from_bytes(frame[1:5], "big"))
        if watcher is not None:
            watcher.write(frame[5:])

    async def _command(self, text: str) -> None:
        try:
            msg = json.loads(text)
            op = msg["op"]
            ch = int(msg.get("ch", 0))
        except (ValueError, KeyError, TypeError):
            await self.json({"op": "error", "message": "Not understood."})
            return
        watcher = self.watchers.get(ch)
        if op == "attach":
            await self._attach(ch, msg)
        elif watcher is None:
            return
        elif op == "resize":
            _resize(watcher.terminal, msg)
        elif op == "focus":
            watcher.focus()
        elif op == "pause":
            watcher.pause()
        elif op == "resume":
            watcher.resume()
        elif op == "detach":
            await self._detach(ch)
        elif op == "close":
            # Ending a shell can take a moment; the socket goes on meanwhile.
            task = asyncio.create_task(watcher.terminal.close())
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

    async def _attach(self, ch: int, msg: dict) -> None:
        await self._detach(ch)
        try:
            terminal_id = uuid.UUID(str(msg.get("id")))
        except ValueError:
            await self.json({"op": "gone", "ch": ch, "terminal": None})
            return
        terminal = self.terminals.get(terminal_id)
        if terminal is None:
            await self.json({"op": "gone", "ch": ch,
                             "terminal": await self.terminals.ended(terminal_id)})
            return
        if len(self.watchers) >= MAX_CHANNELS:
            await self.json({"op": "error", "message": "Too many terminals."})
            return
        _resize(terminal, msg)
        after = msg.get("after")
        after = int(after) if isinstance(after, int) and after >= 0 else None
        watcher = Watcher(terminal, ch, self.send, after)
        if msg.get("focus") is True:
            watcher.focus()
        self.watchers[ch] = watcher

    async def _detach(self, ch: int) -> None:
        watcher = self.watchers.pop(ch, None)
        if watcher is not None:
            await watcher.stop()


def _resize(terminal, msg: dict) -> None:
    """A size the page measured. A page that only watches sends none, and
    leaves the size as it is."""
    try:
        cols, rows = int(msg["cols"]), int(msg["rows"])
    except (KeyError, TypeError, ValueError):
        return
    if cols > 0 and rows > 0:
        terminal.resize(cols, rows)
