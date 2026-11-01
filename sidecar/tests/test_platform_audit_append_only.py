"""The platform's audit trail (catalog.audit) is only ever added to: no code path of the platform
updates, deletes, truncates, drops or alters it (docs/platform-audit.md). Every statement in the
engine that names the table must be its CREATE TABLE, an INSERT or a SELECT, so a new statement
that changes rows fails here until someone decides it should."""

import re
from pathlib import Path

from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.central.core import Db

ADMIN, DEV = "chris@example.com", "dev@example.com"

PKG = Path(__file__).resolve().parents[1] / "fabric_desktop"
# How code names the table: db.t('catalog', 'audit'), catalog.audit in T-SQL, catalog__audit in SQLite.
NAMES = re.compile(r"""t\(\s*['"]catalog['"]\s*,\s*['"]audit['"]\s*\)|\bcatalog\.audit\b|\bcatalog__audit\b""")
VERB = re.compile(r"\b(CREATE TABLE|INSERT INTO|DELETE FROM|MERGE INTO|TRUNCATE TABLE|DROP TABLE|ALTER TABLE|UPDATE|FROM)\s*\{?[\w.]*$", re.I)
ALLOWED = {"CREATE TABLE", "INSERT INTO", "FROM"}


def _uses() -> list[tuple[str, int, str]]:
    found = []
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in NAMES.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
            found.append((path.relative_to(PKG).as_posix(), line, before))
    return found


def test_every_statement_on_the_audit_table_only_creates_adds_or_reads():
    sql = []
    for name, line, before in _uses():
        if '"""' in before or before.lstrip().startswith("#") or "exists(" in before:
            continue  # a docstring, a comment or an existence check, not a statement
        verb = VERB.search(before)
        if verb is None:
            continue  # prose in a message or a docstring line
        sql.append((name, line))
        assert verb.group(1).upper() in ALLOWED, f"{name}:{line} {verb.group(1)}s catalog.audit, which is append-only"
    assert len(sql) >= 3  # the CREATE, the INSERT in _audit and the reads


def test_no_upsert_or_generic_write_targets_the_audit_table():
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""_upsert\(\s*\w+\s*,\s*['"]catalog['"]\s*,\s*['"]audit['"]""", text), path


def test_the_platform_changes_audit_rows_only_by_inserting(tmp_path, monkeypatch):
    """The same at run time: a run of platform functions on a local platform sends no statement
    on catalog.audit other than CREATE TABLE, INSERT and SELECT."""
    seen = []
    real = Db.run

    def run(self, sql, params=()):
        seen.append(sql)
        return real(self, sql, params)
    monkeypatch.setattr(Db, "run", run)
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("whoami")
    dev = LocalPlatform(admin.path, lambda: DEV)
    req = dev.call("request", kind="source", target="erp", reason="needed")
    admin.call("decide_request", id=req["id"], approve=False, note="not now")
    again = dev.call("request", kind="source", target="erp", reason="needed now")
    dev.call("withdraw_request", id=again["id"])
    admin.call("save_domain", domain={"id": "sales", "name": "Sales"})
    admin.call("delete_domain", did="sales")
    touching = [q for q in seen if NAMES.search(q)]
    assert len(touching) >= 5
    for q in touching:
        assert re.match(r"\s*(CREATE TABLE|INSERT INTO|SELECT)\b", q, re.I), q
