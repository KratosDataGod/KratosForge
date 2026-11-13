"""Upgrading from the last Stable builds keeps every SQLite store and its rows.

tests/fixtures/store_schemas/stable-<build>.json is each store's schema as that build creates it.
Each test makes the old file from that schema, puts a row in every table, then opens it with this
build's code: every old table and column must still be there, with the rows.

When a new Stable build is promoted, add its schema and drop the oldest of three:

    git worktree add ../kf-<build> <build>
    cd ../kf-<build>/sidecar && PYTHONPATH=$PWD python <this repo>/sidecar/tests/test_store_upgrades.py \
        > <this repo>/sidecar/tests/fixtures/store_schemas/stable-<build>.json

Running this file prints the schemas of the code on sys.path, which is how the fixtures were made.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "store_schemas"


def openers() -> dict[str, Callable[[Path], Any]]:
    """Each store, by its path under the data folder (the agent's under ``agent/``), and how it opens.
    Imports are guarded so the same list runs on older code, which may not have every store."""
    out: dict[str, Callable[[Path], Any]] = {}
    try:
        from fabric_desktop.integrations.store import IntegrationStore
        out["integrations/integrations.sqlite"] = IntegrationStore
    except ImportError:
        pass
    try:
        from fabric_desktop.workloads.store import ManagementStore
        out["workloads.sqlite"] = ManagementStore
    except ImportError:
        pass
    try:
        from fabric_desktop.central.client import LocalPlatform
        out["platform/platform-dev.sqlite"] = LocalPlatform
        out["platform/keys.sqlite"] = LocalPlatform
    except ImportError:
        pass
    try:
        from fabric_desktop.workloads.notify import Outbox as AlertOutbox
        out["alerts_outbox.sqlite"] = AlertOutbox
        from fabric_desktop.workloads.platform_notify import Ledger
        out["platform_outbox.sqlite"] = lambda p: (AlertOutbox(p), Ledger(p))
    except ImportError:
        pass
    try:
        from fabric_desktop.optimize.outbox import Outbox as OptimizeOutbox
        out["optimize/outbox.sqlite"] = OptimizeOutbox
    except ImportError:
        pass
    try:
        from fabric_desktop.jobqueue import JobQueue
        out["agent/queue.sqlite3"] = JobQueue
    except ImportError:
        pass
    return out


def schemas(root: Path) -> dict[str, list[str]]:
    """Every store opened fresh under ``root``, and the SQL of what it made."""
    found = {}
    for rel, open_ in openers().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        open_(path)
        db = sqlite3.connect(path)
        found[rel] = [r[0] for r in db.execute(
            "select sql from sqlite_master where sql is not null and name not like 'sqlite_%' order by type desc, name")]
        db.close()
    return found


def _value(kind: str, n: int) -> Any:
    kind = kind.upper()
    if "INT" in kind:
        return n
    if any(k in kind for k in ("REAL", "FLOA", "DOUB")):
        return float(n)
    if "BLOB" in kind:
        return b"x"
    return f"upgrade-{n}"


def _tables(db: sqlite3.Connection) -> dict[str, list[str]]:
    names = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    return {t: [c[1] for c in db.execute(f'pragma table_info("{t}")')] for t in names}


def _fill(db: sqlite3.Connection) -> dict[str, int]:
    """One row in every table: every column set where the table allows it, else only what it must have."""
    counts = {}
    for table in _tables(db):
        cols = list(db.execute(f'pragma table_info("{table}")'))
        for keep in (lambda c: True, lambda c: c[3] and c[4] is None):  # all columns, then NOT NULL without default
            chosen = [c for c in cols if keep(c)]
            try:
                if chosen:
                    names = ", ".join(f'"{c[1]}"' for c in chosen)
                    db.execute(f'insert into "{table}" ({names}) values ({", ".join("?" * len(chosen))})',
                               [_value(c[2], 1) for c in chosen])
                else:
                    db.execute(f'insert into "{table}" default values')
                break
            except sqlite3.IntegrityError:
                continue
        counts[table] = db.execute(f'select count(*) from "{table}"').fetchone()[0]
    db.commit()
    return counts


def _cases() -> list[tuple[str, str]]:
    return [(f.stem, rel) for f in sorted(FIXTURES.glob("stable-*.json")) for rel in json.loads(f.read_text())]


def test_there_are_schemas_for_the_last_two_stable_builds():
    builds = sorted(f.stem for f in FIXTURES.glob("stable-*.json"))
    assert len(builds) >= 2, builds
    for build in builds:
        stores = json.loads((FIXTURES / f"{build}.json").read_text())
        assert {"integrations/integrations.sqlite", "workloads.sqlite", "platform/keys.sqlite"} <= set(stores), build


@pytest.mark.parametrize(("build", "rel"), _cases())
def test_a_store_from_a_stable_build_opens_with_its_tables_columns_and_rows(tmp_path, build, rel):
    if rel not in openers():
        pytest.fail(f"{rel} (from {build}) has no opener here: a store was removed or renamed without a migration")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    for sql in json.loads((FIXTURES / f"{build}.json").read_text())[rel]:
        db.execute(sql)
    before_cols = _tables(db)
    before_rows = _fill(db)
    db.close()

    for _ in range(2):  # the upgrade, then an ordinary open of the upgraded file
        opened = openers()[rel](path)
        assert not getattr(opened, "set_aside", None), f"{rel} from {build} was moved aside as damaged"

    db = sqlite3.connect(path)
    assert db.execute("pragma quick_check").fetchone()[0] == "ok"
    after_cols = _tables(db)
    for table, cols in before_cols.items():
        assert table in after_cols, f"{rel}: table {table} from {build} is gone"
        assert set(cols) <= set(after_cols[table]), f"{rel}.{table}: columns gone: {set(cols) - set(after_cols[table])}"
        rows = db.execute(f'select count(*) from "{table}"').fetchone()[0]
        assert rows >= before_rows[table], f"{rel}.{table}: {before_rows[table]} rows from {build}, {rows} after"
    db.close()


def test_a_fresh_store_has_everything_an_upgraded_one_has(tmp_path):
    """The other way round: an upgrade ends where a new install starts (no table only new installs get)."""
    fresh = schemas(tmp_path / "fresh")
    newest = sorted(FIXTURES.glob("stable-*.json"))[-1]
    for rel, sqls in json.loads(newest.read_text()).items():
        path = tmp_path / "old" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        for sql in sqls:
            db.execute(sql)
        db.close()
        openers()[rel](path)
        db = sqlite3.connect(path)
        upgraded = _tables(db)
        db.close()
        db = sqlite3.connect(tmp_path / "fresh" / rel)
        new = _tables(db)
        db.close()
        assert set(new) == set(upgraded), f"{rel}: {set(new) ^ set(upgraded)}"
        for table, cols in new.items():
            assert set(cols) == set(upgraded[table]), f"{rel}.{table}: {set(cols) ^ set(upgraded[table])}"
    assert fresh


if __name__ == "__main__":
    json.dump(schemas(Path(tempfile.mkdtemp())), sys.stdout, indent=1, sort_keys=True)
