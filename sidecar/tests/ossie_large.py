"""Large generated semantic models (OSSIE-012b): N tables of M columns and K measures, chained by relationships,
with one security role and one calculation group, as a parsed ``model.bim`` and as Ossie 0.2.0.dev0 text.

Both describe the same model. Each size gives the same model on every run: the choices come from
``random.Random(seed)``, never from the clock or the global generator. Synthetic names and values only.

- Table ``tNNN`` has columns ``cNNN``; ``c000`` is the integer key the next table's relationship points to.
- Measures ``tNNN_mNNN`` cycle through the parts of the L2 subset (SUM, AVERAGE, MIN, MAX over a number column,
  COUNTROWS, DISTINCTCOUNTNOBLANK, DIVIDE of two earlier measures) and, every eighth one, a CALCULATE that is
  outside it. In Ossie each has its DAX and, except COUNTROWS and CALCULATE, its SQL (ANSI_SQL).
- Relationship ``rNNN`` joins ``tNNN[c001]`` to the next table's key.
- Role ``readers`` filters the first table; calculation group ``periods`` has two items. In Ossie both travel in
  the model's Power BI section, as an export writes them.

``tests/test_ossie_large.py`` checks that the work grows about linearly with the model; ``tools/ossie_large_timing.py``
times each stage for given sizes.
"""

from __future__ import annotations

import json
import random
from typing import Any

import yaml

SEED = 12
# TMSL data type -> Ossie datatype, as the converter maps them (its TMSL_TO_OSSIE_DATATYPE).
TYPES = {"int64": "Integer", "decimal": "Decimal", "double": "Float", "string": "String", "dateTime": "DateTime",
         "boolean": "Boolean"}
NUMBERS = ("int64", "decimal", "double")
_AGG_SQL = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX"}
ROLE = "readers"
CALC_GROUP = "periods"


def _table(i: int) -> str:
    return f"t{i:03d}"


def _column(j: int) -> str:
    return f"c{j:03d}"


def spec(tables: int, columns: int, measures: int, seed: int = SEED) -> list[dict[str, Any]]:
    """The model both formats are written from: per table its columns (name, TMSL type) and measures (name, DAX,
    SQL or None when the measure is outside the L2 subset)."""
    if tables < 1 or columns < 2 or measures < 0:
        raise ValueError("a large model has at least one table and two columns per table")
    rng = random.Random(seed)
    out = []
    for i in range(tables):
        t = _table(i)
        cols = [(_column(0), "int64"), (_column(1), "int64")]
        cols += [(_column(j), rng.choice(tuple(TYPES))) for j in range(2, columns)]
        numbers = [c for c, ty in cols if ty in NUMBERS]
        ms: list[tuple[str, str, str | None]] = []
        for k in range(measures):
            name = f"{t}_m{k:03d}"
            pick = k % 8
            if pick == 7:
                col = rng.choice(numbers)
                ms.append((name, f"CALCULATE(SUM('{t}'[{col}]), ALL('{t}'))", None))
            elif pick == 6 and k >= 2:
                a, b = ms[k - 1][0], ms[k - 2][0]
                ms.append((name, f"DIVIDE([{a}], [{b}])", f"{a} / NULLIF({b}, 0)"))
            elif pick == 5:
                col = rng.choice([c for c, _ in cols])
                ms.append((name, f"DISTINCTCOUNTNOBLANK('{t}'[{col}])", f"COUNT(DISTINCT {t}.{col})"))
            elif pick == 4:
                ms.append((name, f"COUNTROWS('{t}')", None))  # DAX only, as an export writes it
            else:
                agg = rng.choice(tuple(_AGG_SQL))
                col = rng.choice(numbers)
                ms.append((name, f"{agg}('{t}'[{col}])", f"{_AGG_SQL[agg]}({t}.{col})"))
        out.append({"name": t, "columns": cols, "measures": ms})
    return out


def _role(first: str) -> dict[str, Any]:
    return {"name": ROLE, "modelPermission": "read", "tablePermissions": [{"name": first, "filterExpression": "[c001] > 0"}]}


def _calc_group() -> dict[str, Any]:
    return {"name": CALC_GROUP, "calculationGroup": {"precedence": 1, "calculationItems": [
        {"name": "current", "expression": "SELECTEDMEASURE()", "ordinal": 0},
        {"name": "doubled", "expression": "SELECTEDMEASURE() * 2", "ordinal": 1}]},
        "columns": [{"name": "period", "dataType": "string", "sourceColumn": "Name"},
                    {"name": "Ordinal", "dataType": "int64", "sourceColumn": "Ordinal", "isHidden": True}],
        "partitions": [{"name": CALC_GROUP, "mode": "import", "source": {"type": "calculationGroup"}}]}


def _relationships(model: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(f"r{i:03d}", model[i]["name"], model[i + 1]["name"]) for i in range(len(model) - 1)]


def bim(tables: int, columns: int, measures: int, seed: int = SEED) -> dict[str, Any]:
    """The model as a parsed ``model.bim``."""
    model = spec(tables, columns, measures, seed)
    out_tables: list[dict[str, Any]] = [{
        "name": t["name"],
        "columns": [{"name": c, "dataType": ty, "sourceColumn": c, **({"isKey": True} if c == _column(0) else {})}
                    for c, ty in t["columns"]],
        "measures": [{"name": m, "expression": dax} for m, dax, _ in t["measures"]],
        "partitions": [{"name": t["name"], "mode": "directLake", "source": {
            "type": "entity", "entityName": t["name"], "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}],
    } for t in model]
    out_tables.append(_calc_group())
    return {"name": "large", "compatibilityLevel": 1604, "model": {
        "culture": "en-US",
        "tables": out_tables,
        "relationships": [{"name": r, "fromTable": a, "fromColumn": _column(1), "toTable": b, "toColumn": _column(0)}
                          for r, a, b in _relationships(model)],
        "roles": [_role(model[0]["name"])]}}


def _extension(data: dict[str, Any]) -> list[dict[str, str]]:
    return [{"vendor_name": "POWER_BI", "data": json.dumps({"_v": 2, **data}, sort_keys=True)}]


def ossie_doc(tables: int, columns: int, measures: int, seed: int = SEED) -> dict[str, Any]:
    """The model as an Ossie 0.2.0.dev0 document."""
    model = spec(tables, columns, measures, seed)
    metrics = []
    for t in model:
        for m, dax, sql in t["measures"]:
            dialects = ([{"dialect": "ANSI_SQL", "expression": sql}] if sql else []) + [{"dialect": "DAX", "expression": dax}]
            metrics.append({"name": m, "expression": {"dialects": dialects},
                            "custom_extensions": _extension({"table": t["name"]})})
    return {
        "version": "0.2.0.dev0", "name": "large",
        "datasets": [{"name": t["name"], "source": f"dbo.{t['name']}", "primary_key": [_column(0)], "fields": [
            {"name": c, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": c}]}, "datatype": TYPES[ty]}
            for c, ty in t["columns"]]} for t in model],
        "relationships": [{"name": r, "from": a, "to": b, "from_columns": [_column(1)], "to_columns": [_column(0)]}
                          for r, a, b in _relationships(model)],
        "metrics": metrics,
        "custom_extensions": _extension({"culture": "en-US", "document": {"compatibilityLevel": 1604}, "roles": [_role(model[0]["name"])],
                                         "excludedTables": [_calc_group()]}),
    }


def ossie(tables: int, columns: int, measures: int, seed: int = SEED) -> str:
    """The model as Ossie 0.2.0.dev0 YAML text."""
    return yaml.safe_dump(ossie_doc(tables, columns, measures, seed), sort_keys=False)


def counts(tables: int, columns: int, measures: int) -> dict[str, int]:
    """How many objects of each kind a generated model has (the calculation group's table aside)."""
    return {"tables": tables, "columns": tables * columns, "measures": tables * measures,
            "relationships": tables - 1, "roles": 1, "calculation_groups": 1}
