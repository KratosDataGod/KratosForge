"""SQL that can't be read gets a plain-text message: sqlglot's terminal escape codes around the bad
token never reach the app, where they'd show as stray characters."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlglot
from sqlglot.errors import ParseError

from fabric_desktop import delta_dml, sql_dialects, warehouse
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.localdata import delta

ESC = "\x1b"
PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"


def _parse_error(code: str, read: str = "spark") -> ParseError:
    with pytest.raises(ParseError) as exc:
        sqlglot.parse(code, read=read)
    return exc.value


def test_the_escape_codes_go_and_the_message_and_excerpt_stay():
    exc = _parse_error("SELEC FROM WHERE ((")
    assert ESC in str(exc)                          # sqlglot still underlines with escape codes
    text = sql_dialects.parse_problem(exc)
    assert ESC not in text
    assert "Line 1, Col: 10" in text and "SELEC FROM WHERE ((" in text


def test_text_without_escape_codes_is_unchanged():
    assert sql_dialects.parse_problem(ValueError("plain [4m text")) == "plain [4m text"


@pytest.mark.parametrize("call", [
    lambda: warehouse.verdicts("SELEC FROM WHERE (("),
    lambda: delta_dml.parse("MERGE INTO t USING (( ON"),
    lambda: delta.rewrite_as_of("SELECT * FROM t VERSION AS OF (("),
], ids=["warehouse", "delta_dml", "as_of"])
def test_each_place_that_reads_sql_says_so_in_plain_text(call):
    with pytest.raises(ValueError) as exc:
        call()
    assert ESC not in str(exc.value) and str(exc.value)


def test_a_duckdb_cell_in_spark_sql_that_cant_be_read_says_so_in_plain_text():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        r = s.execute(CellRequest("SELEC FROM WHERE ((", "sql", "databricks"))
    finally:
        s.close()
    message = r.outputs[-1]["evalue"]
    assert r.status == "error" and message.startswith("Could not read the query") and ESC not in message


def _interpolates_the_error(handler: ast.ExceptHandler) -> bool:
    """A ParseError handler that puts ``{exc}`` straight into an f-string."""
    for node in ast.walk(handler):
        if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name) and node.value.id == handler.name:
            return True
    return False


def test_no_parse_error_is_put_into_a_message_as_it_is():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ExceptHandler) and node.name and node.type is not None):
                continue
            caught = ast.unparse(node.type)
            if "ParseError" in caught and _interpolates_the_error(node):
                found.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert not found, ("Use sql_dialects.parse_problem(exc) for a sqlglot ParseError in a message, so its "
                       f"escape codes don't reach the app: {found}")
