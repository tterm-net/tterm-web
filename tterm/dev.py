"""tTerm Web on this computer: python -m tterm.dev

Starts the core with the page and the machines' side. From 0.0.3 it starts
the signer next to it as a separate process, as on the server later. Ctrl+C
stops everything.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import __version__
from .core import db
from .core.config import Settings
from .server import Core, PortBusy


def banner(settings: Settings, core: Core) -> str:
    return "\n".join([
        f"tTerm Web {__version__}",
        f"  page       http://localhost:{core.page_port}"
        "   only on this computer",
        f"  machines   http://127.0.0.1:{core.connect_port}"
        f"   the tunnel to {settings.connect_url} points here",
        f"  database   {db.describe(settings.database_url)}",
        f"  you        {core.owner.name if core.owner else '?'}",
        "Ctrl+C stops it.",
        "",
    ])


async def run(settings: Settings) -> int:
    core = Core(settings)
    try:
        await core.start()
    except (db.DatabaseUnavailable, PortBusy) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(banner(settings, core), flush=True)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    waiter = asyncio.create_task(stopping.wait())
    died = asyncio.create_task(core.wait())
    await asyncio.wait([waiter, died], return_when=asyncio.FIRST_COMPLETED)
    if died.done() and not stopping.is_set():
        logging.getLogger("tterm").error("a server stopped on its own")
    print("\nStopping…", flush=True)
    await core.stop()
    waiter.cancel()
    died.cancel()
    return 0 if stopping.is_set() else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    settings = Settings.load()
    sys.exit(asyncio.run(run(settings)))


if __name__ == "__main__":
    main()
