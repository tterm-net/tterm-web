"""The signer: its keys, the certificates it signs and what it refuses."""
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path

import asyncssh

from ..core.signer_client import SignerClient, SignerRefused, SignerUnavailable
from ..signer import service
from ..signer.service import Refused, Signer, serve


def _signer() -> tuple[Signer, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="tt"))
    signer = Signer(tmp / "signer")
    signer.load_or_create()
    return signer, tmp


def _public() -> str:
    return asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()


#: A server's own principal, as the core makes them (from 0.0.12).
LAB = f"m-{uuid.uuid4()}"


def test_first_start_makes_one_ca_and_keeps_it():
    signer, tmp = _signer()
    try:
        assert len(signer.cas) == 1 and signer.active in signer.cas
        ca_id = signer.active
        assert ca_id.startswith("ca-") and len(ca_id) == len("ca-20261012-abcd")
        key_file = tmp / "signer" / "cas" / f"{ca_id}.key"
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp / "signer").stat().st_mode) == 0o700
        again = Signer(tmp / "signer")
        again.load_or_create()
        assert again.active == ca_id, "the same CA after a restart"
        assert again.cas[ca_id].export_public_key() == \
            signer.cas[ca_id].export_public_key()
    finally:
        shutil.rmtree(tmp)


def test_certificate_is_short_and_narrow():
    signer, tmp = _signer()
    try:
        reply = signer.sign(_public(), LAB, "Denis via tTerm Web", ttl=120)
        cert = asyncssh.import_certificate(reply["certificate"])
        assert reply["serial"] == 1 and reply["ca"] == signer.active
        assert cert.principals == [LAB], "one server's, and nothing else"
        # asyncssh keeps these fields private; read them anyway, they are
        # exactly what sshd checks.
        assert cert._key_id == "Denis via tTerm Web" and cert._serial == 1
        now = time.time()
        assert cert._valid_after <= now <= cert._valid_before
        assert cert._valid_before - now <= 121, "lives about two minutes"
        assert cert.signing_key.get_fingerprint() == \
            signer.cas[signer.active].get_fingerprint(), "signed by our CA"
        # Only a terminal: no forwarding of agents, ports or X11, no rc.
        assert cert.options == {"permit-pty": True}, cert.options
        second = signer.sign(_public(), LAB, "x", ttl=60)
        assert second["serial"] == 2, "serials go up"
        restarted = Signer(tmp / "signer")
        restarted.load_or_create()
        assert restarted.sign(_public(), LAB, "x")["serial"] == 3, \
            "and survive a restart"
        # For now also the user tterm: the line of a server connected
        # before 0.0.12 takes only that, until the core puts in its own.
        old = signer.sign(_public(), "tterm", "x")
        assert asyncssh.import_certificate(old["certificate"]).principals == [
            "tterm"]
    finally:
        shutil.rmtree(tmp)


def test_signer_refuses_what_it_should():
    signer, tmp = _signer()
    try:
        for args, words in (
            ((_public(), "root", "x"), "principal"),
            ((_public(), "m-lab", "x"), "principal"),
            ((_public(), LAB + "\n", "x"), "principal"),
            ((_public(), f"{LAB},root", "x"), "principal"),
            ((_public(), LAB.upper(), "x"), "principal"),
            ((_public(), LAB, "x", 0), "ttl"),
            ((_public(), LAB, "x", 121), "ttl"),
            (("not a key", LAB, "x"), "public key"),
            ((_public(), LAB, ""), "key_id"),
            ((_public(), LAB, "x" * 201), "key_id"),
            ((_public(), LAB, "bad\nline"), "key_id"),
            ((_public(), LAB, "x", 120, "ca-nope"), "no such CA"),
        ):
            try:
                signer.sign(*args)
            except Refused as exc:
                assert words in str(exc), (words, str(exc))
            else:
                raise AssertionError(f"signed {args[1:]}")
    finally:
        shutil.rmtree(tmp)


def test_a_retired_ca_signs_nothing():
    signer, tmp = _signer()
    try:
        first = signer.active
        second = signer.create_ca("ca-20261018-beef")
        (tmp / "signer" / "active").write_text(f"{first}\n")
        # Retired by its id in the file and a restart: the other one signs.
        (tmp / "signer" / "retired").write_text(f"{first}  # may have leaked\n")
        again = Signer(tmp / "signer")
        again.load_or_create()
        assert again.active == second, again.active
        try:
            again.sign(_public(), LAB, "x", ca=first)
        except Refused as exc:
            assert "retired" in str(exc), exc
        else:
            raise AssertionError("signed with a retired CA")
        assert again.sign(_public(), LAB, "x")["ca"] == second
        assert {k["id"]: (k["active"], k["retired"])
                for k in again.public_keys()} == {
            first: (False, True), second: (True, False)}
        # Every CA retired: a new one, as on the very first start.
        (tmp / "signer" / "retired").write_text(f"{first}\n{second}\n")
        last = Signer(tmp / "signer")
        last.load_or_create()
        assert last.active not in (first, second) and len(last.cas) == 3
    finally:
        shutil.rmtree(tmp)


def test_signer_limits_signatures_per_minute():
    signer, tmp = _signer()
    old = service.PER_MINUTE
    service.PER_MINUTE = 3
    try:
        for _ in range(3):
            signer.sign(_public(), LAB, "x")
        try:
            signer.sign(_public(), LAB, "x")
        except Refused as exc:
            assert "too many" in str(exc)
        else:
            raise AssertionError("no limit")
    finally:
        service.PER_MINUTE = old
        shutil.rmtree(tmp)


def test_signer_keeps_its_own_journal():
    signer, tmp = _signer()
    try:
        signer.sign(_public(), LAB, "Denis via tTerm Web")
        lines = [json.loads(line) for line in
                 (tmp / "signer" / "journal.jsonl").read_text().splitlines()]
        assert [entry["event"] for entry in lines] == ["ca.created", "signed"]
        assert lines[1]["key_id"] == "Denis via tTerm Web"
        assert lines[1]["serial"] == 1 and lines[1]["principal"] == LAB
        journal = tmp / "signer" / "journal.jsonl"
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    finally:
        shutil.rmtree(tmp)


def test_signer_answers_only_proper_requests():
    signer, tmp = _signer()
    try:
        assert signer.answer(b"nonsense")["ok"] is False
        assert signer.answer(b"[1, 2]")["ok"] is False
        assert "unknown op" in signer.answer(b'{"op": "delete"}')["error"]
        assert signer.answer(b'{"op": "ping"}') == {"ok": True}
        keys = signer.answer(b'{"op": "keys"}')
        assert keys["ok"] and keys["cas"][0]["active"] is True
        # The private key is never part of any answer.
        assert "PRIVATE" not in json.dumps(keys)
    finally:
        shutil.rmtree(tmp)


async def test_core_gets_certificates_over_the_socket(h):
    async with h.signer() as (signer, client):
        assert stat.S_IMODE(os.stat(client.path).st_mode) == 0o600
        keys = await client.keys()
        assert [k["id"] for k in keys] == [signer.active]
        key, cert = await client.credentials(LAB, "Denis via tTerm Web")
        assert cert.key.export_public_key() == key.export_public_key()
        assert cert.principals == [LAB]
        try:
            await client.credentials("root", "x")
        except SignerRefused as exc:
            assert "principal" in str(exc)
        else:
            raise AssertionError("signed for root")


async def test_one_signer_per_socket(h):
    async with h.signer() as (signer, client):
        try:
            await serve(signer, client.path)
        except RuntimeError as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("two signers on one socket")


async def test_missing_signer_says_what_to_do():
    client = SignerClient(Path("/nonexistent/s.sock"), timeout=0.5)
    try:
        await client.keys()
    except SignerUnavailable as exc:
        assert "python -m tterm.dev" in str(exc), exc
    else:
        raise AssertionError("answered from nowhere")


async def test_on_the_server_the_core_group_reaches_the_socket():
    """The signer and the core are different users there: the socket is
    the group's too, and its folder lets that group in, no one else."""
    import grp
    group = grp.getgrgid(os.getgid()).gr_name
    tmp = Path(tempfile.mkdtemp(prefix="tt"))
    try:
        signer = Signer(tmp / "signer")
        signer.load_or_create()
        server = await serve(signer, tmp / "run" / "s.sock", group)
        try:
            folder = stat.S_IMODE((tmp / "run").stat().st_mode)
            sock = stat.S_IMODE((tmp / "run" / "s.sock").stat().st_mode)
            assert (folder, sock) == (0o750, 0o660), (oct(folder), oct(sock))
            assert (tmp / "run" / "s.sock").stat().st_gid == os.getgid()
            keys = await SignerClient(tmp / "run" / "s.sock").keys()
            assert keys and keys[0]["active"]
            assert stat.S_IMODE((tmp / "signer").stat().st_mode) == 0o700, \
                "the keys stay the signer's alone"
        finally:
            server.close()
            await server.wait_closed()
        # A folder the group may write to is refused.
        open_dir = tmp / "group-writes"
        open_dir.mkdir()
        os.chmod(open_dir, 0o770)
        try:
            service.private_dir(open_dir, group)
        except RuntimeError as exc:
            assert "closed to others" in str(exc)
        else:
            raise AssertionError("accepted a folder the group can write")
    finally:
        shutil.rmtree(tmp)


def test_socket_folder_must_be_private():
    tmp = Path(tempfile.mkdtemp(prefix="tt"))
    try:
        open_dir = tmp / "shared"
        open_dir.mkdir(mode=0o777)
        os.chmod(open_dir, 0o777)
        try:
            service.private_dir(open_dir)
        except RuntimeError as exc:
            assert "closed to others" in str(exc)
        else:
            raise AssertionError("accepted a folder others can write")
        service.private_dir(tmp / "mine")
        assert stat.S_IMODE((tmp / "mine").stat().st_mode) & 0o077 == 0
    finally:
        shutil.rmtree(tmp)
