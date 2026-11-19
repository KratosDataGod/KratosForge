"""Derive expressions, valid and garbage, through column lineage (meta/lineage.py) and what reads it.

A seeded generator, so a failure names the same input on every run. An expression's columns are either
known exactly or marked unknown: never guessed, and nothing that reads lineage invents an edge from one.
"""

from __future__ import annotations

import random
import re
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import change_impact, lineage, openlineage
from fabric_desktop.meta.project import MAX_EXPR_OPERATORS, check_expr
from fabric_desktop.service import Service

SEED = 20261119
# Bare names, mixed case, and quoted ones that a bare word couldn't be (a space, a keyword).
IDENTS = {"a": "a", "b": "b", "c_1": "c_1", "Amount": "Amount", '"order total"': "order total", '"select"': "select"}
# Literals that look like column names: never read as columns.
LITERALS = ["1", "2.5", "0", "NULL", "TRUE", "'a'", "'b'", "'Amount'", "'it''s'", "'c_1 + b'", "DATE '2026-01-01'"]


def gen_valid(rng: random.Random, depth: int = 0) -> tuple[str, set[str]]:
    """An expression Polars SQL reads, and exactly the columns it uses."""
    if depth >= 3 or rng.random() < 0.3:
        if rng.random() < 0.6:
            text = rng.choice(list(IDENTS))
            return text, {IDENTS[text]}
        return rng.choice(LITERALS), set()
    kind = rng.randrange(10)
    x, xs = gen_valid(rng, depth + 1)
    y, ys = gen_valid(rng, depth + 1)
    if kind == 0:
        return f"{x} {rng.choice(['+', '-', '*', '/', '%', '||'])} {y}", xs | ys
    if kind == 1:
        return f"(({x}))", xs
    if kind == 2:
        c, cs = gen_valid(rng, depth + 1)
        return f"CASE WHEN {c} > {y} THEN {x} ELSE {y} END", xs | ys | cs
    if kind == 3:
        return f"COALESCE({x}, {y}, 0)", xs | ys
    if kind == 4:
        return f"CAST({x} AS {rng.choice(['VARCHAR', 'DOUBLE', 'BIGINT', 'DATE'])})", xs
    if kind == 5:
        return f"{rng.choice(['UPPER', 'LOWER', 'TRIM', 'LENGTH'])}({x})", xs
    if kind == 6:
        return f"NULLIF({x}, {y})", xs | ys
    if kind == 7:
        return f"{x} IS {rng.choice(['NULL', 'NOT NULL'])}", xs
    if kind == 8:
        return f"{x} BETWEEN {y} AND 10", xs | ys
    part, order = rng.choice(list(IDENTS)), rng.choice(list(IDENTS))
    fn = rng.choice(["SUM", "MIN", "MAX", "COUNT", "AVG"])
    return f"{fn}({x}) OVER (PARTITION BY {part} ORDER BY {order})", xs | {IDENTS[part], IDENTS[order]}


TOKENS = ["(", ")", "((", "))", ",", "+", "-", "*", "/", "||", "'", '"', "`", "[", "]", ";", "::", "->",
          "a", "b", "Amount", "SELECT", "FROM", "WHERE", "UNION", "WITH", "OVER", "PARTITION", "BY", "CASE",
          "WHEN", "THEN", "END", "COALESCE", "CAST", "AS", "NOT", "AND", "OR", "1", "'x'", "é", "名前", "\x00",
          "\u202e", "🙂", "\t", "\n", "--", "/*", "*/", "$1", "?", "@x", "COLUMNS('^a')"]


def gen_garbage(rng: random.Random) -> str:
    return rng.choice(["", " "]).join(rng.choice(TOKENS) for _ in range(rng.randrange(1, 40)))


SPECIAL = [
    "", "   ", "\n", "(", ")", "((a)", "a))", "a +* )", "*", "a.*", "a; b", "a UNION b", "SELECT a FROM t",
    "WITH c AS (SELECT 1) SELECT * FROM c", "a IN (SELECT b FROM t)", "\ud800", "\x00", "é" * 5000,
    "a" * 10_000, "(" * 5000 + "a" + ")" * 5000, "COALESCE(" * 2000 + "a" + ")" * 2000,
    # A flat chain this long overflowed Polars' parser stack and killed the process (SIGSEGV).
    "+".join(f"c{i}" for i in range(3000)), " AND ".join("a > 1" for _ in range(3000)),
    "1" + "+1" * (MAX_EXPR_OPERATORS + 1),
    "COLUMNS('^a.*$')", "COLUMNS('^a.*$') + b", "MAX(COLUMNS('a')) OVER (PARTITION BY b)", "SUM(x) OVER w",
    None, 5, ["a"],
]


def appears(name: str, expr: str) -> bool:
    """``name`` is written in ``expr`` as a word, or quoted."""
    return (re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", expr) is not None
            or f'"{name.replace(chr(34), chr(34) * 2)}"' in expr or f"`{name}`" in expr)


def corpus() -> list[tuple[object, set[str] | None]]:
    rng = random.Random(SEED)
    valid = [gen_valid(rng) for _ in range(400)]
    return valid + [(gen_garbage(rng), None) for _ in range(400)] + [(s, None) for s in SPECIAL]


CORPUS = corpus()


def test_expr_columns_never_raises_and_never_names_a_column_the_text_does_not():
    read = 0
    for expr, used in CORPUS:
        got = lineage._expr_columns(expr)
        assert got is None or isinstance(got, list), expr
        if got is None:
            continue
        read += used is not None
        assert all(appears(c, expr) for c in got), (expr, got)
        if used is not None:  # a valid one: exactly its columns, and never a literal that looks like one
            assert set(got) == used, (expr, got, used)
    assert read >= 380  # the valid ones are read, not given up on


@pytest.mark.parametrize("expr", ["", "a +* )", "a; b", "SELECT a FROM t", "+".join(["a"] * 3000), "COLUMNS('^a.*$') + b",
                                  None, 5], ids=lambda v: repr(v)[:24])
def test_what_cant_be_read_is_unknown(expr):
    assert lineage._expr_columns(expr) is None


def test_the_operator_limit_counts_operators_outside_quotes_only():
    assert lineage._expr_columns("1" + "+1" * MAX_EXPR_OPERATORS) == []
    assert lineage._expr_columns("1" + "+1" * (MAX_EXPR_OPERATORS + 1)) is None
    long_text = "'" + "+".join(["x"] * 3000) + "'"
    assert lineage._expr_columns(f"COALESCE(a, {long_text})") == ["a"]
    assert lineage._expr_columns('"' + "a+" * 300 + 'b" + c') == ["a+" * 300 + "b", "c"]
    assert check_expr("+".join(["a"] * 3000), {"a"}) == ["expression '" + "+".join(["a"] * 3000)
                                                         + "' does not parse (ValueError)"]


@pytest.mark.parametrize(("expr", "columns"), [
    ("SUM(x) OVER (PARTITION BY y)", ["x", "y"]),
    ("SUM(x) OVER (PARTITION BY y ORDER BY z ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)", ["x", "y", "z"]),
    ("ROW_NUMBER() OVER (PARTITION BY y ORDER BY z)", ["y", "z"]),
    ("LAG(a) OVER (PARTITION BY b ORDER BY c)", ["a", "b", "c"]),
    ("a - AVG(a) OVER (PARTITION BY b)", ["a", "b"]),
    ("MAX(a) OVER (PARTITION BY b) - MIN(a) OVER (PARTITION BY b)", ["a", "b"]),
    ("COUNT(*) OVER (PARTITION BY k)", ["k"]),
    ("COALESCE(CASE WHEN a > 1 THEN b ELSE NULL END, c)", ["a", "b", "c"]),
    ("COALESCE(CASE WHEN a IS NULL THEN b END, CASE WHEN c > 0 THEN d ELSE e END)", ["a", "b", "c", "d", "e"]),
    ("CASE WHEN COALESCE(a, b) > 0 THEN c END", ["a", "b", "c"]),
    ("CASE WHEN s = 'b' THEN 'c' ELSE 'a' END", ["s"]),  # literals that look like columns aren't columns
    ('"order total" * 2 + "select"', ["order total", "select"]),
    ("CAST(COALESCE(a, '0') AS DOUBLE) + b", ["a", "b"]),
    ("UPPER(TRIM(name)) || '-' || CAST(id AS VARCHAR)", ["id", "name"]),
    ("a IN (b, c)", ["a", "b", "c"]),
    ("COUNT(*)", []),
    ("'amount'", []),
])
def test_window_case_and_coalesce_name_their_input_columns(expr, columns):
    assert sorted(lineage._expr_columns(expr)) == sorted(columns)


@pytest.mark.parametrize("expr", ["a UNION b", "WITH c AS (SELECT a) SELECT a FROM c", "a IN (SELECT b FROM t)",
                                  "SUM(x) OVER w", "COLUMNS('^a.*$')"])
def test_statement_level_ideas_are_unknown_not_guessed(expr):
    assert lineage._expr_columns(expr) is None


# ---- on an entity, without a project on disk ----

def stand_in(name, columns, spec, sources=("bronze.raw",)):
    return SimpleNamespace(name=name, columns=list(columns), spec=spec, mode="append", mode_columns=lambda: set(),
                           surrogate_key=None, sources=lambda: list(sources))


def test_entity_columns_upstream_and_downstream_on_every_expression():
    raw = stand_in("bronze.raw", ["a", "b", "Amount"], {"source": {"files": "landing/*.csv"}})
    for expr, _ in CORPUS[::3]:
        s = stand_in("silver.s", ["a", "z"], {"source": {"entity": ["bronze.raw"]},
                                              "derive": [{"column": "z", "expr": expr}]})
        project = SimpleNamespace(entities={"bronze.raw": raw, "silver.s": s})
        rec = lineage.entity_columns(project, "silver.s")["z"]
        known = lineage._expr_columns(expr)
        if known is None:
            assert rec["label"] == "unknown" and rec["inputs"] == [] and rec["note"], expr
        else:
            assert rec["label"] == "declared" and "note" not in rec, expr
            for i in rec["inputs"]:  # each one is a column the expression names, here or in its source
                assert i["column"] in known, (expr, i)
                assert i["entity"] in ("silver.s", "bronze.raw"), (expr, i)
                assert i.get("missing", False) == (i["entity"] == "bronze.raw" and i["column"] not in raw.columns)
        steps = lineage.upstream(project, "silver.s", "z")
        assert steps[0]["column"] == "z" and all(st["entity"] in project.entities for st in steps)
        for col in raw.columns:
            down = lineage.downstream(project, "bronze.raw", col)
            assert (("silver.s", "z") in {(d["entity"], d["column"]) for d in down}) == bool(known and col in known)


def test_a_derive_without_text_is_unknown():
    s = stand_in("silver.s", ["z"], {"source": {"entity": ["bronze.raw"]}, "derive": [{"column": "z"}]})
    project = SimpleNamespace(entities={"silver.s": s})
    rec = lineage.entity_columns(project, "silver.s")["z"]
    assert rec["label"] == "unknown" and rec["inputs"] == [] and rec["expr"] is None


# ---- through the service, change impact and OpenLineage ----

@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def set_net_amount(svc, expr):
    text = svc.meta_entity("silver.orders")["text"]
    old = 'expr = "amount - coalesce(discount, 0)"'
    assert old in text
    svc.meta_save_entity("silver.orders", text.replace(old, f"expr = {expr}", 1))


RUNS = [{"run_id": "r1", "entity": e, "status": "ok", "started_at": "2026-11-14T10:00:00+00:00"}
        for e in ("bronze.orders", "silver.orders", "gold.fact_sales")]


@pytest.mark.parametrize("expr", ['"amount -* ) discount"', "\"COLUMNS('^d.*$') + amount\"", "5",
                                  '"' + "+".join(["amount"] * 3000) + '"', '"SELECT amount FROM t"'],
                         ids=["garbage", "selector", "not-text", "3000-operators", "statement"])
def test_an_unreadable_derive_is_unknown_everywhere_and_adds_no_edge(svc, expr):
    set_net_amount(svc, expr)
    rec = svc.meta_lineage("silver.orders")["columns"]["net_amount"]
    assert rec["how"] == "derived" and rec["label"] == "unknown" and rec["inputs"] == []
    up = svc.meta_lineage("gold.fact_sales", "net_amount")["upstream"]
    assert [(s["entity"], s["column"], s["label"]) for s in up] == [
        ("gold.fact_sales", "net_amount", "inferred"), ("silver.orders", "net_amount", "unknown")]
    down = svc.meta_lineage("bronze.orders", "discount")["downstream"]
    assert ("silver.orders", "net_amount") not in {(d["entity"], d["column"]) for d in down}

    project = svc._project()
    for change in change_impact.CHANGES:
        out = change_impact.column_impact(project, "bronze.orders", "discount", change, "d2", "string")
        chains = [c for it in out["items"] for c in it["chain"]]
        assert "silver.orders.net_amount" not in chains and "gold.fact_sales.net_amount" not in chains
    out = change_impact.column_impact(project, "silver.orders", "net_amount", "drop")
    assert {i["name"] for i in out["items"] if i["kind"] == "table"} >= {"gold.fact_sales"}

    events = {e["job"]["name"]: e for e in openlineage.run_events(project, RUNS, "dev")["events"]}
    facet = events["silver.orders"]["outputs"][0]["facets"].get("columnLineage", {"fields": {}})
    assert "net_amount" not in facet["fields"]
    assert "net_amount" in {f["name"] for f in events["silver.orders"]["outputs"][0]["facets"]["schema"]["fields"]}


def test_a_window_derive_reads_its_partition_and_order_columns(svc):
    set_net_amount(svc, '"SUM(amount) OVER (PARTITION BY customer_id ORDER BY order_date)"')
    rec = svc.meta_lineage("silver.orders")["columns"]["net_amount"]
    assert rec["label"] == "declared"
    assert rec["inputs"] == [{"entity": "silver.orders", "column": c} for c in ("amount", "order_date", "customer_id")]
    down = svc.meta_lineage("bronze.orders", "customer_id")["downstream"]
    assert ("silver.orders", "net_amount", "derived") in {(d["entity"], d["column"], d["how"]) for d in down}
    project = svc._project()
    events = {e["job"]["name"]: e for e in openlineage.run_events(project, RUNS, "dev")["events"]}
    fields = events["silver.orders"]["outputs"][0]["facets"]["columnLineage"]["fields"]["net_amount"]["inputFields"]
    assert sorted(f["field"] for f in fields) == ["amount", "customer_id", "order_date"]
