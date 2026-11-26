"""The SQL and Python examples in docs/ run on this PC and give what the page shows, or say they're
illustrative (see docexamples.py for the markers and the claim lists)."""

from __future__ import annotations

import re
from collections import Counter

import pytest
from docexamples import (CLAIM_PAGES, PRACTICE_PAGES, Claim, Statement, fenced_blocks, inline_claims, inline_statements,
                         render, same)
from docexamples_more import CLAIMS as MORE_CLAIMS
from docexamples_more import ILLUSTRATIVE as MORE_ILLUSTRATIVE
from docexamples_more import STATEMENTS as MORE_STATEMENTS
from docexamples_pages import CLAIMS as PAGE_CLAIMS
from docexamples_pages import ILLUSTRATIVE as PAGE_ILLUSTRATIVE
from docexamples_pages import STATEMENTS as PAGE_STATEMENTS
from docexamples_sqlcompat import CLAIMS as COMPAT_CLAIMS
from docexamples_sqlcompat import ILLUSTRATIVE as COMPAT_ILLUSTRATIVE
from docexamples_sqlcompat import STATEMENTS as COMPAT_STATEMENTS

CLAIMS = COMPAT_CLAIMS + PAGE_CLAIMS + MORE_CLAIMS
ILLUSTRATIVE = {**COMPAT_ILLUSTRATIVE, **PAGE_ILLUSTRATIVE, **MORE_ILLUSTRATIVE}
STATEMENTS = COMPAT_STATEMENTS + PAGE_STATEMENTS + MORE_STATEMENTS
BLOCKS = [b for b in fenced_blocks() if b.page not in PRACTICE_PAGES]


# ---- fenced blocks

def test_every_fenced_example_says_whether_it_runs():
    unmarked = [f"{b.page}:{b.line}" for b in BLOCKS if b.marker is None]
    assert not unmarked, ("add <!-- example: runs --> or <!-- example: illustrative --> on the line before "
                          f"each of these blocks: {unmarked}")


# ---- inline claims

def _found():
    return [f for page in CLAIM_PAGES for f in inline_claims(page)]


def _found_statements():
    return [f for page in CLAIM_PAGES for f in inline_statements(page)]


def test_every_inline_claim_is_run_or_illustrative():
    known = {(c.page, c.expr) for c in CLAIMS} | {(s.page, s.sql) for s in STATEMENTS} | set(ILLUSTRATIVE)
    missing = [f"{f.page}:{f.line} `{f.expr}` -> {f.want or '(statement)'}" for f in _found() + _found_statements()
               if (f.page, f.expr) not in known]
    assert not missing, "add a Claim or an ILLUSTRATIVE entry for each:\n" + "\n".join(missing)


def test_no_stale_entries():
    found = {(f.page, f.expr) for f in _found() + _found_statements()}
    stale = [k for k in [(c.page, c.expr) for c in CLAIMS] + [(s.page, s.sql) for s in STATEMENTS] + list(ILLUSTRATIVE)
             if k not in found]
    assert not stale, f"no longer on the page (remove them): {stale}"
    dupes = [k for k, n in Counter((c.page, c.expr) for c in CLAIMS).items() if n > 1 and k in ILLUSTRATIVE]
    assert not dupes


def _want(claim: Claim) -> str:
    if claim.want is not None:
        return claim.want
    wants = {f.want for f in inline_claims(claim.page) if f.expr == claim.expr}
    assert len(wants) == 1, f"{claim.page} `{claim.expr}`: give want=, the page shows {sorted(wants)}"
    return wants.pop()


@pytest.fixture(scope="module")
def local(tmp_path_factory):
    from fabric_desktop.localdata import LocalData
    data = LocalData(tmp_path_factory.mktemp("docs") / "local-data")
    made = iter(range(1, 10_000))
    yield lambda: data.engine(data.create(f"Docs examples {next(made)}")["id"])
    data.close_all()


def _rows(path: str, query: str, setup: tuple[str, ...], local) -> list[list]:
    """Run ``query`` (after ``setup``) as the page says it runs, and return its rows."""
    if path == "notebook":
        from fabric_desktop import sql
        for stmt in setup:
            sql.run(stmt)
        return [list(r.values()) for r in sql.run(query).to_pylist()]
    if path.startswith("local-"):
        engine = local()
        dialect = path.removeprefix("local-")
        for stmt in setup:
            engine.execute(stmt, dialect=dialect)
        return engine.execute(query, dialect=dialect)["rows"]
    raise AssertionError(f"no runner for path {path!r}")


def _value(claim: Claim, local) -> str:
    target = f"typeof({claim.expr})" if claim.typeof else claim.expr
    return render(_rows(claim.path, claim.query or f"SELECT {target} AS v", claim.setup, local)[0][0])


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: f"{c.page}:{c.expr}"[:90])
def test_inline_claim_gives_what_the_page_shows(claim: Claim, local):
    got, want = _value(claim, local), _want(claim)
    assert same(got, want), f"{claim.page}: `{claim.expr}` ({claim.path}) gives {got}, the page says {want}"


@pytest.mark.parametrize("stmt", STATEMENTS, ids=lambda s: f"{s.page}:{s.sql}"[:90])
def test_inline_statement_runs_or_is_refused_as_the_page_says(stmt: Statement, local):
    if not stmt.refused:
        _rows(stmt.path, stmt.sql, stmt.setup, local)
        return
    with pytest.raises(Exception) as err:
        _rows(stmt.path, stmt.sql, stmt.setup, local)
    text = str(err.value)
    assert "Catalog Error" not in text and "Parser Error" not in text and "Binder Error" not in text, text
    if stmt.match:
        assert re.search(stmt.match, text), text


# ---- fenced blocks marked runs: each has a test here, keyed by its page and first line

def _block(page: str, first: str):
    found = [b for b in BLOCKS if b.page == page and b.first == first]
    assert len(found) == 1, f"{page}: no single block starting {first!r}"
    assert found[0].marker == "runs", f"{page}:{found[0].line} is run here: mark it <!-- example: runs -->"
    return found[0]


RUN_HERE = {
    ("decision-patterns.md", "# Runs on this PC with DuckDB; nothing is read from or written to Fabric."),
    ("decision-patterns.md", "# Runs on this PC with DuckDB: how long a group-by over 10,000,000 rows takes here. It's a"),
    ("local-data.md", "-- Create one (fails if it exists; IF NOT EXISTS skips it, OR REPLACE gives it a new schema)"),
    ("local-data.md", "MERGE INTO customers AS t USING updates AS s ON t.id = s.id"),
}


def test_every_block_marked_runs_has_a_test_here():
    marked = {(b.page, b.first) for b in BLOCKS if b.marker == "runs"}
    assert marked == RUN_HERE, f"marked runs without a test: {marked - RUN_HERE}; tested but not marked: {RUN_HERE - marked}"


def _exec(code: str) -> str:
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<docs example>", "exec"), {})  # noqa: S102  # the docs' own example, run as a reader would
    return out.getvalue()


def test_decision_pattern_incremental_load_misses_the_late_row():
    block = _block("decision-patterns.md", "# Runs on this PC with DuckDB; nothing is read from or written to Fabric.")
    assert "[(4,)]" in block.code
    assert _exec(block.code).strip() == "Rows the incremental run missed: [(4,)]"


def test_decision_pattern_group_by_timing_runs():
    block = _block("decision-patterns.md",
                   "# Runs on this PC with DuckDB: how long a group-by over 10,000,000 rows takes here. It's a")
    assert _exec(block.code).startswith("1,000 groups in ")


def _orders(engine):
    engine.execute("CREATE TABLE dbo.orders AS SELECT * FROM (VALUES (1, DATE '2024-05-01', 10.0), "
                   "(2, DATE '2025-02-01', 20.0)) v(id, order_date, amount)")
    engine.execute("CREATE TABLE dbo.new_orders AS SELECT * FROM (VALUES (3, DATE '2025-03-01', 30.0, 2025)) "
                   "v(id, order_date, amount, year)")


def _statements(code: str) -> list[str]:
    return [s for s in re.sub(r"--[^\n]*", "", code).split(";") if s.strip()]


def test_local_data_delta_table_statements_run_one_at_a_time(local):
    block = _block("local-data.md", "-- Create one (fails if it exists; IF NOT EXISTS skips it, OR REPLACE gives it a new schema)")
    engine = local()
    _orders(engine)
    for stmt in _statements(block.code):
        engine.execute(stmt)
    # orders 1 and 2, new order 3 twice (once by position, once by name); the overwrite keeps 2025 on
    assert sorted(r[0] for r in engine.execute("SELECT id FROM sales")["rows"]) == [2, 3, 3]


@pytest.mark.xfail(strict=True, reason="a script that creates a Delta table and then INSERT OVERWRITEs it stops with "
                                       "DuckDB's parser error: each statement is read before the one that makes the "
                                       "table has run (localdata/engine.py _statements; WR-143)")
def test_local_data_delta_table_statements_run_as_one_script(local):
    block = _block("local-data.md", "-- Create one (fails if it exists; IF NOT EXISTS skips it, OR REPLACE gives it a new schema)")
    engine = local()
    _orders(engine)
    engine.execute(block.code)


_DUCKDB_200 = __import__("duckdb").__version__ == "2.0.0"  # 2.0.1 merges into a Delta table again, as 1.x does


@pytest.mark.parametrize("dialect", [
    pytest.param("duckdb", marks=pytest.mark.xfail(_DUCKDB_200, strict=True, reason=(
        "on DuckDB 2.0.0 a DuckDB SQL MERGE into a project's Delta table reaches DuckDB itself, which says "
        "'Can only merge into base tables'; Spark SQL takes the Delta path on both (WR-144)"))),
    "spark"])
def test_local_data_merge_update_delete_run_in_either_dialect(local, dialect):
    block = _block("local-data.md", "MERGE INTO customers AS t USING updates AS s ON t.id = s.id")
    engine = local()
    engine.execute("CREATE TABLE customers USING DELTA AS SELECT * FROM (VALUES (1, 'a', 'EU', 100.0, DATE '2019-01-01'), "
                   "(2, 'b', 'US', 50.0, DATE '2025-01-01'), (3, 'c', 'EU', 10.0, DATE '2025-01-01'), "
                   "(5, 'e', 'EU', 1.0, DATE '2025-01-01')) v(id, name, region, spend, last_order)")
    engine.execute("CREATE TABLE updates AS SELECT * FROM (VALUES (1, 'a2', 'EU', 100.0, DATE '2019-01-01', false), "
                   "(4, 'd', 'EU', 5.0, DATE '2025-06-01', false), (3, 'c', 'EU', 10.0, DATE '2025-01-01', true)) "
                   "v(id, name, region, spend, last_order, deleted)")
    engine.execute(block.code, dialect=dialect)
    rows = engine.execute("SELECT id, name, spend FROM customers ORDER BY id")["rows"]
    # 1 updated then deleted as an old order, 3 deleted by the source, 4 inserted (EU: spend * 1.1), 5 is EU and
    # not in the source, so gone; 2 is US and stays
    assert [(r[0], r[1]) for r in rows] == [(2, "b"), (4, "d")]
