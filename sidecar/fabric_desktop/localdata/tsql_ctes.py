"""Local T-SQL's common table expressions as a Fabric Warehouse has them (WR-230), or refused by name.

From Microsoft's T-SQL surface area page for Warehouse in Microsoft Fabric:

- Recursive queries aren't supported. In T-SQL a CTE is recursive when its own query names it (there is no
  RECURSIVE keyword), so such a CTE is refused here by name before anything runs. DuckDB would stop on its own
  "Circular reference" binder error, and with WITH RECURSIVE a CTE with no end would run until memory runs out;
  neither happens. ``OPTION (MAXRECURSION n)`` only limits a recursive CTE, so it is refused by name too.
- Nested CTEs (a WITH inside a CTE) are a preview feature there. They run here, with a note that says so.
- Several CTEs in one WITH, a CTE's column list, and a CTE before INSERT, UPDATE ... FROM, DELETE and SELECT run
  here as they do there: a CTE sees the CTEs before it, and a later one's name is a table's. Two CTEs of one
  name stop with SQL Server's own message.
- UPDATE or DELETE through a CTE (``WITH a AS (...) UPDATE a SET ...``) changes the CTE's table in SQL Server.
  The surface area page doesn't say whether a warehouse does, and DuckDB changes tables only, so it is refused by
  name with the way to write it against the table.
"""

from __future__ import annotations

from sqlglot import exp

_NOT_YET = "This T-SQL can't be run on the local engine: "
_UNROLL = "Unroll the levels you need with joins, or compute the hierarchy in a notebook."
NESTED_NOTE = ("A WITH inside a CTE runs here. In a Fabric Warehouse nested CTEs are a preview feature, so test it "
               "there before you rely on it.")


def check(tree: exp.Expression) -> list[str]:
    """Refuses a recursive CTE, MAXRECURSION and a change through a CTE by name; notes a nested CTE."""
    notes: list[str] = []
    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            raise ValueError(_NOT_YET + "WITH RECURSIVE isn't T-SQL, and a Fabric Warehouse doesn't run recursive "
                             "queries. " + _UNROLL)
        seen: set[str] = set()
        for cte in with_.expressions:
            name = cte.alias_or_name.lower()
            if name in seen:  # SQL Server's own words, where DuckDB's parser error would name DuckDB
                raise ValueError(f"Duplicate common table expression name '{cte.alias_or_name}' was specified.")
            seen.add(name)
            if _names_itself(cte):
                raise ValueError(_NOT_YET + f"the CTE '{cte.alias_or_name}' names itself, which makes it recursive, "
                                 "and a Fabric Warehouse doesn't run recursive queries. " + _UNROLL)
        if with_.find_ancestor(exp.CTE) is not None and NESTED_NOTE not in notes:
            notes.append(NESTED_NOTE)
    for option in tree.find_all(exp.QueryOption):
        if option.name.upper() == "MAXRECURSION":
            raise ValueError(_NOT_YET + "OPTION (MAXRECURSION ...) isn't carried yet. It limits a recursive CTE, "
                             "and a Fabric Warehouse doesn't run recursive queries. Remove the hint.")
    if isinstance(tree, (exp.Update, exp.Delete)):
        _changes_a_table(tree)
    return notes


def _names_itself(cte: exp.CTE) -> bool:
    name = cte.alias_or_name.lower()
    for table in cte.this.find_all(exp.Table):
        if table.args.get("db") or table.name.lower() != name:
            continue
        if not _shadowed(table, cte, name):
            return True
    return False


def _shadowed(table: exp.Table, cte: exp.CTE, name: str) -> bool:
    """True when a WITH nested between ``table`` and ``cte`` defines ``name`` itself."""
    node = table.parent
    while node is not None and node is not cte:
        inner = node.args.get("with_") if isinstance(node, exp.Expression) else None
        if isinstance(inner, exp.With) and any(c.alias_or_name.lower() == name for c in inner.expressions):
            return True
        node = node.parent
    return False


def _changes_a_table(tree: exp.Update | exp.Delete) -> None:
    with_ = tree.args.get("with_")
    if not isinstance(with_, exp.With):
        return
    ctes = {c.alias_or_name.lower() for c in with_.expressions}
    # UPDATE x SET ... FROM c AS x: the target is the alias of a CTE in FROM
    for table in tree.find_all(exp.Table):
        if table.alias and not table.args.get("db") and table.name.lower() in ctes \
                and table.find_ancestor(exp.CTE) is None:
            ctes.add(table.alias.lower())
    targets = [tree.this] if isinstance(tree, exp.Update) else [tree.this, *(tree.args.get("tables") or [])]
    verb = "UPDATE" if isinstance(tree, exp.Update) else "DELETE"
    for target in targets:
        if isinstance(target, exp.Table) and not target.args.get("db") and target.name.lower() in ctes:
            raise ValueError(_NOT_YET + f"{verb} through the CTE '{target.name}' isn't carried yet. The local engine "
                             f"changes tables only. Name the table in {verb}, and put the CTE's filter in WHERE.")
