"""Table clones and warehouse snapshots in T-SQL (WR-226), read the same way for a Fabric Warehouse change
(warehouse.py) and for the local engine (localdata/tsql.py).

``CREATE TABLE <clone> AS CLONE OF <source> [AT '<UTC time>']`` makes a zero-copy clone in a Fabric
Warehouse: only the table's metadata is copied, the clone shares the source's Parquet files as they were at
that time, and after that the two change independently. The time is UTC, written 'YYYY-MM-DDThh:mm:ss' with
up to three digits of fractions of a second (a datetime value), and no earlier than the warehouse's retention
period, which is at most 120 days. A clone can't reach across warehouses or workspaces, and the SQL analytics
endpoint can't clone (learn.microsoft.com/fabric/data-warehouse/clone-table).

``ALTER DATABASE [<snapshot>] SET TIMESTAMP = ...`` moves a warehouse snapshot, a read-only Fabric item, to
another point in time (learn.microsoft.com/fabric/data-warehouse/create-manage-warehouse-snapshot).

sqlglot can't read either statement (it falls back to a bare command), so they are found in the batch's
tokens: comments are skipped, and a string or a [quoted] name that holds the words is left alone.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlglot.dialects.tsql import TSQL
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

RETENTION_MAX_DAYS = 120  # the longest a Fabric Warehouse keeps its tables' history
_AT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?")
_NAME = (TokenType.VAR, TokenType.IDENTIFIER)
_STRINGS = (TokenType.STRING, TokenType.NATIONAL_STRING)


@dataclass(frozen=True)
class Clone:
    """One CREATE TABLE ... AS CLONE OF statement, and where it sits in the batch's text."""
    clone: tuple[str, ...]  # the name's parts: table, schema.table or database.schema.table
    source: tuple[str, ...]
    at: str | None  # the AT clause's text, as written, or None for now
    start: int
    end: int  # one past the statement's last character (its ; included)

    @property
    def clone_name(self) -> str:
        return ".".join(self.clone)

    @property
    def source_name(self) -> str:
        return ".".join(self.source)

    def statement(self) -> str:
        at = f" AT '{self.at}'" if self.at is not None else ""
        return f"CREATE TABLE {self.clone_name} AS CLONE OF {self.source_name}{at}"


def _tokens(batch: str) -> list[Token]:
    try:
        return list(TSQL().tokenize(batch))
    except SqlglotError:
        return []  # the caller's own parse reports what it can't read


def _word(t: Token) -> str:
    return "" if t.token_type in (*_STRINGS, TokenType.IDENTIFIER) else t.text.upper()


def _name(tokens: list[Token], i: int) -> tuple[tuple[str, ...], int]:
    """The dotted name starting at ``tokens[i]``: its parts and the index after it (parts empty if none)."""
    parts: list[str] = []
    while i < len(tokens) and tokens[i].token_type in _NAME:
        parts.append(tokens[i].text)
        if i + 2 < len(tokens) and tokens[i + 1].token_type == TokenType.DOT:
            i += 2
            continue
        i += 1
        break
    return tuple(parts), i


def clones(batch: str) -> list[Clone]:
    """Every CREATE TABLE ... AS CLONE OF in ``batch``, in order. A statement that starts like one but can't
    be read whole is returned with empty names, so the caller refuses it by name."""
    tokens = _tokens(batch)
    found: list[Clone] = []
    i = 0
    while i < len(tokens):
        if not (_word(tokens[i]) == "CREATE" and i + 1 < len(tokens) and _word(tokens[i + 1]) == "TABLE"):
            i += 1
            continue
        clone, j = _name(tokens, i + 2)
        if not (j + 2 < len(tokens) and _word(tokens[j]) == "AS" and _word(tokens[j + 1]) == "CLONE"
                and _word(tokens[j + 2]) == "OF"):
            i += 1
            continue
        source, k = _name(tokens, j + 3)
        at = None
        if k < len(tokens) and _word(tokens[k]) == "AT":
            if k + 1 < len(tokens) and tokens[k + 1].token_type in _STRINGS:
                at, k = tokens[k + 1].text, k + 2
            else:
                at, k = "", k + 1  # AT with no time: refused as a time that can't be read
        if k < len(tokens) and tokens[k].token_type == TokenType.SEMICOLON:
            k += 1
        end = tokens[k - 1].end + 1
        if not clone or not source:
            clone, source = (), ()
        found.append(Clone(clone, source, at, tokens[i].start, end))
        i = k
    return found


def without(batch: str, found: list[Clone]) -> str:
    """The batch with these clone statements blanked out (same length, so positions still match)."""
    out = list(batch)
    for c in found:
        for p in range(c.start, c.end):
            if out[p] != "\n":
                out[p] = " "
    return "".join(out)


def snapshot_timestamp(batch: str) -> bool:
    """Whether the batch has ALTER DATABASE ... SET TIMESTAMP (a warehouse snapshot's point in time)."""
    words = [_word(t) for t in _tokens(batch)]
    for i, word in enumerate(words):
        if word == "ALTER" and i + 1 < len(words) and words[i + 1] == "DATABASE":
            rest = words[i + 2:i + 12]
            if "SET" in rest and rest[rest.index("SET") + 1:rest.index("SET") + 2] == ["TIMESTAMP"]:
                return True
    return False


SNAPSHOT_TIMESTAMP = ("ALTER DATABASE ... SET TIMESTAMP moves a warehouse snapshot to another point in time. "
                      "A snapshot is a Fabric item of its own, so it isn't moved from here: use Capture new state "
                      "on the snapshot in Fabric.")


def at_problem(at: str, now: dt.datetime | None = None) -> str:
    """Why an AT time can't be used, or "". ``now`` is UTC (for tests)."""
    m = _AT.fullmatch(at)
    when = None
    if m:
        try:
            y, mo, d, h, mi, sec = (int(g) for g in m.groups()[:6])
            when = dt.datetime(y, mo, d, h, mi, sec, int((m.group(7) or "0").ljust(3, "0")) * 1000,
                               tzinfo=dt.timezone.utc)
        except ValueError:
            when = None
    if when is None:
        return (f"AT '{at}' isn't a time a clone can take. Write the UTC time as 'YYYY-MM-DDThh:mm:ss', with up to "
                "three decimal places for the seconds, such as '2026-11-01T14:30:00.000'.")
    now = now or dt.datetime.now(dt.timezone.utc)
    if when > now:
        return f"AT '{at}' is later than now (UTC). A clone can be made as of now or a time in the past."
    if when < now - dt.timedelta(days=RETENTION_MAX_DAYS):
        return (f"AT '{at}' is more than {RETENTION_MAX_DAYS} days ago, past the longest a Fabric Warehouse keeps "
                "a table's history, so there is nothing to clone from.")
    return ""


def local_refusal(batch: str) -> str:
    """The local engine's refusal for a clone or a snapshot's timestamp in ``batch``, or "" if it has neither."""
    found = clones(batch)
    if found:
        c = found[0]
        if not c.clone:
            return ("CREATE TABLE ... AS CLONE OF isn't mapped yet, and this one couldn't be read. Write it as "
                    "CREATE TABLE schema.clone AS CLONE OF schema.source [AT 'YYYY-MM-DDThh:mm:ss'].")
        past = ""
        if c.at is not None:
            past = (f" To read {c.source_name} as it was then, run SELECT * FROM {c.source_name} "
                    f"OPTION (FOR TIMESTAMP AS OF '{c.at}').")
        return (f"{c.statement()} isn't mapped yet. In a Fabric Warehouse a clone is zero-copy: it shares "
                f"{c.source_name}'s files as they were, and after that the two change on their own. Here it would have "
                "to be a full copy with its own history, so it isn't made here. For a copy of the "
                f"rows now, use CREATE TABLE {c.clone_name} AS SELECT * FROM {c.source_name}, or run it in a Fabric "
                f"Warehouse.{past}")
    if snapshot_timestamp(batch):
        return ("ALTER DATABASE ... SET TIMESTAMP isn't mapped yet. It moves a warehouse snapshot, a Fabric item, to "
                "another point in time, and the local engine has no warehouse snapshots. To read a table as it was, "
                "run SELECT ... OPTION (FOR TIMESTAMP AS OF 'YYYY-MM-DDThh:mm:ss').")
    return ""
