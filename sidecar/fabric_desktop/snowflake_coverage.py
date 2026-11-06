"""Which Snowflake SQL functions, operators and statements run on this PC, and how (like #1732 for Spark).

``snowflake_builtins.json`` lists Snowflake's SQL functions by group, from Snowflake's SQL reference
(https://docs.snowflake.com/en/sql-reference/functions-all), then common operators and statements with a
sample of each. Geospatial, Cortex AI, table, system and file functions aren't listed: they need
Snowflake's own objects or services, and translate() refuses those it can read.

``coverage()`` gives each one a status on this PC, by the same path a Snowflake SQL cell takes
(snowflake_sql.translate(), then DuckDB):

    runs        every check in snowflake_sql_parity for it gives the value Snowflake documents
    differs     it runs, with a known difference: a check gives another value, or translate() labels it
    refused     translate() says it runs only in Snowflake, so nothing runs with another answer
    fails       it translates, but DuckDB stops: it has no function of that name, or can't run it
    not checked it runs here, but no check compares its value with Snowflake's documented one

A function with checks takes its status from them. Any other is tried with NULL arguments (0 to 4
of them, inside OVER () for a window function) and a listed sample is run on two small tables, so
"not checked" means only that DuckDB accepts it. Nothing here was run against a Snowflake account.

``docs/snowflake-functions.md`` is ``markdown()``'s output; tests/test_snowflake_coverage.py fails when
it is stale, so a sqlglot, DuckDB or rule change that fixes or breaks one shows up there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).with_name("snowflake_builtins.json")
STATUSES = ("runs", "differs", "refused", "fails", "not checked")
WINDOW_GROUP = "Window"
_ARITIES = 5   # 0 to 4 NULL arguments
_TABLES = ("CREATE TABLE t (a INTEGER, b INTEGER); INSERT INTO t VALUES (1, 2), (2, 3); "
           "CREATE TABLE s (a INTEGER, b INTEGER); INSERT INTO s VALUES (1, 5), (9, 9)")


def load() -> list[dict[str, Any]]:
    return list(json.loads(DATA.read_text(encoding="utf-8")))


def _first_line(exc: BaseException) -> str:
    return ((str(exc).strip().splitlines() or [type(exc).__name__])[0])[:160]


def case_name(function: str) -> str:
    """The list name a snowflake_sql_parity case checks: its function, without the "(what)" after it."""
    name = function.split(" (")[0]
    return "Path (v:a)" if name.upper() == "PARSE_JSON PATH" else name.upper() if name[:1].isalpha() else name


def _from_cases(cases: list[Any]) -> tuple[str, str]:
    from . import snowflake_sql_parity

    outcomes = {c.function: snowflake_sql_parity.run(c) for c in cases}
    for outcome, status in (("untranslated", "refused"), ("error", "fails"), ("differs", "differs")):
        hit = [(f, v) for f, (o, v) in outcomes.items() if o == outcome]
        if hit:
            f, value = hit[0]
            if outcome == "differs":
                note = next(c.note for c in cases if c.function == f) or f"{value!r} here"
                return status, f"{f}: {note}"
            return status, f"{f}: {value}"
    from . import snowflake_sql
    labels = dict.fromkeys(f"{n.id} {n.title}" for c in cases for st in snowflake_sql.translate(c.sql).statements
                           for n in st.notes)
    checks = f"{len(cases)} value check{'s' if len(cases) > 1 else ''}"
    return "runs", checks + (f"; labelled {', '.join(labels)}" if labels else "")


def _try(sql: str, setup: bool = False) -> tuple[str, str]:
    """(status, note) for one sample: refused, differs (a labelled difference), fails or not checked."""
    import duckdb

    from . import snowflake_sql

    try:
        translation = snowflake_sql.translate(sql)
    except ValueError as exc:
        return "unread", _first_line(exc)
    if not translation.runnable:
        return "refused", translation.first_refusal().removeprefix("Statement 1 runs only in Snowflake: ")
    con = duckdb.connect()
    try:
        if setup:
            con.execute(_TABLES)
        con.execute(translation.duckdb_sql).fetchall()
    except duckdb.Error as exc:
        return "fails", _first_line(exc)
    finally:
        con.close()
    labelled = [n for s in translation.statements for n in s.notes]
    if labelled:
        return "differs", "; ".join(f"{n.id} {n.title}" for n in labelled)
    return "not checked", ""


def _missing(note: str) -> bool:
    return "does not exist" in note


def _probe(function: dict[str, Any]) -> tuple[str, str]:
    name = function["name"]
    if function.get("probe"):
        status, note = _try(function["probe"], setup=True)
        return ("fails", f"sqlglot can't read it: {note}") if status == "unread" else (status, note)
    over = " OVER (ORDER BY 1)" if function.get("group") == WINDOW_GROUP else ""
    tried: list[tuple[str, str]] = []
    for n in range(_ARITIES):
        tried.append(_try(f"SELECT {name}({', '.join(['NULL'] * n)}){over}"))
        if tried[-1][0] in ("differs", "not checked"):
            return tried[-1]
    for status, note in tried:  # no number of NULLs ran: a refusal says why, then DuckDB's errors
        if status == "refused":
            return status, note
    fails = [note for status, note in tried if status == "fails"]
    if fails and all(_missing(note) for note in fails):
        return "fails", f"DuckDB has no {name}, and the rules don't rewrite it"
    if fails:
        return "fails", next(note for note in fails if not _missing(note))
    return "fails", f"sqlglot can't read it: {tried[0][1]}"


def check(function: dict[str, Any], cases: dict[str, list[Any]]) -> tuple[str, str]:
    """``function``'s status (see the module docstring) and a short note."""
    mine = cases.get(function["name"])
    return _from_cases(mine) if mine else _probe(function)


def coverage() -> list[dict[str, Any]]:
    """Every listed function, operator and statement with its group and status, in list order."""
    from . import snowflake_sql_parity

    cases: dict[str, list[Any]] = {}
    for case in snowflake_sql_parity.CASES:
        cases.setdefault(case_name(case.function), []).append(case)
    out = []
    for function in load():
        status, note = check(function, cases)
        sample = "" if function["name"] in cases else function.get("probe", "")  # checks decide, not the sample
        out.append({"name": function["name"], "group": function["group"], "sample": sample, "status": status,
                    "note": note})
    return out


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/snowflake-functions.md: the counts, then every function by group."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# Snowflake functions on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/snowflake_coverage.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.snowflake_coverage` in sidecar/ and commit the result. -->",
        "",
        "Snowflake's SQL functions, common operators and statements, with what a Snowflake SQL cell does",
        "with each on this PC, where it is translated for DuckDB. No Snowflake account was used: a value",
        "check compares this PC's answer with the value Snowflake's SQL reference documents. CI runs every",
        "check again, so this page changes when a function is fixed or breaks.",
        "",
        "- **Runs**: every value check for it gives the value Snowflake documents.",
        "- **Differs**: runs, with a known difference (a value check differs, or the translation labels it).",
        "- **Refused**: stops with a message saying it runs only in Snowflake, so nothing runs with another answer.",
        "- **Fails**: stops with an error, usually because DuckDB has no function of that name. Run it in Snowflake.",
        "- **Not checked**: DuckDB runs it, but no check compares its value with Snowflake's yet. Check",
        "  results that use it in Snowflake.",
        "",
        "Geospatial, Cortex AI, table, system and file functions aren't listed: they need Snowflake's own",
        "objects or services. [Engines](engines.md#snowflake-sql-on-this-pc-preview) lists the rules and",
        "the known differences in more detail.",
        "",
        "| Status | Functions |",
        "| --- | ---: |",
        *[f"| {s.capitalize()} | {counts[s]} |" for s in STATUSES],
        f"| **All** | **{len(rows)}** |",
        "",
    ]
    for group in dict.fromkeys(r["group"] for r in rows):
        sample = any(r["sample"] for r in rows if r["group"] == group)
        head = "| Name | Status | Sample | Note |" if sample else "| Function | Status | Note |"
        lines += [f"## {group}", "", head, "| --- | --- | --- | --- |" if sample else "| --- | --- | --- |"]
        for r in (r for r in rows if r["group"] == group):
            cells = [f"`{r['name']}`", r["status"].capitalize(), *([f"`{r['sample']}`" if r["sample"] else ""] if sample else []),
                     r["note"]]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


DOC = Path(__file__).resolve().parents[2] / "docs" / "snowflake-functions.md"

if __name__ == "__main__":  # pragma: no cover - writes the docs page
    from .jsonfile import write_text
    write_text(DOC, markdown(coverage()), newline="\n")  # LF, like the committed page
    print(f"Wrote {DOC}")
