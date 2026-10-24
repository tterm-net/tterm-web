"""One's own photo (0.1.7): only a JPEG such as the page makes, and nothing
of it kept but the picture; seen by its owner, and from 0.2 by those in a
project with them."""
import base64

from ..core import photos
from .harness import request, sign_in_as

# 32 pixels in colour, the same progressive, and 16 in grey, as encoders
# write them.
BASE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8l"
    "JCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIo"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAAR"
    "CAAgACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDziGz9qvw2ftV+Gz9qvw2ftXt1MSZ4LF7F"
    "GGz9qvw2ftV+Gz9qvw2ftXnVMSfZ4LF7GfDZ+1aENn7Vehs/atCGz9q86piT8hwWL2KENn7V"
    "fhs/ar8Nn7Vfhs/avOqYk+zwWL2P/9k=")
PROGRESSIVE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8l"
    "JCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIo"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wgAR"
    "CAAgACADASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAwQG/8QAFwEBAQEBAAAAAAAA"
    "AAAAAAAABAUCBv/aAAwDAQACEAMQAAABzdD0NyD0OezPQ9BuQB6HPa//xAAWEAADAAAAAAAA"
    "AAAAAAAAAAAAAQL/2gAIAQEAAQUCUCgUCgUCgUCgUCgUCgUCgUCg/8QAFREBAQAAAAAAAAAA"
    "AAAAAAAAAwD/2gAIAQMBAT8BFYVhWFb/xAAWEQADAAAAAAAAAAAAAAAAAAAAAgP/2gAIAQIB"
    "AT8Bag1BqDUP/8QAFBABAAAAAAAAAAAAAAAAAAAAQP/aAAgBAQAGPwIH/8QAFBABAAAAAAAA"
    "AAAAAAAAAAAAQP/aAAgBAQABPyEFVVVVV//aAAwDAQACAAMAAAAQqV/U/8QAFREBAQAAAAAA"
    "AAAAAAAAAAAAADH/2gAIAQMBAT8Qmmmm/8QAFBEBAAAAAAAAAAAAAAAAAAAAIP/aAAgBAgEB"
    "PxAKv//EABUQAQEAAAAAAAAAAAAAAAAAAABh/9oACAEBAAE/EIIIIIIIIIIIIIIIIP/Z")
GRAY = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8l"
    "JCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/wAALCAAQABABAREA/8QAHwAA"
    "AQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
    "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RF"
    "RkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ip"
    "qrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEB"
    "AAA/AIaKKK//2Q==")
CHANGE = {"x-tterm": "1", "content-type": "image/jpeg"}


def _segment(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body


def _with_extras(jpeg: bytes) -> bytes:
    """The JPEG with what a camera adds: where it was taken (EXIF), a
    comment, a colour profile; and Adobe's mark with more in it."""
    return (jpeg[:2] + _segment(0xE1, b"Exif\x00\x00II*\x00GPS 38.7223 N 9.1393 W")
            + _segment(0xFE, b"taken at home") + _segment(0xE2, b"ICC_PROFILE\x00x")
            + _segment(0xEE, b"Adobe\x00d\x00\x00\x00\x00\x01" + b"hidden" * 100)
            + jpeg[2:])


def _sof(jpeg: bytes) -> int:
    """Where the frame's marker is."""
    for marker in (b"\xff\xc0", b"\xff\xc2"):
        at = jpeg.find(marker)
        if at >= 0:
            return at
    raise AssertionError("no frame")


def test_only_the_picture_is_kept():
    for jpeg in (BASE, PROGRESSIVE, GRAY):
        clean = photos.clean_jpeg(jpeg)
        assert clean[:2] == b"\xff\xd8" and clean[-2:] == b"\xff\xd9"
        assert b"JFIF" not in clean, "nothing but the picture"
        assert photos.clean_jpeg(clean) == clean, "cleaned twice, the same"
        dirty = _with_extras(jpeg)
        assert photos.clean_jpeg(dirty) == clean
        for told in (b"GPS", b"home", b"ICC_PROFILE", b"Adobe", b"hidden"):
            assert told not in photos.clean_jpeg(dirty)
        # Anything after the end of the picture goes too.
        assert photos.clean_jpeg(jpeg + b"secret") == clean


def test_what_is_not_a_photo_is_refused():
    def refused(data: bytes, why: str) -> None:
        try:
            photos.clean_jpeg(data)
        except photos.NotAPhoto as exc:
            assert why in str(exc), (why, str(exc))
        else:
            raise AssertionError(f"taken: {data[:20]!r}")

    refused(b"", "Not a JPEG")
    refused(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "Not a JPEG")
    refused(b"<svg onload=alert(1)>", "Not a JPEG")
    refused(BASE[:-2], "broken")
    refused(BASE[:400], "broken")
    refused(b"\xff\xd8\xff\xd9", "no picture")
    at = _sof(BASE)
    wide = BASE[:at + 7] + (600).to_bytes(2, "big") + BASE[at + 9:]
    refused(wide, "16 to 512 pixels")
    tiny = BASE[:at + 5] + (8).to_bytes(2, "big") + BASE[at + 7:]
    refused(tiny, "16 to 512 pixels")
    lossless = BASE[:at + 1] + b"\xc3" + BASE[at + 2:]
    refused(lossless, "not taken")
    long = BASE[:at + 2] + b"\xff\xff" + BASE[at + 4:]
    refused(long, "broken")
    twelve = BASE[:at + 4] + b"\x0c" + BASE[at + 5:]
    refused(twelve, "not taken")
    # A frame and a scan with no tables and no picture: nothing to draw.
    bare = (b"\xff\xd8" + _segment(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00")
            + _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\xff\xd9")
    refused(bare, "broken")
    sos = BASE.find(b"\xff\xda")
    empty = BASE[:sos + 2 + int.from_bytes(BASE[sos + 2:sos + 4], "big")] + b"\xff\xd9"
    refused(empty, "no picture")
    dqt = BASE.find(b"\xff\xdb")
    size = int.from_bytes(BASE[dqt + 2:dqt + 4], "big")
    padded = (BASE[:dqt + 2] + (size + 6).to_bytes(2, "big") + BASE[dqt + 4:dqt + 2 + size]
              + b"secret" + BASE[dqt + 2 + size:])
    refused(padded, "broken")
    refused(BASE[:2] + b"\x00" * (photos.SIZE_MAX), "too big")


async def test_the_page_puts_shows_and_removes_the_photo(h):
    async with h.core() as core:
        port = core.page_port
        me = (await request(port, "GET", "/api/state")).json()["me"]
        assert me["photo"] is None, "initials until there is a photo"

        r = await request(port, "PUT", "/api/me/photo", body=_with_extras(BASE),
                          headers=CHANGE)
        assert r.status == 200, r.text
        url = r.json()["photo"]
        assert url.startswith(f"/api/photos/{me['id']}?v="), url
        assert (await request(port, "GET", "/api/state")).json()["me"]["photo"] == url
        got = await request(port, "GET", url)
        assert got.status == 200 and got.headers["content-type"] == "image/jpeg"
        assert got.body == photos.clean_jpeg(BASE) and b"GPS" not in got.body
        assert "immutable" in got.headers["cache-control"]
        assert got.headers["x-content-type-options"] == "nosniff"
        stale = await request(port, "GET", url.split("?")[0] + "?v=1")
        assert stale.status == 200 and "no-cache" in stale.headers["cache-control"]

        r = await request(port, "PUT", "/api/me/photo", body=PROGRESSIVE,
                          headers=CHANGE)
        assert r.status == 200, r.text
        newer = r.json()["photo"]
        assert newer != url, "a new photo, a new address"

        r = await request(port, "PUT", "/api/me/photo", body=BASE,
                          headers={"content-type": "image/jpeg"})
        assert r.status == 403, "a change without the page's header"
        r = await request(port, "PUT", "/api/me/photo", body=BASE,
                          headers={**CHANGE, "content-type": "image/png"})
        assert r.status == 415, r.text
        r = await request(port, "PUT", "/api/me/photo",
                          body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, headers=CHANGE)
        assert r.status == 422 and "cannot take this photo" in r.json()["error"]
        r = await request(port, "PUT", "/api/me/photo",
                          body=b"\xff\xd8" + b"\x00" * photos.SIZE_MAX, headers=CHANGE)
        assert r.status == 413, r.text
        assert (await request(port, "GET", "/api/state")).json()["me"]["photo"] == newer

        # Someone with no project in common sees no photo of Denis's.
        anna = await sign_in_as(core, "anna@example.com")
        r = await request(port, "GET", newer, headers={"cookie": anna})
        assert r.status == 404, r.text
        r = await request(port, "GET", newer, headers={"cookie": ""})
        assert r.status == 401, r.text
        # In a project with him (0.2), she does.
        anna_id = await core.pool.fetchval(
            "SELECT id FROM users WHERE email = 'anna@example.com'")
        project = await core.pool.fetchval(
            "SELECT id FROM projects WHERE owner_id = $1", core.owner.id)
        await core.pool.execute(
            "INSERT INTO project_members (project_id, user_id, role) "
            "VALUES ($1, $2, 'member')", project, anna_id)
        r = await request(port, "GET", newer, headers={"cookie": anna})
        assert r.status == 200 and r.body == photos.clean_jpeg(PROGRESSIVE)
        await core.pool.execute(
            "UPDATE project_members SET until = now() - interval '1 minute' "
            "WHERE user_id = $1", anna_id)
        r = await request(port, "GET", newer, headers={"cookie": anna})
        assert r.status == 404, "her time in the project is over"

        r = await request(port, "DELETE", "/api/me/photo", headers=CHANGE)
        assert r.status == 200 and r.json() == {"photo": None}, r.text
        assert (await request(port, "GET", "/api/state")).json()["me"]["photo"] is None
        assert (await request(port, "GET", newer)).status == 404
