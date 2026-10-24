"""One's own photo (0.1.7): a picture in place of the initials, where the
name is written next to it.

The page cuts the photo to a circle and makes it 256 pixels square itself,
as a JPEG. The core takes only that: a JPEG of at most 512 pixels a side and
256 KB, and keeps nothing of it but what draws the picture. Whatever else a
JPEG may carry, where it was taken and with what (EXIF), a thumbnail,
comments, is left out: a photo shows a face, not where its owner lives.

Who sees it: the person, and from 0.2 whoever shares a project with them.
Nothing goes to any other service: no Gravatar, the address stays here.
"""
from __future__ import annotations

import datetime as dt
import uuid

import asyncpg

#: What the core takes, at most: the page sends some 20 KB.
SIZE_MAX = 256 * 1024
SIDE_MIN, SIDE_MAX = 16, 512
#: Segments of a JPEG, at most: a real one has a dozen.
SEGMENTS_MAX = 200

#: What draws the picture: the frame (baseline, extended, progressive), the
#: Huffman and quantization tables and the restart interval, each checked to
#: be that and no more. Everything else goes.
_FRAMES = {0xC0, 0xC1, 0xC2}
_DHT, _DQT, _DRI, _SOS = 0xC4, 0xDB, 0xDD, 0xDA
_KEEP = _FRAMES | {_DHT, _DQT, _DRI}


def _tables(marker: int, body: bytes) -> bool:
    """Huffman or quantization tables, whole, with nothing else in the
    segment: a table's class and number, then its 16 counts and the
    symbols they count, or its 64 values of one or two bytes."""
    at = 0
    while at < len(body):
        if marker == _DHT:
            if at + 17 > len(body):
                return False
            at += 17 + sum(body[at + 1:at + 17])
        else:
            at += 1 + (128 if body[at] >> 4 else 64)
    return at == len(body) > 0


class NotAPhoto(ValueError):
    """Not a JPEG the page makes."""


def clean_jpeg(data: bytes) -> bytes:
    """The JPEG with only what draws the picture, checked as it goes:
    NotAPhoto if it is not one, or is too big."""
    if len(data) > SIZE_MAX:
        raise NotAPhoto("The photo is too big.")
    if data[:2] != b"\xff\xd8":
        raise NotAPhoto("Not a JPEG.")
    out = [b"\xff\xd8"]
    frames = scans = 0
    seen: set[int] = set()
    i, n = 2, len(data)
    for _ in range(SEGMENTS_MAX):
        if i >= n or data[i] != 0xFF:
            raise NotAPhoto("The JPEG is broken.")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            raise NotAPhoto("The JPEG is broken.")
        marker = data[i]
        i += 1
        if marker == 0xD9:
            if not frames or not scans:
                raise NotAPhoto("The JPEG has no picture in it.")
            out.append(b"\xff\xd9")
            return b"".join(out)
        if i + 2 > n:
            raise NotAPhoto("The JPEG is broken.")
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > n:
            raise NotAPhoto("The JPEG is broken.")
        segment = data[i - 2:i + length]
        body = data[i + 2:i + length]
        i += length
        if marker == _SOS:
            if (not frames or _DHT not in seen or _DQT not in seen
                    or not body or not 1 <= body[0] <= 4
                    or len(body) != 4 + 2 * body[0]):
                raise NotAPhoto("The JPEG is broken.")
            # The compressed picture runs to the next marker that is not a
            # stuffed byte (FF 00) or a restart (FF D0 to FF D7).
            start = i
            while True:
                at = data.find(b"\xff", i)
                if at < 0 or at + 1 >= n:
                    raise NotAPhoto("The JPEG is broken.")
                following = data[at + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    i = at + 2
                    continue
                if following == 0xFF:
                    i = at + 1
                    continue
                i = at
                break
            if i == start:
                raise NotAPhoto("The JPEG has no picture in it.")
            out.append(segment + data[start:i])
            scans += 1
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if marker not in _FRAMES:
                raise NotAPhoto("This kind of JPEG is not taken.")
            if frames or len(body) < 6 or len(body) != 6 + 3 * body[5]:
                raise NotAPhoto("The JPEG is broken.")
            height = int.from_bytes(body[1:3], "big")
            width = int.from_bytes(body[3:5], "big")
            components = body[5]
            # Browsers draw 8 bits a sample, grey or colour.
            if body[0] != 8 or components not in (1, 3):
                raise NotAPhoto("This kind of JPEG is not taken.")
            if not (SIDE_MIN <= width <= SIDE_MAX and SIDE_MIN <= height <= SIDE_MAX):
                raise NotAPhoto("The photo must be 16 to 512 pixels a side.")
            frames += 1
        if marker in (_DHT, _DQT) and not _tables(marker, body):
            raise NotAPhoto("The JPEG is broken.")
        if marker == _DRI and len(body) != 2:
            raise NotAPhoto("The JPEG is broken.")
        if marker in _KEEP:
            seen.add(marker)
            out.append(segment)
    raise NotAPhoto("The JPEG is broken.")


def url_for(user_id: uuid.UUID, at: dt.datetime) -> str:
    """Where the page loads the photo from: a new photo, a new address, so
    that the browser never shows the one it kept."""
    return f"/api/photos/{user_id}?v={int(at.timestamp() * 1000)}"


async def url_of(pool: asyncpg.Pool, user_id: uuid.UUID) -> str | None:
    at = await pool.fetchval(
        "SELECT updated_at FROM photos WHERE user_id = $1", user_id)
    return url_for(user_id, at) if at is not None else None


async def put(pool: asyncpg.Pool, user_id: uuid.UUID, data: bytes) -> str:
    """Takes the photo, cleaned (NotAPhoto if it is not one); returns where
    the page loads it from."""
    image = clean_jpeg(data)
    at = await pool.fetchval(
        """
        INSERT INTO photos (user_id, image) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE
            SET image = EXCLUDED.image,
                updated_at = greatest(clock_timestamp(),
                                      photos.updated_at + interval '1 millisecond')
        RETURNING updated_at
        """, user_id, image)
    return url_for(user_id, at)


async def remove(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    await pool.execute("DELETE FROM photos WHERE user_id = $1", user_id)


async def get(pool: asyncpg.Pool, viewer: uuid.UUID,
              owner: uuid.UUID) -> tuple[bytes, str] | None:
    """The photo and its version, if there is one and `viewer` may see it:
    their own, or of someone in a project with them."""
    row = await pool.fetchrow(
        """
        SELECT image, updated_at FROM photos ph
        WHERE ph.user_id = $2 AND ($1 = $2 OR EXISTS (
            SELECT 1 FROM project_members a
            JOIN project_members b ON b.project_id = a.project_id
            WHERE a.user_id = $1 AND b.user_id = $2
              AND (a.until IS NULL OR a.until > now())
              AND (b.until IS NULL OR b.until > now())))
        """, viewer, owner)
    if row is None:
        return None
    return row["image"], str(int(row["updated_at"].timestamp() * 1000))
