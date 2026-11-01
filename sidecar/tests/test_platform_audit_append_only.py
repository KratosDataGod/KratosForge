"""The platform's audit trail (catalog.audit) is only ever added to: no code path of the platform
updates, deletes, merges, truncates, drops or alters it (docs/platform-audit.md). Every SQL
statement in the engine that names the table must be its CREATE TABLE, an INSERT or a SELECT, so
a new statement that changes rows fails here until someone decides it should."""

import ast
import re
import sqlite3
from pathlib import Path

from fabric_desktop.central.client import LocalPlatform

PKG = Path(__file__).resolve().parents[1] / "fabric_desktop"
ADMIN, DEV = "chris@example.com", "dev@example.com"
ALLOWED = ("CREATE", "INSERT", "SELECT")
# The table as written in SQL text: catalog.audit / [catalog].[audit] in T-SQL, catalog__audit in SQLite.
NAMED = re.compile(r"\bcatalog\]?\.\[?audit\b|\bcatalog__audit\b", re.I)


def _is_audit_t(node: ast.AST) -> bool:
    """db.t('catalog', 'audit'), however the receiver is named."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "t"
            and len(node.args) == 2 and all(isinstance(a, ast.Constant) for a in node.args)
            and [a.value for a in node.args] == ["catalog", "audit"])


def _text(node: ast.AST) -> str | None:
    """The SQL a string expression builds, with each {value} as a marker: f-strings, implicit and +
    concatenation across lines. None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            else:
                out.append(" <audit> " if _is_audit_t(part.value) else " <value> ")  # type: ignore[attr-defined]
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        return None if left is None or right is None else left + right
    return None


def _first_keyword(sql: str) -> str:
    m = re.match(r"\s*([A-Za-z]+)", sql)
    return m.group(1).upper() if m else ""


def problems(source: str, name: str = "<code>") -> list[str]:
    """Each place in ``source`` that names the audit table outside a CREATE, INSERT or SELECT."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = []
    for node in ast.walk(tree):
        if _is_audit_t(node):
            # Only allowed as a {value} inside the SQL text itself; kept in a name, it could be used anywhere.
            top = parents.get(parents.get(node))  # FormattedValue -> JoinedStr
            while isinstance(parents.get(top), ast.BinOp):
                top = parents.get(top)
            sql = _text(top) if isinstance(top, (ast.JoinedStr, ast.BinOp)) else None
            if sql is None:
                found.append(f"{name}:{node.lineno} keeps db.t('catalog', 'audit') outside a SQL string")
            elif _first_keyword(sql) not in ALLOWED:
                found.append(f"{name}:{node.lineno} {_first_keyword(sql)} on catalog.audit")
        elif isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)) and not isinstance(parents.get(node), (ast.JoinedStr, ast.BinOp)):
            sql = _text(node)
            if sql and NAMED.search(sql) and re.match(r"\s*(WITH|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER)\b", sql, re.I):
                found.append(f"{name}:{node.lineno} {_first_keyword(sql)} on catalog.audit")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_upsert"
              and [getattr(a, "value", None) for a in node.args[1:3]] == ["catalog", "audit"]):
            found.append(f"{name}:{node.lineno} upserts catalog.audit")
    return found


def test_every_statement_on_the_audit_table_only_creates_adds_or_reads():
    found, uses = [], 0
    for path in PKG.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        uses += source.count("'catalog', 'audit'") + source.count('"catalog", "audit"')
        found += problems(source, path.relative_to(PKG).as_posix())
    assert not found
    assert uses >= 4  # the CREATE, the INSERT in _audit and the two reads


def test_a_delete_split_across_lines_is_caught():
    assert problems('db.run(f"DELETE "\n       f"FROM {db.t(\'catalog\', \'audit\')} WHERE id < ?", (1,))')
    assert problems('db.run("DELETE " +\n       f"FROM {db.t(\'catalog\', \'audit\')}")')


def test_the_table_kept_in_a_name_is_caught():
    assert problems('table = db.t("catalog", "audit")\ndb.run(f"DELETE FROM {table}")')


def test_plain_sql_text_and_upserts_are_caught():
    assert problems('db.run("UPDATE [catalog].[audit] SET by_user = NULL")')
    assert problems('db.run(\'DELETE FROM "catalog__audit"\')')
    assert problems('_upsert(db, "catalog", "audit", "id", {})')
    assert problems('db.run("WITH old AS (SELECT id FROM catalog.audit) DELETE FROM catalog.audit WHERE id IN (SELECT id FROM old)")')


def test_the_allowed_statements_pass():
    assert not problems('db.run(f"INSERT INTO {db.t(\'catalog\', \'audit\')} (at) VALUES (?)", (1,))')
    assert not problems('db.rows(f"SELECT {top}* FROM {db.t(\'catalog\', \'audit\')} ORDER BY id")')
    assert not problems('db.run(f"CREATE TABLE {db.t(\'catalog\', \'audit\')} (id int)")')
    assert not problems('"""Writes catalog.audit, never deletes it."""')


def test_the_platform_changes_audit_rows_only_by_inserting(tmp_path, monkeypatch):
    """The same at run time: every statement SQLite runs for a local platform, however the code
    sends it, while it takes, declines and withdraws requests and saves and deletes a domain."""
    seen: list[str] = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn
    monkeypatch.setattr(sqlite3, "connect", connect)
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("whoami")
    dev = LocalPlatform(admin.path, lambda: DEV)
    req = dev.call("request", kind="source", target="erp", reason="needed")
    admin.call("decide_request", id=req["id"], approve=False, note="not now")
    again = dev.call("request", kind="source", target="erp", reason="needed now")
    dev.call("withdraw_request", id=again["id"])
    admin.call("save_domain", domain={"id": "sales", "name": "Sales"})
    admin.call("delete_domain", did="sales")
    touching = [q for q in seen if NAMED.search(q)]
    assert len(touching) >= 5
    for q in touching:
        assert _first_keyword(q) in ALLOWED, q
