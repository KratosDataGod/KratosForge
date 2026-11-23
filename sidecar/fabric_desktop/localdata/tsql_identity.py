"""Local T-SQL's IDENTITY columns as a Fabric Warehouse has them (WR-261), or refused by name.

From Microsoft Learn's Fabric Data Warehouse pages (IDENTITY columns, T-SQL surface area):

- IDENTITY is BIGINT only, with no seed or increment. Its values are unique, not always in order, and can have
  gaps. Here a ``BIGINT IDENTITY`` column takes its values from a DuckDB sequence, as the column's default
  ``nextval``: unique, and in order here. The sequence is made by the engine just before the CREATE TABLE (a
  statement this module marks with ``SETUP``), and kept when the table is dropped, so a table made again under the
  name goes on from the last number.
- ``SET IDENTITY_INSERT t ON`` lets a run give the column its own values, for one table at a time, until ``OFF`` or
  the end of the run. Without it, a value for the column is SQL Server's error 544. An INSERT with no column list
  fills the other columns, in order, as T-SQL does.
- ``SELECT ... INTO`` and ``CREATE TABLE ... AS SELECT`` carry IDENTITY when the column is selected as it is, once,
  from one table, as SQL Server carries it: the new table's column takes its values from the same sequence, so
  they stay unique in both.
- ``@@IDENTITY``, ``IDENT_CURRENT``, SEQUENCE objects and ``ALTER TABLE ... ADD`` of an IDENTITY column aren't
  supported there, and are refused here by name.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlglot import exp

SETUP = "/* IDENTITY */ "  # a statement the engine runs with its neighbour: before a CREATE, after a SELECT INTO
_PREFIX = "__identity_"
_NOT_YET = "This T-SQL can't be run on the local engine: "
_IDENTITY = (exp.AutoIncrementColumnConstraint, exp.GeneratedAsIdentityColumnConstraint)
_SEQUENCES = ("SEQUENCE objects aren't supported in a Fabric Warehouse. Use a BIGINT IDENTITY column, which numbers "
              "the rows an INSERT adds.")
_TEMP_SOURCE_NOTE = ("IDENTITY isn't carried here from a #temp table into a project table (SELECT ... INTO or "
                     "CREATE TABLE ... AS SELECT), because the #temp table's numbers end with the session. In a "
                     "Fabric Warehouse the new table's column is an IDENTITY column. Declare it in CREATE TABLE and "
                     "INSERT the rows instead.")
# The sequence a column default names, as DuckDB writes the default back: nextval('dbo."__identity_t_id"')
_NEXTVAL = re.compile(r"nextval\('((?:[^']|'')*" + _PREFIX + r"(?:[^']|'')*)'\)", re.IGNORECASE)


@dataclass
class Identity:
    """A table's IDENTITY column: its name, the DuckDB sequence its values come from, and every column of the
    table in order (for an INSERT with no column list)."""
    column: str
    sequence: str
    columns: list[str] = field(default_factory=list)


Key = tuple[str, str]


def key(table: exp.Table) -> Key:
    """A table's name as the catalog knows it: ("#", name) for a #temp table, (schema, name) for another, lower case."""
    if table.name.startswith("#") or isinstance(table.this, exp.Identifier) and table.this.args.get("temporary"):
        return "#", ("#" + table.name.lstrip("#")).lower()  # before or after tsql._temp_tables names it #t
    return (table.db or "dbo").lower(), table.name.lower()


def from_default(default: str | None) -> str | None:
    """The sequence a column's DuckDB default takes its values from, when it is an IDENTITY column's."""
    found = _NEXTVAL.fullmatch((default or "").strip())
    return found[1].replace("''", "'") if found else None


class Script:
    """What one T-SQL run knows about IDENTITY: the tables the project has (read once, when first needed), the
    tables the script makes or drops, and the table IDENTITY_INSERT is ON for."""

    def __init__(self, load: Callable[[], dict[Key, Identity]] | None = None,
                 columns: dict[str, dict[str, dict[str, str]]] | None = None):
        self._load = load
        self._known: dict[Key, Identity] | None = None
        self._columns = columns
        self.made: dict[Key, Identity | None] = {}  # None: made without IDENTITY, or dropped
        self.dropped: set[Key] = set()
        self.insert_on: tuple[Key, str] | None = None

    def find(self, table: exp.Table) -> Identity | None:
        k = key(table)
        if k in self.made:
            return self.made[k]
        if self._known is None:
            self._known = self._load() if self._load is not None else {}
        return self._known.get(k)

    def _exists(self, table: exp.Table) -> bool | None:
        """Whether the table is there: None when the run has no catalog to look in."""
        k = key(table)
        if k in self.dropped:
            return False
        if k in self.made:
            return True
        if self._columns is None:
            return None
        if k[0] == "#":
            return any(k[1] in {t.lower() for t in tables} for tables in self._columns.values())
        return any(s.lower() == k[0] and k[1] in {t.lower() for t in tables} for s, tables in self._columns.items())

    # SET IDENTITY_INSERT

    def set_insert(self, tree: exp.Expression) -> bool:
        """True when ``tree`` is ``SET IDENTITY_INSERT table ON|OFF``, which is taken here and runs nothing."""
        found = _identity_insert(tree)
        if found is None:
            return False
        table, on = found
        written = table.sql(dialect="tsql")
        ident = self.find(table)
        if ident is None:
            exists = self._exists(table)
            if exists is False:
                raise ValueError(f'Cannot find the object "{table.name}" because it does not exist or you do not '
                                 "have permissions.")
            if exists:
                raise ValueError(f"Table '{written}' does not have the identity property. Cannot perform SET "
                                 "operation.")
        k = key(table)
        if on:
            if self.insert_on is not None and self.insert_on[0] != k:
                raise ValueError(f"IDENTITY_INSERT is already ON for table '{self.insert_on[1]}'. Cannot perform "
                                 f"SET operation for table '{written}'.")
            self.insert_on = (k, written)
        elif self.insert_on is not None and self.insert_on[0] == k:
            self.insert_on = None
        return True

    # Each statement

    def statement(self, tree: exp.Expression) -> tuple[list[str], list[str], list[str]]:
        """IDENTITY in ``tree``, which is changed in place: the statements to run before it and after it, and notes."""
        if isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() == "TABLE":
            name = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            if not isinstance(name, exp.Table):
                return [], [], []
            self.dropped.discard(key(name))
            if isinstance(tree.this, exp.Schema):
                return self._create(tree.this, name), [], []
            query = tree.expression
            if isinstance(query, exp.Select):
                return [], *self._carry(query, name)
            self.made[key(name)] = None
            return [], [], []
        if isinstance(tree, exp.Select) and isinstance(tree.args.get("into"), exp.Into) \
                and isinstance(tree.args["into"].this, exp.Table):
            self.dropped.discard(key(tree.args["into"].this))
            return [], *self._carry(tree, tree.args["into"].this)
        if isinstance(tree, exp.Drop) and (tree.args.get("kind") or "").upper() == "TABLE":
            for table in tree.args.get("tables") or []:
                if isinstance(table, exp.Table):
                    self.made[key(table)] = None
                    self.dropped.add(key(table))
                    if self.insert_on is not None and self.insert_on[0] == key(table):
                        self.insert_on = None
        elif isinstance(tree, exp.Insert):
            self._insert(tree)
        elif isinstance(tree, exp.Update) and isinstance(tree.this, exp.Table):
            self._update(tree)
        return [], [], []

    def merge(self, tree: exp.Expression) -> None:
        """MERGE's INSERT and UPDATE of an IDENTITY column, before tsql_merge reads its clauses: an INSERT with no
        column list fills the columns but the IDENTITY one."""
        if isinstance(tree, exp.Merge) and isinstance(tree.this, exp.Table):
            self._merge(tree)

    def _create(self, schema: exp.Schema, name: exp.Table) -> list[str]:
        defs = [c for c in schema.expressions if isinstance(c, exp.ColumnDef)]
        marked = [(c, k) for c in defs for k in c.args.get("constraints") or []
                  if isinstance(k.args.get("kind"), _IDENTITY)]
        if len(marked) > 1:
            raise ValueError(f"Multiple identity columns specified for table '{name.name}'. Only one identity column "
                             "per table is allowed.")
        if not marked:
            self.made[key(name)] = None
            return []
        column, constraint = marked[0]
        _check_identity(column, constraint)
        temporary = name.name.startswith("#")
        sequence_name = f"{_PREFIX}{name.name}_{column.name}"
        if temporary:
            sequence = f"temp.main.{_quote(sequence_name)}"
            setup = f"CREATE TEMPORARY SEQUENCE IF NOT EXISTS {_quote(sequence_name)}"
        else:
            sequence = f"{_quote(name.db or 'dbo')}.{_quote(sequence_name)}"
            setup = f"CREATE SEQUENCE IF NOT EXISTS {sequence}"
        constraint.set("kind", exp.DefaultColumnConstraint(
            this=exp.Anonymous(this="nextval", expressions=[exp.Literal.string(sequence)])))
        if not any(isinstance(k.args.get("kind"), exp.NotNullColumnConstraint)
                   for k in column.args.get("constraints") or []):
            column.append("constraints", exp.ColumnConstraint(kind=exp.NotNullColumnConstraint()))
        self.made[key(name)] = Identity(column.name, sequence, [c.name for c in defs])
        return [SETUP + setup]

    def _carry(self, query: exp.Select, target: exp.Table) -> tuple[list[str], list[str]]:
        """SELECT ... INTO and CREATE TABLE ... AS SELECT: the new table's IDENTITY column, as SQL Server carries it.
        Not carried with a join, GROUP BY, an aggregate or a UNION, or when the column is in an expression or
        selected more than once, as in SQL Server."""
        self.made[key(target)] = None
        source = query.args.get("from_")
        table = source.this if isinstance(source, exp.From) else None
        if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier) or query.args.get("joins") \
                or query.args.get("group") or any(isinstance(s, exp.AggFunc) for p in query.selects
                                                  for s in p.find_all(exp.AggFunc)):
            return [], []
        ident = self.find(table)
        if ident is None:
            return [], []
        names: list[str] = []
        carried: list[str] = []
        for projection in query.selects:
            if isinstance(projection, exp.Star) or isinstance(projection, exp.Column) \
                    and isinstance(projection.this, exp.Star):
                if not ident.columns:
                    return [], []
                names += ident.columns
                carried.append(ident.column)
                continue
            inner = projection.this if isinstance(projection, exp.Alias) else projection
            names.append(projection.alias_or_name)
            if isinstance(inner, exp.Column) and inner.name.lower() == ident.column.lower():
                carried.append(projection.alias_or_name)
        if len(carried) != 1:
            return [], []
        if ident.sequence.lower().startswith("temp.") and not target.name.startswith("#"):
            return [], [_TEMP_SOURCE_NOTE]
        written = f"{_quote(target.db or 'dbo')}.{_quote(target.name)}" if not target.name.startswith("#") \
            else _quote(target.name)
        self.made[key(target)] = Identity(carried[0], ident.sequence, names)
        column = _quote(carried[0])
        sequence = ident.sequence.replace("'", "''")
        return [SETUP + f"ALTER TABLE {written} ALTER COLUMN {column} SET DEFAULT nextval('{sequence}')",
                SETUP + f"ALTER TABLE {written} ALTER COLUMN {column} SET NOT NULL"], []

    def _explicit(self, table: exp.Table, ident: Identity, columns: list[str] | None,
                  set_columns: Callable[[list[str]], None]) -> None:
        on = self.insert_on is not None and self.insert_on[0] == key(table)
        if columns is None:
            if on:
                raise ValueError(f"An explicit value for the identity column in table '{table.name}' can only be "
                                 "specified when a column list is used and IDENTITY_INSERT is ON.")
            if not ident.columns:
                return  # a table this run can't list: DuckDB says the values don't match its columns
            set_columns([c for c in ident.columns if c.lower() != ident.column.lower()])
            return
        if not on and ident.column.lower() in {c.lower() for c in columns}:
            raise ValueError(f"Cannot insert explicit value for identity column in table '{table.name}' when "
                             "IDENTITY_INSERT is set to OFF.")

    def _insert(self, tree: exp.Insert) -> None:
        target = tree.this
        table = target.this if isinstance(target, exp.Schema) else target
        if not isinstance(table, exp.Table):
            return
        ident = self.find(table)
        if ident is None:
            return
        listed = [c.name for c in target.expressions] if isinstance(target, exp.Schema) else None

        def set_columns(names: list[str]) -> None:
            tree.set("this", exp.Schema(this=table.copy(), expressions=[exp.to_identifier(n) for n in names]))
        self._explicit(table, ident, listed, set_columns)

    def _update(self, tree: exp.Update) -> None:
        ident = self.find(tree.this)
        if ident is None:
            return
        for assignment in tree.expressions:
            if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column) \
                    and assignment.this.name.lower() == ident.column.lower():
                raise ValueError(f"Cannot update identity column '{assignment.this.name}'.")

    def _merge(self, tree: exp.Merge) -> None:
        table = tree.this
        ident = self.find(table)
        if ident is None:
            return
        for when in tree.find_all(exp.When):
            then = when.args.get("then")
            if isinstance(then, exp.Update):
                for assignment in then.expressions:
                    if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column) \
                            and assignment.this.name.lower() == ident.column.lower():
                        raise ValueError(f"Cannot update identity column '{assignment.this.name}'.")
            if not isinstance(then, exp.Insert):
                continue
            listed = [c.name for c in then.this.expressions] if isinstance(then.this, exp.Tuple) else None

            def set_columns(names: list[str], then: exp.Insert = then) -> None:
                then.set("this", exp.Tuple(expressions=[exp.column(n, quoted=True) for n in names]))
            self._explicit(table, ident, listed, set_columns)


def refuse(tree: exp.Expression) -> None:
    """What a Fabric Warehouse doesn't have, by name: SEQUENCE objects, @@IDENTITY, IDENT_CURRENT, and ALTER TABLE
    ... ADD of an IDENTITY column."""
    kind = (tree.args.get("kind") or "").upper() if isinstance(tree, (exp.Create, exp.Drop, exp.Alter)) else ""
    if kind == "SEQUENCE" or tree.find(exp.NextValueFor) is not None:
        raise ValueError(_NOT_YET + _SEQUENCES)
    if isinstance(tree, exp.Command) and tree.name.upper() in ("ALTER", "CREATE", "DROP") \
            and str(tree.args.get("expression") or "").strip().upper().startswith("SEQUENCE"):
        raise ValueError(_NOT_YET + _SEQUENCES)
    for p in tree.find_all(exp.Parameter):
        if p.sql(dialect="tsql").upper() == "@@IDENTITY":
            raise ValueError(_NOT_YET + "@@IDENTITY isn't available in a Fabric Warehouse. Read the new rows back "
                             "with a SELECT, such as SELECT MAX(id) FROM the table.")
    for call in tree.find_all(exp.Anonymous):
        if str(call.this).upper() == "IDENT_CURRENT":
            raise ValueError(_NOT_YET + "IDENT_CURRENT isn't available in a Fabric Warehouse. Read the table's "
                             "highest value with SELECT MAX(id) FROM the table.")
    if isinstance(tree, exp.Alter) and kind == "TABLE":
        for action in tree.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef) and any(isinstance(c.args.get("kind"), _IDENTITY)
                                                         for c in action.args.get("constraints") or []):
                raise ValueError(_NOT_YET + f"ALTER TABLE ... ADD of an IDENTITY column ({action.name}) isn't "
                                 "supported in a Fabric Warehouse. Declare it in CREATE TABLE.")


def unsupported(messages: list[str]) -> list[str]:
    """sqlglot's "contains unsupported syntax" messages, without SET IDENTITY_INSERT (read here from the Command it
    falls back to). ALTER SEQUENCE, which it can't read either, is refused by name."""
    kept = []
    for message in messages:
        if re.match(r"'\s*ALTER\s+SEQUENCE\b", message, re.IGNORECASE):
            raise ValueError(_NOT_YET + _SEQUENCES)
        if not re.match(r"'\s*SET\s+IDENTITY_INSERT\b", message, re.IGNORECASE):
            kept.append(message)
    return kept


def _check_identity(column: exp.ColumnDef, constraint: exp.ColumnConstraint) -> None:
    kind = constraint.args.get("kind")
    if isinstance(kind, exp.GeneratedAsIdentityColumnConstraint) and (
            kind.args.get("start") is not None or kind.args.get("increment") is not None):
        seed = kind.args.get("start")
        step = kind.args.get("increment")
        written = f"IDENTITY({seed.sql() if seed else ''}, {step.sql() if step else ''})"
        raise ValueError(_NOT_YET + f"IDENTITY with a seed or increment ({written}, column {column.name}) isn't "
                         "supported in a Fabric Warehouse. Write BIGINT IDENTITY, with neither.")
    data_type = column.args.get("kind")
    if not (isinstance(data_type, exp.DataType) and data_type.this == exp.DataType.Type.BIGINT):
        raise ValueError(_NOT_YET + f"IDENTITY on column {column.name}, which isn't BIGINT, isn't supported in a "
                         "Fabric Warehouse, which takes IDENTITY on BIGINT only. Make the column BIGINT IDENTITY.")


def _identity_insert(tree: exp.Expression) -> tuple[exp.Table, bool] | None:
    """The table and ON (True) or OFF of ``SET IDENTITY_INSERT table ON|OFF``, which sqlglot reads as a SET or as a
    Command, depending on how the name is written."""
    if isinstance(tree, exp.Command) and tree.name.upper() == "SET":
        text = str(tree.args.get("expression") or "")
    elif isinstance(tree, exp.Set):
        text = tree.sql(dialect="tsql")[len("SET "):] if len(tree.expressions) == 1 else ""
    else:
        return None
    found = re.fullmatch(r"\s*IDENTITY_INSERT\s*(?:=\s*)?(.+?)\s+(?:AS\s+)?(ON|OFF)\s*;?\s*", text, re.IGNORECASE)
    if found is None:
        return None
    return exp.to_table(found[1], dialect="tsql"), found[2].upper() == "ON"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
