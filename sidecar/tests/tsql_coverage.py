"""Which T-SQL functions, operators and statements run on this PC's local engine, and how (like #1732 for
Spark and #1863 for Snowflake).

``tsql_contract/builtins.json`` lists T-SQL's scalar, aggregate, window and JSON functions by group, from
Microsoft's T-SQL function reference, then common operators and statements with a sample of each. System,
metadata, security and cursor functions aren't listed: they read the server's own state.

``coverage()`` gives each one a status, by the path a T-SQL query on a local data project takes
(localdata.tsql.translate(), then DuckDB):

    runs        every value check for it gives T-SQL's documented answer (some uses may still be refused,
                said in the note)
    differs     a value check gives another answer
    refused     the local engine refuses it by name, so nothing runs with another answer
    fails       it stops with DuckDB's own error instead of the local engine's message
    not checked it runs here, but no value check compares its answer with T-SQL's

The value checks are the T-SQL contract cases with Fabric's expected rows (tsql_contract/cases.jsonl,
warehouse and endpoint targets) and the documented answers in test_tsql_loud_gaps.py. A check counts
for each listed function its SQL calls, and for each statement whose pattern it matches. Anything else
is tried with its sample, or with 0 to 4 NULL arguments, on a small table, so "not checked" means only
that it runs. Nothing here was run against a Fabric Warehouse.

``docs/tsql-functions.md`` is ``markdown()``'s output; tests/test_tsql_coverage.py fails when it is
stale. Run ``python -m tests.tsql_coverage`` in sidecar/ to write it again.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent / "tsql_contract"
DATA = HERE / "builtins.json"
CASES = HERE / "cases.jsonl"
STATUSES = ("runs", "differs", "refused", "fails", "not checked")
WINDOW_GROUP = "Window"
_LOCAL = "local engine"
_RESET = ("DROP VIEW IF EXISTS dbo.v; DROP TABLE IF EXISTS dbo.t; DROP TABLE IF EXISTS dbo.t2; "
          "DROP TABLE IF EXISTS dbo.t3; DROP TABLE IF EXISTS dbo.t_copy; "
          "CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 2, 'x'), (2, 3, 'y')) v(a, b, s)")


def load() -> list[dict[str, Any]]:
    return list(json.loads(DATA.read_text(encoding="utf-8")))


def checks() -> list[dict[str, Any]]:
    """Every value check: {id, sql, rows}, rows being T-SQL's answer (the first cell only, for a gap)."""
    out = []
    for line in CASES.read_text(encoding="utf-8").splitlines()[1:]:
        case = json.loads(line)
        fabric = case.get("fabric") or {}
        if case.get("target") in ("warehouse", "endpoint") and fabric.get("expect") == "accepts" and "rows" in fabric:
            out.append({"id": case["id"], "sql": case["sql"], "rows": fabric["rows"]})
    from tests import test_tsql_loud_gaps as gaps
    out += [{"id": name, "sql": sql, "first": want} for name, sql, want in gaps.GAPS]
    return out


def _first_line(exc: BaseException) -> str:
    return ((str(exc).strip().splitlines() or [type(exc).__name__])[0])[:160]


def _cell_same(got: Any, want: Any) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(got) == bool(want) and got is not None and want is not None
    if want is None or got is None:
        return want is None and got is None
    if isinstance(want, (int, float)) or isinstance(got, (int, float, Decimal)):
        try:
            return float(got) == float(want)
        except (TypeError, ValueError):
            return False
    return str(got) == str(want)


def _rows_same(got: list[list[Any]], want: list[list[Any]]) -> bool:
    return len(got) == len(want) and all(len(g) == len(w) and all(map(_cell_same, g, w)) for g, w in zip(got, want, strict=True))


class Runner:
    """One local data project with a two-row table, dbo.t (a INT, b INT, s VARCHAR), made again before each run."""

    def __init__(self, folder: Path):
        from fabric_desktop.localdata import LocalData
        self.local = LocalData(folder)
        self.engine = self.local.engine(self.local.create("T-SQL coverage")["id"])

    def close(self) -> None:
        self.local.close_all()

    def run(self, sql: str) -> tuple[str, Any]:
        """("ok", rows), ("refused", message) or ("fails", message)."""
        self.engine.execute(_RESET)
        try:
            return "ok", self.engine.execute(sql, dialect="tsql")["rows"]
        except Exception as exc:  # noqa: BLE001 - the outcome is what's recorded
            message = _first_line(exc)
            return ("refused" if _LOCAL in message else "fails"), message

    def check(self, c: dict[str, Any]) -> tuple[str, str]:
        """("same" | "differs" | "refused" | "fails", note) for one value check."""
        outcome, got = self.run(c["sql"])
        if outcome != "ok":
            return outcome, f"{c['id']}: {got}"
        if "first" in c:
            same = bool(got and got[0]) and _cell_same(got[0][0], c["first"])
            return ("same", "") if same else ("differs", f"{c['id']}: {got[0][0] if got and got[0] else None!r} here, "
                                                          f"{c['first']!r} in T-SQL")
        if _rows_same(got, c["rows"]):
            return "same", ""
        return "differs", f"{c['id']}: {got!r} here, {c['rows']!r} in T-SQL"


def calls(item: dict[str, Any], sql: str) -> bool:
    """Whether ``sql`` uses ``item``: a statement's pattern, or a function called by name."""
    if item.get("match"):
        return re.search(item["match"], sql, re.I | re.M) is not None
    return re.search(rf"(?<![\w.@#\[]){re.escape(item['name'])}\s*\(", sql, re.I) is not None


def _from_checks(runner: Runner, mine: list[dict[str, Any]]) -> tuple[str, str]:
    results = [runner.check(c) for c in mine]
    for status in ("differs", "fails"):
        hit = [note for outcome, note in results if outcome == status]
        if hit:
            return status, hit[0]
    refused = [note for outcome, note in results if outcome == "refused"]
    same = sum(1 for outcome, _ in results if outcome == "same")
    if not same:
        return "refused", refused[0]
    note = f"{same} value check{'s' if same > 1 else ''}"
    return "runs", note + (f"; refused when {refused[0]}" if refused else "")


def _probe(runner: Runner, item: dict[str, Any]) -> tuple[str, str]:
    if item.get("probe"):
        tried = [runner.run(item["probe"])]
    else:
        over = " OVER (ORDER BY a) FROM dbo.t" if item["group"] == WINDOW_GROUP else ""
        tried = []
        for n in range(5):
            tried.append(runner.run(f"SELECT {item['name']}({', '.join(['NULL'] * n)}){over}"))
            if tried[-1][0] == "ok":
                break
    if tried[-1][0] == "ok":
        return "not checked", ""
    for status in ("refused", "fails"):
        hit = [note for outcome, note in tried if outcome == status]
        if hit:
            return status, hit[0]
    return "fails", tried[0][1]


def coverage(folder: Path) -> list[dict[str, Any]]:
    """Every listed function, operator and statement with its group and status, in list order."""
    every = checks()
    runner = Runner(folder)
    try:
        out = []
        for item in load():
            mine = [c for c in every if calls(item, c["sql"])]
            status, note = _from_checks(runner, mine) if mine else _probe(runner, item)
            out.append({"name": item["name"], "group": item["group"], "sample": "" if mine else item.get("probe", ""),
                        "status": status, "note": note.replace("This T-SQL can't be run on the local engine: ", "")})
        return out
    finally:
        runner.close()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/tsql-functions.md: the counts, then every function by group."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# T-SQL functions on this PC",
        "",
        "<!-- Generated by sidecar/tests/tsql_coverage.py. Don't edit by hand: run",
        "     `python -m tests.tsql_coverage` in sidecar/ and commit the result. -->",
        "",
        "T-SQL's functions, common operators and statements, with what a T-SQL query on a",
        "[local data project](local-data.md#t-sql) does with each on this PC, where it is translated for",
        "DuckDB. No Fabric Warehouse was used: a value check compares this PC's answer with T-SQL's",
        "documented one (the T-SQL contract cases and the documented answers the engine's tests pin). CI",
        "runs every check again, so this page changes when a function is fixed or breaks.",
        "",
        "- **Runs**: every value check for it gives T-SQL's answer. The note says when a use of it is refused.",
        "- **Differs**: a value check gives another answer here. Check results that use it in Fabric.",
        "- **Refused**: stops with a message naming it, so nothing runs with another answer. Run it in Fabric.",
        "- **Fails**: stops with DuckDB's own error instead of a message naming it. Run it in Fabric.",
        "- **Not checked**: it runs here, but no check compares its answer with T-SQL's yet. Check results",
        "  that use it in Fabric.",
        "",
        "System, metadata, security and cursor functions aren't listed: they read a server's own state.",
        "[Local data: T-SQL](local-data.md#t-sql) lists what is translated and refused in more detail.",
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
            cells = [f"`{r['name']}`", r["status"].capitalize(),
                     *([f"`{r['sample']}`" if r["sample"] else ""] if sample else []), r["note"]]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


DOC = Path(__file__).resolve().parents[2] / "docs" / "tsql-functions.md"

if __name__ == "__main__":  # pragma: no cover - writes the docs page
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        text = markdown(coverage(Path(tmp) / "local-data"))
    DOC.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {DOC}")
