"""Fuzz the audit trail's JSONL files (audit.py, audit_chain.py; integrations/audit.py checks its trail with the
same verify): a torn last line, flipped bytes, invalid UTF-8, NUL bytes, CRLF, a huge line, fields of the wrong
JSON type, a field written twice, and lines reordered, deleted, duplicated or inserted, in either month's file.

Whatever the damage, ``entries()`` never raises and shows each line it can't read as a placeholder naming the
file and line, ``verify()`` never raises and never calls a changed trail intact, and the next write still goes
on and doesn't hide it. Only a change that leaves every line's content as it was (CRLF line ends, a space
between fields) may still check out. Seeded, so a failure repeats."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from fabric_desktop import audit, audit_chain
from fabric_desktop.audit import AuditLog

from tests.settings_fuzz import ODD_VALUES

CASES = 150
FIELDS = ("schema", "at", "action", "outcome", "seconds", "who", "target", "prev", "hash")


def _trail(folder: Path) -> None:
    """Eight lines over two months' files, written as the app writes them."""
    log = AuditLog(folder)
    for i in range(8):
        entry = {"schema": audit.SCHEMA, "at": f"2026-{10 + i // 4}-0{i % 4 + 1}T09:00:00.000+00:00", "action": "meta_run",
                 "outcome": "ok", "seconds": 0.1, "who": "a@contoso.example", "tenant": "", "host": log.host,
                 "os_user": log.os_user, "app": "desktop", "version": "1", "compute": "this PC",
                 "target": {"env": "dev", "n": i}}
        audit_chain.append(folder, entry)


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    folder = tmp_path_factory.mktemp("trail") / "audit"
    _trail(folder)
    assert audit_chain.verify(folder) == {"lines": 8, "ok": True, "problems": []}
    assert [p.name for p in audit_chain.files(folder)] == ["audit-2026-10.jsonl", "audit-2026-11.jsonl"]
    return folder


def _pairs(line: str):
    """A line's content, field order and repeats included; the text itself when it isn't JSON."""
    try:
        return json.loads(line, object_pairs_hook=lambda pairs: pairs)
    except (ValueError, RecursionError):
        return line


def _content(folder: Path) -> list:
    return [_pairs(line) for p in audit_chain.files(folder)
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def _mutate(rng: random.Random, files: dict[str, bytes]) -> str:
    """Damages ``files`` ({name: bytes}) in place; returns what was done."""
    name = rng.choice(sorted(files))
    raw = files[name]
    lines = raw.splitlines(keepends=True)
    kind = rng.choice(("flip", "flips", "truncate", "invalid utf-8", "nul", "crlf", "huge", "field", "reseal",
                       "duplicate key", "swap", "delete", "copy", "insert", "blank", "lone cr"))
    if kind in ("flip", "flips"):
        b = bytearray(raw)
        for _ in range(1 if kind == "flip" else rng.randint(2, 6)):
            i = rng.randrange(len(b))
            b[i] = (b[i] + rng.randrange(1, 256)) % 256
        files[name] = bytes(b)
    elif kind == "truncate":
        files[name] = raw[:rng.randrange(len(raw))]
    elif kind == "invalid utf-8":
        i = rng.randrange(len(raw))
        files[name] = raw[:i] + rng.choice([b"\xff", b"\xc3", b"\xed\xa0\x80", b"\x80\x80"]) + raw[i:]
    elif kind == "nul":
        i = rng.randrange(len(raw))
        files[name] = raw[:i] + b"\x00" * rng.randint(1, 4) + raw[i:]
    elif kind == "crlf":
        files[name] = raw.replace(b"\n", b"\r\n")
    elif kind == "lone cr":
        i = rng.randrange(len(raw))
        files[name] = raw[:i] + b"\r" + raw[i:]
    elif kind == "huge":
        i = rng.randrange(len(lines) + 1)
        big = rng.choice([b"x" * 3_000_000, json.dumps({"action": "y" * 2_000_000}).encode(), b"[" * 200_000])
        files[name] = b"".join(lines[:i] + [big + b"\n"] + lines[i:])
    elif kind in ("field", "reseal", "duplicate key"):
        i = rng.randrange(len(lines))
        entry = json.loads(lines[i])
        field = rng.choice(FIELDS)
        if kind == "duplicate key":  # a second copy of a field, before or after the one the hash covers
            pair = json.dumps({field: rng.choice(ODD_VALUES[:12])})[1:-1].encode()
            body = lines[i].rstrip(b"\r\n")
            lines[i] = (b"{" + pair + b", " + body[1:] if rng.random() < 0.5 else body[:-1] + b", " + pair + b"}") + b"\n"
        else:
            entry[field] = rng.choice(ODD_VALUES + [[1], {"x": None}, 10**400 if False else 10**30])
            if kind == "reseal" and field not in ("prev", "hash"):  # its own hash recomputed, as a forger would
                entry.pop("hash", None)
                text = audit_chain.seal(entry.get("prev", ""), entry)
            else:
                text = json.dumps(entry, default=str)
            lines[i] = text.encode() + b"\n"
        files[name] = b"".join(lines)
    elif kind == "swap" and len(lines) > 1:
        i, j = rng.sample(range(len(lines)), 2)
        lines[i], lines[j] = lines[j], lines[i]
        files[name] = b"".join(lines)
    elif kind == "delete":
        del lines[rng.randrange(len(lines))]
        files[name] = b"".join(lines)
    elif kind == "copy":
        other = rng.choice(b"".join(files.values()).splitlines(keepends=True))
        lines.insert(rng.randrange(len(lines) + 1), other)
        files[name] = b"".join(lines)
    elif kind == "insert":  # a forged line, sealed onto the line before it
        # Not after the newest line: a line chained onto the end is what a write does, and one whose head.json
        # update is still to come looks the same (audit_chain's documented limit: not an attacker who can write).
        i = rng.randrange(len(lines) + (name != max(files)))
        prev = json.loads(lines[i - 1])["hash"] if i else audit_chain.GENESIS
        forged = {"schema": audit.SCHEMA, "at": "2026-11-02T09:30:00.000+00:00", "action": "meta_promote", "outcome": "ok"}
        lines.insert(i, audit_chain.seal(prev, forged).encode() + b"\n")
        files[name] = b"".join(lines)
    else:  # blank lines: no content changed
        files[name] = raw.replace(b"\n", b"\n\n", 1)
    return f"{kind} in {name}"


def _check_entries(folder: Path, label: str) -> None:
    rows = list(AuditLog(folder).entries(unreadable=True))
    assert all(isinstance(r, dict) for r in rows), label
    texts = [line for p in audit_chain.files(folder)
             for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    assert len(rows) == len(texts), label  # every line shows, read or as a placeholder
    for row in rows:
        if row.get("action") == audit.UNREADABLE:
            assert row["file"].startswith("audit-") and isinstance(row["line"], int), label
            assert f"{row['file']} line {row['line']}" in row["error"], label
    AuditLog(folder).recent(limit=5)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_any_damage_to_the_trail_is_reported_and_never_raises(pristine, tmp_path, seed):
    rng = random.Random(seed)
    before = _content(pristine)
    for case in range(CASES):
        folder = tmp_path / f"c{case}"
        shutil.copytree(pristine, folder)
        files = {p.name: p.read_bytes() for p in audit_chain.files(folder)}
        for _ in range(rng.choice((1, 1, 1, 2))):
            what = _mutate(rng, files)
        for n, raw in files.items():
            (folder / n).write_bytes(raw)
        label = f"seed {seed} case {case}: {what}"
        result = audit_chain.verify(folder)
        assert set(result) >= {"lines", "ok", "problems"} and isinstance(result["problems"], list), label
        changed = _content(folder) != before
        if changed:
            assert result["ok"] is False and result["problems"], label
        _check_entries(folder, label)
        out = tmp_path / "export" / f"c{case}.jsonl"
        audit_chain.export(folder, out, replace=True)  # a damaged trail is copied as it is, without a crash
        assert isinstance(audit_chain.verify_export(out, folder)["problems"], list), label
        # The next write goes on, and doesn't make a changed trail check out.
        AuditLog(folder).record("meta_run", "ok", 0.1, {"env": "dev"})
        after = audit_chain.verify(folder)
        if changed:
            assert after["ok"] is False, label
        shutil.rmtree(folder)


def test_a_field_written_twice_is_reported_whichever_copy_the_hash_covers(pristine, tmp_path):
    # Regression: json keeps the last copy, so a line with a different first copy of a field checked out while
    # a reader that keeps the first copy (many log stores do) saw other content than the hash covers.
    for where in ("first", "last"):
        folder = tmp_path / where
        shutil.copytree(pristine, folder)
        path = audit_chain.files(folder)[-1]
        lines = path.read_bytes().splitlines(keepends=True)
        body = lines[1].rstrip(b"\n")
        lines[1] = (b'{"action": "nothing_happened", ' + body[1:] if where == "first"
                    else body[:-1] + b', "action": "meta_run"}') + b"\n"
        path.write_bytes(b"".join(lines))
        result = audit_chain.verify(folder)
        assert result["ok"] is False and f"{path.name} line 2: not an audit line" in result["problems"], result


@pytest.mark.parametrize("prev", [None, 5, [], {"a": 1}, True])
def test_a_line_whose_prev_isnt_text_is_reported_not_a_crash(pristine, tmp_path, prev):
    # Regression: hash_entry added the line's prev to its body, so a prev of another JSON type raised TypeError.
    path = audit_chain.files(pristine)[-1]
    shutil.copytree(pristine, tmp_path / "t")
    path = tmp_path / "t" / path.name
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[2])
    entry["prev"] = prev
    lines[2] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = audit_chain.verify(tmp_path / "t")
    assert result["ok"] is False
    assert f"{path.name} line 3: a line before it is missing or changed" in result["problems"]


@pytest.mark.parametrize("lost", ["x", [1, 2], 7, True])
def test_a_head_file_with_a_lost_mark_of_another_type_is_not_a_crash(pristine, tmp_path, lost):
    # Regression: verify() called .get on head.json's "lost" whatever its type.
    shutil.copytree(pristine, tmp_path / "t")
    head = json.loads((tmp_path / "t" / audit_chain.HEAD).read_text(encoding="utf-8"))
    (tmp_path / "t" / audit_chain.HEAD).write_text(json.dumps({**head, "lost": lost}), encoding="utf-8")
    assert audit_chain.verify(tmp_path / "t")["lines"] == 8


def test_a_line_nested_too_deep_is_named_not_a_crash(pristine, tmp_path):
    # Regression: json.loads raises RecursionError, not ValueError, for a line nested this deep.
    shutil.copytree(pristine, tmp_path / "t")
    path = audit_chain.files(tmp_path / "t")[-1]
    with path.open("ab") as f:
        f.write(b"[" * 200_000 + b"\n")
    rows = list(AuditLog(tmp_path / "t").entries(unreadable=True))
    assert rows[-1]["action"] == audit.UNREADABLE and rows[-1]["line"] == 5
    assert f"{path.name} line 5: not an audit line" in audit_chain.verify(tmp_path / "t")["problems"]
    AuditLog(tmp_path / "t").record("meta_run", "ok", 0.1, {})  # the next write still goes on
    audit_chain.export(tmp_path / "t", tmp_path / "copy.jsonl")
