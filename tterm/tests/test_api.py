"""The running core: the page and its API, the machines' side, the guards."""
import json
import tempfile
from pathlib import Path

from .. import __version__
from ..server import Core, PortBusy, listen
from .harness import free_socket, request

CHANGE = {"x-tterm": "1", "content-type": "application/json"}


async def test_both_sides_answer(h):
    async with h.core() as core:
        page = await request(core.page_port, "GET", "/health")
        assert page.status == 200 and page.json()["service"] == "tterm-web page"
        machines = await request(core.connect_port, "GET", "/health",
                                 host="connect-dev.tterm.net")
        assert machines.status == 200, machines.status
        assert machines.json() == {"ok": True, "service": "tterm-web connect",
                                   "version": __version__}


async def test_state_has_owner_and_personal(h):
    async with h.core() as core:
        r = await request(core.page_port, "GET", "/api/state")
        assert r.status == 200, r.status
        assert r.headers["cache-control"] == "no-store"
        state = r.json()
        assert state["version"] == __version__
        me = state["me"]
        assert (me["name"], me["firstName"], me["initials"], me["theme"]) == (
            "Denis Gerasimenko", "Denis", "DG", "system"), me
        assert [(p["name"], p["code"], p["role"], p["machines"])
                for p in state["projects"]] == [("Personal", "P", "owner", [])]


async def test_theme_change_needs_the_page_header(h):
    async with h.core() as core:
        body = json.dumps({"theme": "light"}).encode()
        r = await request(core.page_port, "PATCH", "/api/me", body=body,
                          headers={"content-type": "application/json"})
        assert r.status == 403, f"without x-tterm: {r.status}"
        r = await request(core.page_port, "PATCH", "/api/me", body=body,
                          headers=CHANGE)
        assert r.status == 204, r.status
        state = (await request(core.page_port, "GET", "/api/state")).json()
        assert state["me"]["theme"] == "light"
        r = await request(core.page_port, "PATCH", "/api/me", headers=CHANGE,
                          body=json.dumps({"theme": "blue"}).encode())
        assert r.status == 422, r.status


async def test_other_sites_cannot_act(h):
    async with h.core() as core:
        body = json.dumps({"theme": "dark"}).encode()
        # A page in another tab, even with our header, gives its own origin.
        r = await request(core.page_port, "PATCH", "/api/me", body=body,
                          headers={**CHANGE, "origin": "https://evil.example"})
        assert r.status == 403, r.status
        r = await request(core.page_port, "PATCH", "/api/me", body=body,
                          headers={**CHANGE,
                                   "origin": f"http://localhost:{core.page_port}"})
        assert r.status == 204, r.status


async def test_dns_rebinding_is_refused(h):
    async with h.core() as core:
        for host in ("evil.example", f"evil.example:{core.page_port}",
                     "localhost:1", "127.0.0.1"):
            r = await request(core.page_port, "GET", "/api/state", host=host)
            assert r.status == 421, f"{host}: {r.status}"
            assert "frame-ancestors 'none'" in r.headers.get(
                "content-security-policy", ""), "refusals carry the headers too"
        for host in (f"localhost:{core.page_port}",
                     f"127.0.0.1:{core.page_port}"):
            r = await request(core.page_port, "GET", "/api/state", host=host)
            assert r.status == 200, f"{host}: {r.status}"


async def test_page_security_headers(h):
    async with h.core() as core:
        r = await request(core.page_port, "GET", "/")
        csp = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
        assert "script-src 'self'" in csp and "font-src 'self'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"


async def test_page_files_and_addresses_inside_the_page(h):
    async with h.core() as core:
        index = await request(core.page_port, "GET", "/")
        assert index.status == 200 and "<div id=root>" in index.text
        assert index.headers["cache-control"] == "no-cache"
        # An address inside the page survives a reload.
        deep = await request(core.page_port, "GET", "/projects/p/overview")
        assert deep.status == 200 and "<div id=root>" in deep.text
        asset = await request(core.page_port, "GET", "/assets/index.js")
        assert asset.status == 200 and asset.text == "console.log(1)"
        missing = await request(core.page_port, "GET", "/assets/nope.js")
        assert missing.status == 404, missing.status
        api = await request(core.page_port, "GET", "/api/nope")
        assert api.status == 404 and api.json() == {"error": "no such API"}


async def test_page_files_stay_inside_dist(h):
    async with h.core() as core:
        for path in ("/../tterm/server.py", "/%2e%2e/tterm/server.py",
                     "/assets/../../tterm/server.py", "//etc/passwd"):
            r = await request(core.page_port, "GET", path)
            assert "import" not in r.text and "root:" not in r.text, path


async def test_machines_side_has_no_page_and_no_api(h):
    async with h.core() as core:
        for path in ("/", "/api/state", "/assets/index.js"):
            r = await request(core.connect_port, "GET", path,
                              host="connect-dev.tterm.net")
            assert r.status == 404, f"{path}: {r.status}"
        r = await request(core.connect_port, "GET", "/health",
                          host="connect-dev.tterm.net")
        assert r.headers["content-security-policy"].startswith("default-src 'none'")


async def test_page_not_built_says_so(h):
    empty = Path(tempfile.mkdtemp())
    async with h.core(dist=empty) as core:
        r = await request(core.page_port, "GET", "/")
        assert r.status == 503 and "not built" in r.text, r.status


async def test_busy_port_is_a_clear_message(h):
    taken = free_socket()
    try:
        port = taken.getsockname()[1]
        try:
            listen("127.0.0.1", port)
        except PortBusy as exc:
            assert str(port) in str(exc) and "already running" in str(exc)
        else:
            raise AssertionError("bound a busy port")
    finally:
        taken.close()


async def test_core_stops_cleanly_and_frees_its_ports(h):
    async with h.core() as core:
        page_port, connect_port = core.page_port, core.connect_port
    for port in (page_port, connect_port):
        listen("127.0.0.1", port).close()   # free again


async def test_core_without_database_does_not_leave_ports_taken(h):
    from ..core.config import Settings
    from ..core.db import DatabaseUnavailable
    settings = Settings.load(environ={
        "TTERM_DATABASE_URL": "postgresql://localhost:1/tterm_web"},
        env_file=Path("/nonexistent/.env"))
    page, connect = free_socket(), free_socket()
    ports = (page.getsockname()[1], connect.getsockname()[1])
    try:
        await Core(settings).start(page_socket=page, connect_socket=connect)
    except DatabaseUnavailable:
        pass
    else:
        raise AssertionError("started without a database")
    for port in ports:
        listen("127.0.0.1", port).close()
