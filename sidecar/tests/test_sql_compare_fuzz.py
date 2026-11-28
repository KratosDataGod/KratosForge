"""Compare data (editor parity S34) against a plain-Python diff, on seeded random tables.

Each seed builds A, then B from it: rows dropped, rows added, values moved, NULLs set and cleared. Keys
are whole numbers, now and then NULL, and on some seeds repeated. The other columns are a nullable float,
a nullable decimal and nullable text. sql_compare must give the reference's counts, its pages must add up
to every differing row, a tolerance must never call a difference larger than itself the same, and a NULL
equals only another NULL. Numbers sit on a 0.01 grid and tolerances halfway between, so DOUBLE and exact
arithmetic agree on every pair and the reference needs no floating-point care."""

from __future__ import annotations

import random
from collections import Counter
from decimal import Decimal

import pyarrow as pa
import pytest

from fabric_desktop import sql_compare

SEEDS = range(16)
TEXT = ["a", "b", "c", "dd", "", "=x"]
COLUMNS = ("f", "d", "t")
DECIMAL = pa.decimal128(12, 2)


class Runs:
    def __init__(self, a, b):
        self.tables = {"a": a, "b": b}

    def table(self, run_id):
        return {"id": run_id}, self.tables[run_id]


def _value(rng, column):
    if rng.random() < 0.15:
        return None
    if column == "f":
        return rng.randint(-500, 500) / 100
    if column == "d":
        return Decimal(rng.randint(-500, 500)).scaleb(-2)
    return rng.choice(TEXT)


def _moved(rng, column, value):
    """Another value for a column: a NULL set or cleared, a number moved by 1 to 5 hundredths, other text."""
    if value is None or rng.random() < 0.2:
        return _value(rng, column) if value is None else None
    if column == "t":
        return rng.choice([t for t in TEXT if t != value])
    step = rng.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5])
    if column == "f":
        return round(value * 100 + step) / 100
    return value + Decimal(step).scaleb(-2)


def _tables(seed):
    rng = random.Random(seed)
    n = rng.randint(20, 120)
    ids = rng.sample(range(1, 10 * n), n)
    if rng.random() < 0.3:
        ids[rng.randrange(n)] = None
    a = [{"id": i, **{c: _value(rng, c) for c in COLUMNS}} for i in ids]
    b = []
    for row in a:
        r = rng.random()
        if r < 0.15:
            continue  # only in A
        row = dict(row)
        if r < 0.45:
            for c in rng.sample(COLUMNS, rng.randint(1, 3)):
                row[c] = _moved(rng, c, row[c])
        b.append(row)
    taken = {i for i in ids if i is not None}
    for _ in range(rng.randint(0, n // 5)):  # only in B
        i = rng.randrange(10 * n, 20 * n)
        if i not in taken:
            taken.add(i)
            b.append({"id": i, **{c: _value(rng, c) for c in COLUMNS}})
    repeated = rng.random() < 0.3
    if repeated:  # a whole row again, or a key again with other values, on one side or both
        for side in rng.sample([a, b], rng.randint(1, 2)):
            for _ in range(rng.randint(1, 3)):
                row = dict(rng.choice(side))
                if rng.random() < 0.5:
                    row["f"] = _moved(rng, "f", row["f"])
                side.extend([row] * rng.randint(1, 3))  # a row two or more times over: a line "×3" on the page
    rng.shuffle(b)
    tolerance = {c: rng.choice([0.015, 0.025, 0.045]) for c in ("f", "d") if rng.random() < 0.5}
    return a, b, tolerance, repeated


def _arrow(rows):
    return pa.table({"id": pa.array([r["id"] for r in rows], pa.int64()),
                     "f": pa.array([r["f"] for r in rows], pa.float64()),
                     "d": pa.array([r["d"] for r in rows], DECIMAL),
                     "t": pa.array([r["t"] for r in rows], pa.string())})


def _same(column, x, y, tolerance):
    if x is None or y is None:
        return x is None and y is None  # a NULL equals only another NULL
    if column in tolerance:
        return abs(Decimal(str(x)) - Decimal(str(y))) <= Decimal(str(tolerance[column]))
    return x == y


def _keyed_reference(a, b, tolerance):
    ka, kb = {r["id"]: r for r in a}, {r["id"]: r for r in b}
    changed = {}
    for k in ka.keys() & kb.keys():
        columns = {c for c in COLUMNS if not _same(c, ka[k][c], kb[k][c], tolerance)}
        if columns:
            changed[k] = columns
    return {"only_a": set(ka) - set(kb), "only_b": set(kb) - set(ka), "changed": changed}


def _whole(rows):
    return Counter((r["id"], r["f"], r["d"], r["t"]) for r in rows)


def _pages(runs, status, **kw):
    """Every example of one kind (or of every kind), a page at a time, and the listed total."""
    first = sql_compare.compare(runs, "a", "b", status=status, **kw)
    examples, offset = list(first["examples"]), 0
    while offset + sql_compare.PAGE_ROWS < first["listed"]:
        offset += sql_compare.PAGE_ROWS
        page = sql_compare.compare(runs, "a", "b", status=status, offset=offset, **kw)
        assert 0 < len(page["examples"]) <= sql_compare.PAGE_ROWS and page["listed"] == first["listed"]
        examples += page["examples"]
    assert len(examples) == first["listed"], "the pages hold every row the total says"
    return first, examples


def _check_keyed(a, b, tolerance):
    runs, ref = Runs(_arrow(a), _arrow(b)), _keyed_reference(a, b, tolerance)
    kw = {"keys": ["id"], "tolerance": tolerance}
    out, every = _pages(runs, None, **kw)
    assert out["counts"] == {"only_a": len(ref["only_a"]), "only_b": len(ref["only_b"]), "changed": len(ref["changed"]),
                             "same": len(a) - len(ref["only_a"]) - len(ref["changed"])}
    assert out["result"] == ("same" if not (ref["only_a"] or ref["only_b"] or ref["changed"]) else "differs")
    keys = [e["key"]["id"] for e in every]
    assert len(keys) == len(set(keys)), "no row is on two pages"
    assert set(keys) == ref["only_a"] | ref["only_b"] | set(ref["changed"])
    for status in ("only_a", "only_b", "changed"):
        listed, examples = _pages(runs, status, **kw)
        assert {e["status"] for e in examples} <= {status}
        got = {e["key"]["id"] for e in examples}
        assert got == (set(ref[status]) if status != "changed" else set(ref["changed"]))
        assert listed["listed"] == out["counts"][status]
        if status == "changed":
            for e in examples:  # the columns that differ, never one within its tolerance, never NULL beside NULL
                assert set(e["columns"]) == ref["changed"][e["key"]["id"]]
                assert set(e["columns"]) | set(e["same"]) == set(COLUMNS)
    # A tolerance never makes a difference larger than itself the same.
    rows_b = {r["id"]: r for r in b}
    for row in a:
        other = rows_b.get(row["id"])
        if other is None:
            continue
        for c, tol in tolerance.items():
            x, y = row[c], other[c]
            if (x is None) != (y is None) or (x is not None and abs(Decimal(str(x)) - Decimal(str(y))) > Decimal(str(tol))):
                assert c in ref["changed"].get(row["id"], ()), (row, other, c)
    return set(ref["changed"])


def _check_whole_rows(a, b):
    runs = Runs(_arrow(a), _arrow(b))
    ca, cb = _whole(a), _whole(b)
    only_a, only_b = ca - cb, cb - ca
    out, every = _pages(runs, None)
    assert out["counts"] == {"only_a": sum(only_a.values()), "only_b": sum(only_b.values()), "changed": None,
                             "same": len(a) - sum(only_a.values())}
    got = {"only_a": Counter(), "only_b": Counter()}
    for status in ("only_a", "only_b"):
        _listed, examples = _pages(runs, status)
        for e in examples:
            r = e["row"]
            row = (r["id"], r["f"], None if r["d"] is None else Decimal(r["d"]), r["t"])
            assert got[status][row] == 0, "a row is listed once, with how many more times"
            got[status][row] = e["times"]
    assert got == {"only_a": only_a, "only_b": only_b}
    assert len(every) == len(only_a) + len(only_b)


@pytest.mark.parametrize("seed", SEEDS)
def test_compare_agrees_with_a_plain_python_diff(seed):
    a, b, tolerance, repeated = _tables(seed)
    keys_a, keys_b = Counter(r["id"] for r in a), Counter(r["id"] for r in b)
    if repeated and (max(keys_a.values()) > 1 or max(keys_b.values()) > 1):
        out = sql_compare.compare(Runs(_arrow(a), _arrow(b)), "a", "b", keys=["id"], tolerance=tolerance)
        where = ("both sides" if max(keys_a.values()) > 1 and max(keys_b.values()) > 1
                 else "A" if max(keys_a.values()) > 1 else "B")
        assert f"The key (id) is repeated in {where}," in out["stopped"] and "counts" not in out
    else:
        _check_keyed(a, b, tolerance)
    _check_whole_rows(a, b)


def test_the_seeds_cover_every_case():
    """The seeds above aren't all alike: some repeat a key, some have a tolerance, a NULL key, a NULL beside a value."""
    seen = Counter()
    for seed in SEEDS:
        a, b, tolerance, repeated = _tables(seed)
        seen["repeated"] += repeated
        seen["tolerance"] += bool(tolerance)
        seen["null key"] += any(r["id"] is None for r in a)
        ca, cb = _whole(a), _whole(b)
        seen["a row more times"] += any(v > 1 for v in ((ca - cb) + (cb - ca)).values())
        if not repeated:
            changed = _keyed_reference(a, b, tolerance)["changed"]
            seen["changed"] += bool(changed)
            rows_b = {r["id"]: r for r in b}
            seen["null beside a value"] += any((r[c] is None) != (rows_b[r["id"]][c] is None)
                                               for r in a if r["id"] in rows_b for c in COLUMNS)
            seen["within tolerance"] += any(c in tolerance and r[c] is not None and rows_b[r["id"]][c] is not None
                                            and r[c] != rows_b[r["id"]][c]
                                            and _same(c, r[c], rows_b[r["id"]][c], tolerance)
                                            for r in a if r["id"] in rows_b for c in COLUMNS)
    assert all(seen[k] >= 2 for k in ("repeated", "a row more times", "tolerance", "null key", "changed", "null beside a value",
                                      "within tolerance")), seen
