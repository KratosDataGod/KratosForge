"""Will this SQL run on another platform? A static report, statement by statement (KUDP-05).

``portability(sql, source)`` reads SQL written for one platform and says, for each other platform,
one of:

    compatible    reads the same there as written, and no known problem applies
    translated    runs there once rewritten; the rewrite is shown, never applied
    unverified    something in it can't be checked (a function sqlglot doesn't know, or a statement
                  it can only keep as text), so test it there
    partial       runs there, but a known difference or risk applies; the reasons say which
    unsupported   uses something the platform doesn't have, so it won't run as is

The checks reuse what KratosForge already has: sqlglot reads and rewrites the SQL (with unsupported
constructs raising, not passing through quietly); the Fabric targets add sqlcompat's rules on the
rewritten SQL; Snowflake SQL on DuckDB uses snowflake_sql's labels. Platforms without rules of their
own (Databricks, Snowflake, PostgreSQL) are checked by the rewrite only, and the report says so.

What it never does: run the SQL, connect anywhere, change the person's SQL, or say two platforms give
the same answer. "Compatible" means no known problem, not proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp, transforms
from sqlglot.errors import ErrorLevel, ParseError, UnsupportedError

from . import snowflake_sql, sqlcompat

SCHEMA = "sql-portability-report@1"
# Each platform: its label, how sqlglot names its dialect, and the sqlcompat target that checks it.
PLATFORMS: dict[str, dict[str, Any]] = {
    "duckdb": {"label": "DuckDB (this PC)", "sqlglot": "duckdb", "rules": "duckdb"},
    "spark": {"label": "Spark SQL (Fabric lakehouse)", "sqlglot": "spark", "rules": "spark"},
    "warehouse": {"label": "Fabric Warehouse (T-SQL)", "sqlglot": "tsql", "rules": "warehouse"},
    "databricks": {"label": "Databricks SQL", "sqlglot": "databricks", "rules": None},
    "snowflake": {"label": "Snowflake SQL", "sqlglot": "snowflake", "rules": None},
    "postgres": {"label": "PostgreSQL", "sqlglot": "postgres", "rules": None},
}
GRADES = {
    "compatible": "No known problem: it reads the same there as written.",
    "translated": "Runs there once rewritten. The rewrite is shown; your SQL isn't changed.",
    "unverified": "Part of it can't be checked statically. Test it there.",
    "partial": "Runs there, but a known difference or risk applies.",
    "unsupported": "Uses something that platform doesn't have.",
}
_RANK = {g: i for i, g in enumerate(GRADES)}
MAX_SQL = 200_000  # characters; sqlcompat checks up to 500,000 per target, this runs it once per target
NOTES = ("Static analysis: the SQL was read, not run, and nothing was sent to any platform.",
         "Compatible means no known problem, not proof that it runs or gives the same results there.")


@dataclass(frozen=True)
class Finding:
    statement: int        # 1-based
    grade: str
    rewritten: str | None  # the SQL for the target, when it differs from what was written
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "grade": self.grade, "rewritten": self.rewritten,
                "reasons": list(self.reasons)}


def _worst(grades: list[str]) -> str:
    return max(grades, key=_RANK.__getitem__) if grades else "unverified"


def _normal(sql: str, dialect: str) -> str | None:
    """``sql`` as ``dialect`` reads and prints it, or None when it can't read it."""
    try:
        out = sqlglot.transpile(sql, read=dialect, write=dialect)
    except (ParseError, UnsupportedError, ValueError):
        return None
    return out[0] if len(out) == 1 else None


def _rules(rewritten: str, target: str) -> tuple[str, list[str]]:
    """sqlcompat's grade and reasons for SQL already written for ``target``."""
    report = sqlcompat.analyze(rewritten, target)
    reasons = [f"{d['rule']}: {d['message']} {d['action']}".strip() for d in report["diagnostics"]
               if d["severity"] in ("error", "warning", "unverified")]
    counts = report["counts"]
    grade = ("unsupported" if counts["error"] else "partial" if counts["warning"]
             else "unverified" if counts["unverified"] else "compatible")
    return grade, reasons


_QUALIFY_ONLY_INSIDE: dict[str, bool] = {}
_TAIL = ("order", "limit", "offset", "fetch")
QUALIFY_NOTE = "QUALIFY is rewritten as a filter in an outer query."
QUALIFY_STAR_NOTE = ("With SELECT *, the rewrite also returns its helper column (_w), so the result has one "
                     "column more than written. Select the columns by name to match it.")
QUALIFY_TAIL_NOTE = ("QUALIFY is rewritten as a filter in an outer query, with ORDER BY, LIMIT and OFFSET moved "
                     "outside it so that they still apply after the filter, as written.")


def _drops_qualify(dialect: str) -> bool:
    """Does sqlglot write QUALIFY for ``dialect`` as a subquery filter (sqlglot's eliminate_qualify)?"""
    if dialect not in _QUALIFY_ONLY_INSIDE:
        probe = sqlglot.parse_one("SELECT a FROM t QUALIFY a = 1")
        _QUALIFY_ONLY_INSIDE[dialect] = "QUALIFY" not in probe.sql(dialect=dialect).upper()
    return _QUALIFY_ONLY_INSIDE[dialect]


def _output_names(select: exp.Select) -> dict[str, exp.Expr] | None:
    """Each output column's identifier by its lower-cased name, or None when one has no name or two
    share one."""
    out: dict[str, exp.Expr] = {}
    for e in select.expressions:
        ident = e.args.get("alias") if isinstance(e, exp.Alias) else e.this if isinstance(e, exp.Column) else None
        if not isinstance(ident, exp.Identifier) or ident.name.lower() in out:
            return None
        out[ident.name.lower()] = e
    return out


def _same_column(key: exp.Column, projected: exp.Expr) -> bool:
    """Does ORDER BY ``key`` name the output column ``projected``? An unqualified name always does
    (an output name comes first in ORDER BY); a qualified one only when it selects that very column."""
    if not key.table:
        return True
    return isinstance(projected, exp.Column) and projected.name.lower() == key.name.lower() \
        and projected.table.lower() == key.table.lower()


def _alias_clash(select: exp.Select) -> bool:
    """Does a window in QUALIFY name a table's column that shares its name with an output alias?
    sqlglot's eliminate_qualify swaps such a column for the aliased expression, which reads another
    column."""
    aliases = {e.alias for e in select.expressions if isinstance(e, exp.Alias)}
    qualify = select.args.get("qualify")
    if not qualify:
        return False
    return any(c.table and c.name in aliases for w in qualify.find_all(exp.Window) for c in w.find_all(exp.Column))


def _qualify_last(select: exp.Select) -> exp.Select | str:
    """``select`` without QUALIFY, filtered in an outer query that then applies ORDER BY, LIMIT, OFFSET
    and FETCH, or the reason that can't be done safely.

    sqlglot's eliminate_qualify wraps the SELECT in a subquery that filters on the window function but
    leaves LIMIT and ORDER BY inside it, so rows are cut before the filter: a wrong answer, not an error.
    Here the tail is taken off first and put on the outer query."""
    inner = select.copy()
    for k in _TAIL:
        inner.set(k, None)
    star = inner.is_star
    names = None if star else _output_names(inner)
    if not star and names is None:
        return ("QUALIFY with ORDER BY, LIMIT or OFFSET is rewritten here only when every selected column has a "
                "name of its own. Give each column a distinct name, or test it there.")
    order = select.args.get("order")
    hidden: list[str] = []
    if order:
        order = order.copy()
        for ordered in order.expressions:
            key = ordered.this
            if isinstance(key, exp.Literal) and not key.is_string:
                continue  # ORDER BY 1: the outer query selects the same columns in the same order
            if isinstance(key, exp.Column) and not isinstance(key.this, exp.Star):
                if star and not key.table:
                    continue
                projected = (names or {}).get(key.name.lower())
                if projected is not None and _same_column(key, projected):
                    ordered.set("this", exp.column(key.this.copy()))
                    continue
            if star:
                return ("QUALIFY with SELECT * is rewritten here only when ORDER BY names plain columns. Select the "
                        "columns by name, or test it there.")
            hidden.append(f"_o{len(hidden) + 1}")
            inner.select(exp.alias_(key.copy(), hidden[-1]), copy=False)
            ordered.set("this", exp.column(hidden[-1]))
    outer = transforms.eliminate_qualify(inner)
    if not isinstance(outer, exp.Select):
        return "QUALIFY couldn't be rewritten here. Test it there."
    if hidden:  # ORDER BY reads them from the subquery; they aren't returned
        outer.set("expressions", [e for e in outer.expressions if e.alias_or_name not in hidden])
    for k in _TAIL:
        part = order if k == "order" else select.args.get(k)
        if part is not None:
            outer.set(k, part.copy())
    return outer


def _move_qualify_tails(tree: exp.Expr) -> tuple[exp.Expr, bool, str | None]:
    """``tree`` with every QUALIFY's ORDER BY and LIMIT moved after its filter (see _qualify_last),
    whether any moved, and the reason when one can't be."""
    tree = tree.copy()
    moved_any = False
    # Innermost first: an outer SELECT is rebuilt from a copy, which must already carry its inner rewrites.
    for select in reversed(list(tree.find_all(exp.Select))):
        if not select.args.get("qualify"):
            continue
        if _alias_clash(select):
            return tree, moved_any, ("A window in QUALIFY names a table's column that has the same name as an "
                                     "output alias, which the rewrite would read as the alias. Rename the alias, "
                                     "or test it there.")
        if not any(select.args.get(k) for k in _TAIL):
            continue
        moved = _qualify_last(select)
        if isinstance(moved, str):
            return tree, moved_any, moved
        moved_any = True
        if select is tree:
            tree = moved
        else:
            select.replace(moved)
    return tree, moved_any, None


def _statement(n: int, tree: exp.Expr, source: str, target: str) -> Finding:
    src, tgt = PLATFORMS[source]["sqlglot"], PLATFORMS[target]["sqlglot"]
    written = tree.sql(dialect=src)
    if isinstance(tree, exp.Command):
        return Finding(n, "unverified", None, ("sqlglot keeps this statement only as text, so it can't be checked.",))
    qualify_notes: list[str] = []
    qualifies = [s for s in tree.find_all(exp.Select) if s.args.get("qualify")]
    if qualifies and _drops_qualify(tgt):
        star = any(s.is_star for s in qualifies)
        tree, moved, problem = _move_qualify_tails(tree)
        if problem:
            return Finding(n, "unverified", None, (problem,))
        qualify_notes.append(QUALIFY_TAIL_NOTE if moved else QUALIFY_NOTE)
        if star:
            qualify_notes.append(QUALIFY_STAR_NOTE)
    try:
        rewritten = tree.sql(dialect=tgt, unsupported_level=ErrorLevel.RAISE)
    except UnsupportedError as exc:
        first = (str(exc).splitlines() or ["sqlglot can't write it for this platform"])[0]
        return Finding(n, "unsupported", None, (first,))
    # Text, not trees: a lenient reader takes LIMIT as T-SQL, but the Warehouse doesn't.
    same = rewritten == written
    grade = "compatible" if same else "translated"
    reasons: list[str] = []
    if QUALIFY_STAR_NOTE in qualify_notes:
        grade = "partial"
    rules = PLATFORMS[target]["rules"]
    if rules:
        found, reasons = _rules(rewritten, rules)
        if _RANK[found] > _RANK[grade]:
            grade = found
        reasons = qualify_notes + reasons
    else:
        if not same and _normal(rewritten, tgt) is None:
            return Finding(n, "unverified", rewritten, (("The rewrite doesn't read back as "
                                                        f"{PLATFORMS[target]['label']}, so it can't be trusted."),))
        unknown = sorted({f.name for f in tree.find_all(exp.Anonymous)})
        if unknown:
            grade = "unverified"
            reasons.append(f"Not checked: {', '.join(unknown)} (a function sqlglot doesn't know, passed through as is).")
        reasons.append(f"{PLATFORMS[target]['label']} has no rules of its own here; only the rewrite was checked.")
        reasons = qualify_notes + reasons
    return Finding(n, grade, None if same else rewritten, tuple(reasons))


def _snowflake_only(sql: str) -> dict[int, tuple[str, ...]]:
    """Statements that use something only Snowflake has (streams, tasks, stages...), by number."""
    out = {}
    for s in snowflake_sql.translate(sql).statements:
        only = tuple(f"{n.id}: only in Snowflake ({n.title})." for n in s.notes if n.id.startswith("SFN"))
        if s.verdict == "not_translatable" and only:
            out[s.statement] = only
    return out


def _snowflake_on_duckdb(sql: str) -> list[Finding]:
    """Snowflake SQL on DuckDB goes through snowflake_sql, which knows its rewrites and differences."""
    grade = {"translated": "translated", "difference": "partial", "not_translatable": "unsupported"}
    return [Finding(s.statement, grade[s.verdict], s.duckdb_sql, tuple(f"{n.id}: {n.reason} {n.action}".strip()
                                                                        for n in s.notes))
            for s in snowflake_sql.translate(sql).statements]


def portability(sql: str, source: str, targets: list[str] | None = None) -> dict[str, Any]:
    """For each target platform, a grade per statement and overall. Reads the SQL; nothing runs."""
    if source not in PLATFORMS:
        raise ValueError(f"Unknown platform {source!r}. Platforms: {', '.join(PLATFORMS)}.")
    chosen = list(targets) if targets is not None else [p for p in PLATFORMS if p != source]
    unknown = [t for t in chosen if t not in PLATFORMS]
    if unknown:
        raise ValueError(f"Unknown platform {unknown[0]!r}. Platforms: {', '.join(PLATFORMS)}.")
    report: dict[str, Any] = {"schema": SCHEMA, "source": {"id": source, "label": PLATFORMS[source]["label"]},
                              "grades": GRADES, "notes": list(NOTES), "targets": []}
    if len(sql) > MAX_SQL:
        report["problem"] = f"The SQL is longer than {MAX_SQL:,} characters, so it wasn't checked."
        return report
    try:
        trees = [t for t in sqlglot.parse(sql, read=PLATFORMS[source]["sqlglot"]) if t is not None]
    except ParseError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "it can't be read"
        report["problem"] = f"Could not read the SQL as {PLATFORMS[source]['label']}: {first}"
        return report
    if not trees:
        report["problem"] = "There is no SQL to check."
        return report
    report["statements"] = len(trees)
    snowflake_only = _snowflake_only(sql) if source == "snowflake" else {}
    for target in chosen:
        if source == "snowflake" and target == "duckdb":
            findings = _snowflake_on_duckdb(sql)
            checked = "rules"
        else:
            findings = [Finding(i, "unsupported", None, snowflake_only[i]) if i in snowflake_only and target != source
                        else _statement(i, t, source, target) for i, t in enumerate(trees, 1)]
            checked = "rules" if PLATFORMS[target]["rules"] else "rewrite"
        report["targets"].append({
            "id": target, "label": PLATFORMS[target]["label"], "checked_by": checked,
            "grade": _worst([f.grade for f in findings]), "statements": [f.as_dict() for f in findings]})
    return report
