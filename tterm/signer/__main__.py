"""python -m tterm.signer — the signer on its own.

On this computer `python -m tterm.dev` starts it for you. On the server it
runs as its own system user, under its own systemd unit (deploy/), and the
core reaches its socket through the group TTERM_SIGNER_GROUP.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from ..core.config import Settings
from .service import Signer, serve


async def run(settings: Settings) -> int:
    signer = Signer(settings.signer_dir)
    signer.load_or_create()
    try:
        server = await serve(signer, settings.signer_socket,
                             settings.signer_group or None)
    except RuntimeError as exc:
        print(f"signer: {exc}", file=sys.stderr)
        return 1
    active = signer.cas[signer.active]
    logging.getLogger("tterm.signer").info(
        "signer ready: CA %s %s", signer.active, active.get_fingerprint())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(server.wait_closed(), 2)
    with contextlib.suppress(FileNotFoundError):
        settings.signer_socket.unlink()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s signer: %(message)s",
                        datefmt="%H:%M:%S")
    sys.exit(asyncio.run(run(Settings.load())))


if __name__ == "__main__":
    main()
