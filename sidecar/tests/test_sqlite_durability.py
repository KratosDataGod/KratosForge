"""Every SQLite store the app keeps opens its file through sqlitefile, so each one commits durably
(synchronous=full): a job that says succeeded, an approval, a key or a notice marked sent is still
there after a power cut. docs/quality/sqlite-stores.md lists the stores."""

import ast
import sqlite3
from pathlib import Path

import pytest

from fabric_desktop import sqlitefile

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"

# The only files that may call sqlite3.connect themselves, and why.
ALLOWED = {
    "sqlitefile.py": "the helper every store opens its file with",
    "backup.py": "reads other stores' files read-only to copy them into a backup, and checks a restored one",
    "engines/local_sql.py": "the person's own SQL against a database they chose, not a file the app keeps",
}
EXERCISES = "academy/packs/"  # exercise code a learner runs in the sandbox, against :memory:


def _connect_calls(tree: ast.AST) -> list[int]:
    """Lines that call sqlite3.connect, under any name sqlite3 or its connect is imported as."""
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "sqlite3"}
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            functions |= {a.asname or a.name for a in node.names if a.name == "connect"}
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "connect" and isinstance(f.value, ast.Name)
                and f.value.id in modules) or (isinstance(f, ast.Name) and f.id in functions):
            lines.append(node.lineno)
    return lines


def test_no_store_opens_its_file_without_the_helper():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in ALLOWED or rel.startswith(EXERCISES):
            continue
        found += [f"{rel}:{line}" for line in _connect_calls(ast.parse(path.read_text(encoding="utf-8")))]
    assert found == [], ("open the file with sqlitefile.connect or sqlitefile.transaction, which commit "
                         "durably, or add the file to ALLOWED with the reason it isn't a store: " + ", ".join(found))


def test_the_guard_sees_every_way_of_calling_connect():
    tree = ast.parse("import sqlite3\nimport sqlite3 as s\nfrom sqlite3 import connect as c\n"
                     "sqlite3.connect('a')\ns.connect('b')\nc('c')\nother.connect('d')\n")
    assert _connect_calls(tree) == [4, 5, 6]


def test_the_allowed_files_still_exist():
    assert all((PACKAGE / rel).is_file() for rel in ALLOWED)


@pytest.mark.parametrize("opener", [sqlitefile.connect, sqlitefile.transaction])
def test_both_helpers_commit_durably(tmp_path, monkeypatch, opener):
    real = sqlite3.connect

    def connect(*a, **k):  # whatever this SQLite was built to default to
        db = real(*a, **k)
        db.execute("pragma synchronous=off")
        return db
    monkeypatch.setattr(sqlite3, "connect", connect)
    with opener(tmp_path / "s.sqlite") as db:
        assert db.execute("pragma synchronous").fetchone()[0] == 2  # FULL
        db.execute("create table t (x)")
        db.execute("insert into t values (1)")
    with sqlitefile.connect(tmp_path / "s.sqlite") as db:
        assert [tuple(r) for r in db.execute("select x from t")] == [(1,)]


class Recorded(sqlite3.Connection):
    """A connection that notes its synchronous setting when it closes."""
    seen: list[int] = []

    def close(self):
        try:
            Recorded.seen.append(self.execute("pragma synchronous").fetchone()[0])
        except sqlite3.ProgrammingError:
            pass
        super().close()


def _stores():
    from fabric_desktop.central.client import LocalPlatform
    from fabric_desktop.integrations.store import IntegrationStore
    from fabric_desktop.jobqueue import JobQueue
    from fabric_desktop.optimize.outbox import LOCAL_EXECUTIONS
    from fabric_desktop.optimize.outbox import Outbox as OptimizeOutbox
    from fabric_desktop.workloads.notify import Outbox as NoticeOutbox
    from fabric_desktop.workloads.platform_notify import Ledger
    from fabric_desktop.workloads.store import ManagementStore
    return {
        "job queue and run history": lambda d: JobQueue(d / "queue.sqlite3").enqueue("meta", {"env": "dev"}),
        "integrations and their approvals": lambda d: IntegrationStore(d / "integrations.sqlite").add_client("t"),
        "platform keys and rules": lambda d: LocalPlatform(d / "platform-dev.sqlite", lambda: "chris"),
        "workloads, actions and audit not yet sent": lambda d: ManagementStore(d / "workloads.sqlite").assign(
            "nb:a", {"team": "Sales"}, by="chris"),
        "Teams notices waiting": lambda d: NoticeOutbox(d / "alerts.sqlite3").add("t", ["line"], ""),
        "notices already sent": lambda d: Ledger(d / "platform_outbox.sqlite"),
        "Optimize results waiting": lambda d: OptimizeOutbox(d / "outbox.sqlite").add("lh", LOCAL_EXECUTIONS, {"rows": [{}]}),
    }


@pytest.mark.parametrize("name", list(_stores()))
def test_every_store_commits_durably(tmp_path, monkeypatch, name):
    real = sqlite3.connect
    Recorded.seen = []
    def connect(*a, **k):  # as on a SQLite built to default to synchronous=normal, which can lose commits
        db = real(*a, factory=Recorded, **k)
        db.execute("pragma synchronous=normal")
        return db
    monkeypatch.setattr(sqlite3, "connect", connect)
    _stores()[name](tmp_path)
    assert Recorded.seen and set(Recorded.seen) == {2}, Recorded.seen
