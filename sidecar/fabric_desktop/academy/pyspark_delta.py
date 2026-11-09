"""``from delta.tables import DeltaTable`` in an Academy PySpark exercise: the exercise's Delta tables
(``tables/<schema>/<table>``) read, merged into, updated and deleted from by delta-rs.

It runs in the learner's process as part of the generated ``kf_learner`` package (learner_pkg.py).

    DeltaTable.forName(spark, "silver.orders").alias("t")
        .merge(updates.alias("s"), "t.order_id = s.order_id")
        .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()

Conditions and values are SQL text (or a Column), run by delta-rs's DataFusion: Spark SQL's
comparisons, AND/OR, CASE and common functions read the same; a Spark-only function may not.
One MERGE is one commit, and a source row that matches two target rows fails it, as in Spark.
This is not delta-spark: there is no OPTIMIZE, VACUUM or RESTORE here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import deltalake
import pyarrow as pa
from duckdb.experimental.spark.sql.dataframe import DataFrame

TABLES_PREFIXES = ("Tables/", "/lakehouse/default/Tables/", "lakehouse/default/Tables/")


def _sql(value: Any) -> str:
    """A condition or value: SQL text, or a Column (its expression, which DuckDB prints with
    double-quoted names, as DataFusion reads them)."""
    if isinstance(value, str):
        return value
    expr = getattr(value, "expr", None)
    if expr is None:
        raise TypeError(f"Expected SQL text or a Column, got {type(value).__name__}.")
    return str(expr)


def _source(df: Any) -> tuple[pa.Table, str]:
    relation = getattr(df, "relation", None)
    if relation is None:
        raise TypeError("DeltaTable.merge needs a spark DataFrame as its source.")
    alias = getattr(relation, "alias", "") or ""
    if not alias or alias.startswith(("unnamed_relation", "arrow_object")):
        alias = "source"
    return (getattr(relation, "to_arrow_table", None) or relation.arrow)(), alias


class DeltaTable:
    """delta-spark's DeltaTable, for the exercise's tables."""

    _session: Any = None   # the exercise's spark, set by pyspark_shim.session

    def __init__(self, name: str, path: Path, alias: str = ""):
        self._name, self._path, self._alias = name, path, alias

    def _table(self) -> deltalake.DeltaTable:
        return deltalake.DeltaTable(str(self._path))

    @classmethod
    def forName(cls, sparkSession: Any, tableOrViewName: str) -> DeltaTable:  # noqa: N802, N803
        from .pyspark_shim import table_path
        spark = sparkSession or cls._session
        path = table_path(spark.work, tableOrViewName)
        if not (path / "_delta_log").is_dir():
            raise ValueError(f"There's no table {tableOrViewName}")
        return cls(tableOrViewName, path)

    @classmethod
    def forPath(cls, sparkSession: Any, path: str) -> DeltaTable:  # noqa: N802, N803
        text = str(path).replace("\\", "/")
        prefix = next((p for p in TABLES_PREFIXES if text.startswith(p)), None)
        if prefix is None:
            raise ValueError("In an exercise, DeltaTable.forPath takes a Tables/ path (Tables/silver/orders); "
                             "or use DeltaTable.forName.")
        return cls.forName(sparkSession, ".".join(p for p in text[len(prefix):].split("/") if p))

    @classmethod
    def isDeltaTable(cls, sparkSession: Any, identifier: str) -> bool:  # noqa: N802, N803
        try:
            (cls.forPath if "/" in str(identifier) else cls.forName)(sparkSession, identifier)
        except ValueError:
            return False
        return True

    def alias(self, aliasName: str) -> DeltaTable:  # noqa: N803
        return DeltaTable(self._name, self._path, aliasName)

    def toDF(self) -> DataFrame:  # noqa: N802
        return self._session.table(self._name)

    def history(self, limit: int | None = None) -> DataFrame:
        rows = [{"version": h.get("version"), "timestamp": h.get("timestamp"), "operation": h.get("operation")}
                for h in self._table().history(limit)]
        return DataFrame(self._session.conn.from_arrow(pa.Table.from_pylist(rows)), self._session)

    def merge(self, source: Any, condition: Any) -> MergeBuilder:
        return MergeBuilder(self, source, condition)

    def update(self, condition: Any = None, set: dict[str, Any] | None = None) -> None:  # noqa: A002
        if not set:
            raise ValueError("update needs the columns to set, as a dict of column name to SQL text or Column.")
        self._table().update(updates={str(k): _sql(v) for k, v in set.items()},
                             predicate=_sql(condition) if condition is not None else None)

    def updateExpr(self, condition: Any = None, set: dict[str, str] | None = None) -> None:  # noqa: N802, A002
        self.update(condition, set)

    def delete(self, condition: Any = None) -> None:
        self._table().delete(_sql(condition) if condition is not None else None)

    def _not_here(self, what: str) -> Any:
        raise NotImplementedError(f"DeltaTable.{what} isn't in an Academy exercise; it works in a Fabric notebook.")

    def optimize(self, *_a: Any, **_k: Any) -> Any:
        return self._not_here("optimize")

    def vacuum(self, *_a: Any, **_k: Any) -> Any:
        return self._not_here("vacuum")

    def restoreToVersion(self, *_a: Any) -> Any:  # noqa: N802
        return self._not_here("restoreToVersion")

    def restoreToTimestamp(self, *_a: Any) -> Any:  # noqa: N802
        return self._not_here("restoreToTimestamp")


class MergeBuilder:
    """``DeltaTable.merge(source, condition).whenMatched...().execute()``, run by delta-rs."""

    def __init__(self, table: DeltaTable, source: Any, condition: Any):
        self._table, self._source, self._on = table, source, _sql(condition)
        self._clauses: list[tuple[str, dict[str, Any]]] = []

    def _add(self, method: str, condition: Any, **kw: Any) -> MergeBuilder:
        if condition is not None:
            kw["predicate"] = _sql(condition)
        self._clauses.append((method, kw))
        return self

    @staticmethod
    def _sets(values: dict[str, Any] | None, what: str) -> dict[str, str]:
        if not values:
            raise ValueError(f"{what} needs the columns to set, as a dict of column name to SQL text or Column.")
        return {str(k): _sql(v) for k, v in values.items()}

    def whenMatchedUpdate(self, condition: Any = None, set: dict[str, Any] | None = None) -> MergeBuilder:  # noqa: N802, A002
        return self._add("when_matched_update", condition, updates=self._sets(set, "whenMatchedUpdate"))

    def whenMatchedUpdateAll(self, condition: Any = None) -> MergeBuilder:  # noqa: N802
        return self._add("when_matched_update_all", condition)

    def whenMatchedDelete(self, condition: Any = None) -> MergeBuilder:  # noqa: N802
        return self._add("when_matched_delete", condition)

    def whenNotMatchedInsert(self, condition: Any = None, values: dict[str, Any] | None = None) -> MergeBuilder:  # noqa: N802
        return self._add("when_not_matched_insert", condition, updates=self._sets(values, "whenNotMatchedInsert"))

    def whenNotMatchedInsertAll(self, condition: Any = None) -> MergeBuilder:  # noqa: N802
        return self._add("when_not_matched_insert_all", condition)

    def whenNotMatchedBySourceUpdate(self, condition: Any = None, set: dict[str, Any] | None = None) -> MergeBuilder:  # noqa: N802, A002
        return self._add("when_not_matched_by_source_update", condition,
                         updates=self._sets(set, "whenNotMatchedBySourceUpdate"))

    def whenNotMatchedBySourceDelete(self, condition: Any = None) -> MergeBuilder:  # noqa: N802
        return self._add("when_not_matched_by_source_delete", condition)

    def withSchemaEvolution(self) -> MergeBuilder:  # noqa: N802
        raise NotImplementedError("MERGE with schema evolution isn't in an Academy exercise: add the new columns "
                                  "first (an append with mergeSchema), then merge.")

    def execute(self) -> None:
        if not self._clauses:
            raise ValueError("The merge has no whenMatched or whenNotMatched clause, so there is nothing to do.")
        data, alias = _source(self._source)
        target = self._table._alias or self._table._name.rpartition(".")[2]
        merger = self._table._table().merge(source=data, predicate=self._on, source_alias=alias, target_alias=target)
        for method, kw in self._clauses:
            merger = getattr(merger, method)(**kw)
        merger.execute()
