"""python -m tterm.tests — every check, then a summary.

Run from the repository root. The checks need PostgreSQL on the same server
as tTerm Web itself (Postgres.app, port 5433, unless TTERM_DATABASE_URL says
otherwise). They create throwaway databases there and drop them at the end;
the real database is never opened.

    python -m tterm.tests              all checks
    python -m tterm.tests api          only those whose name contains "api"

Prints ✓ or ✗ per check and ends with "Passed: N   Failed: M".
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import pkgutil
import sys
import time
import traceback
from pathlib import Path

from ..core.config import Settings
from ..core.db import DatabaseUnavailable
from . import harness as harness_module

MODULES = sorted(m.name for m in pkgutil.iter_modules([str(Path(__file__).parent)])
                 if m.name.startswith("test_"))


def discover(pattern: str | None):
    for name in MODULES:
        module = importlib.import_module(f"tterm.tests.{name}")
        for attr, fn in vars(module).items():
            if not attr.startswith("test_") or not callable(fn):
                continue
            full = f"{name[5:]}.{attr[5:]}"
            if pattern and pattern not in full:
                continue
            yield full, fn


async def main(pattern: str | None) -> int:
    settings = Settings.load()
    h = harness_module.Harness(settings.database_url)
    try:
        await h.start()
    except DatabaseUnavailable as exc:
        print(f"✗ cannot start: {exc}")
        print("Passed: 0   Failed: 1")
        return 1

    passed = failed = 0
    started = time.monotonic()
    try:
        for full, fn in discover(pattern):
            try:
                if "h" in inspect.signature(fn).parameters:
                    result = fn(h)
                else:
                    result = fn()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, 60)
            except Exception as exc:
                failed += 1
                where = traceback.extract_tb(exc.__traceback__)[-1]
                print(f"✗ {full}: {type(exc).__name__}: {exc} "
                      f"(line {where.lineno} of {where.filename.rsplit('/', 1)[-1]})")
            else:
                passed += 1
                print(f"✓ {full}")
    finally:
        await h.close()
    print(f"\nPassed: {passed}   Failed: {failed}   "
          f"({time.monotonic() - started:.1f} s)")
    return 1 if failed else 0


if __name__ == "__main__":
    # The checks provoke warnings on purpose (a missing signer, a refused
    # certificate); the ✓ and ✗ lines are what matters, not the log.
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(only)))
