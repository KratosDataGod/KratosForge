"""snowpark_api: the statement and expression forms the main tests don't write (relative and star imports,
annotations, defaults, unpacking, attribute targets, module attributes), each checked by what it finds."""

from __future__ import annotations

import ast

from fabric_desktop import snowpark_api
from fabric_desktop.snowpark_api import _Checker, check


def kinds(code):
    return [(f["kind"], f["name"]) for f in check(code)["findings"]]


def env_after(code):
    c = _Checker()
    for node in ast.parse(code).body:
        c.statement(node)
    return c


def test_relative_and_star_imports_bind_nothing():
    c = env_after("from . import helpers\nfrom snowflake.snowpark.functions import *\n")
    assert "helpers" not in c.env and "*" not in c.env
    assert check("from .x import col\ncol('a').nope()")["compatible"] is True  # col isn't Snowpark's here


def test_a_module_member_that_is_a_module():
    c = env_after("from snowflake.snowpark.functions import general_functions as g")
    assert c.env["g"] == ("module", snowpark_api.api()["modules"]["snowflake.snowpark.functions"]["general_functions"]["module"])


def test_attribute_of_a_module_that_is_a_module_path():
    c = env_after("import snowflake\nF = snowflake.snowpark.functions\nx = snowflake.other.thing\n")
    assert c.env["snowflake"] == ("module", "snowflake")
    assert c.env["F"] == ("module", "snowflake.snowpark.functions")
    assert c.env["x"] is None  # not a module Snowpark has: unknown, never guessed
    assert kinds("import snowflake\nsnowflake.snowpark.functions.nope()") == [("unknown_function", "nope")]


def test_calling_a_module_checks_nothing():
    c = env_after("import snowflake.snowpark.functions as F\nr = F()\n")
    assert c.env["r"] is None and c.checked == 0


def test_annotations_bind_a_declared_class():
    c = env_after("from snowflake.snowpark import DataFrame\ndf: DataFrame = make()\nn: int\n")
    assert c.env["df"] == ("instance", "DataFrame")
    assert c.env["n"] is None
    assert kinds("from snowflake.snowpark import DataFrame\ndf: DataFrame = make()\ndf.nope()") == [
        ("unknown_method", "nope")]
    # Annotated with something that isn't a class: the value's type is kept.
    assert env_after("t: int = session.table('t')").env["t"] == ("instance", "Table")


def test_function_defaults_and_annotated_parameters_are_checked():
    code = ("from snowflake.snowpark import DataFrame\n"
            "def f(df: DataFrame, other, n=session.nope()):\n"
            "    df.nope2()\n"
            "    other.whatever()\n")
    assert kinds(code) == [("unknown_method", "nope"), ("unknown_method", "nope2")]
    assert "df" not in env_after(code).env  # the parameters don't outlive the function


def test_unpacking_and_attribute_targets():
    c = env_after("a, *b = session.table('t'), 1\n[x, y] = 1, 2\nsession.query_tag = 'x'\nholder.df = session.nope\n")
    assert c.env["a"] is None and c.env["b"] is None and c.env["x"] is None and c.env["y"] is None
    # Setting a name a Session doesn't have is no unknown read; reading one is.
    assert kinds("session.made_up = 1") == []
    assert kinds("holder.df = session.nope") == [("unknown_method", "nope")]
    assert kinds("session.table('t')[0] = 1") == []


def test_class_members_and_members_of_a_method():
    c = env_after("rc = session.RuntimeConfig\nm = session.sql.anything\n")
    assert c.env["rc"] == ("class", "RuntimeConfig")
    assert c.env["m"] is None  # an attribute of a bound method isn't checked


def test_subscripting_something_unknown():
    c = env_after("v = rows[0]\nw = session.table('t')['a']\n")
    assert c.env["v"] is None
    assert c.env["w"] == ("instance", "Column")


def test_a_class_the_signatures_dont_have(monkeypatch):
    c = _Checker()
    node = ast.parse("x.y", mode="eval").body
    assert c._attribute(("instance", "NotAClass"), node) is None
    assert c._attribute(("function", "m", "f"), node) is None


def test_call_function_with_a_name_that_isnt_text():
    code = "from snowflake.snowpark.functions import call_function\ncall_function(name, 1)\n"
    assert check(code)["families"] == []
    code2 = "from snowflake.snowpark.functions import call_function\ncall_function('AI_COMPLETE', 1)\n"
    assert check(code2)["families"] == ["cortex"]


def test_with_without_as():
    c = env_after("with session.query_history():\n    t = session.table('t')\n")
    assert c.env["t"] == ("instance", "Table")


def test_deleting_a_name_a_class_doesnt_have_is_not_an_unknown_read():
    assert kinds("del session.made_up") == []
    assert kinds("x = session.made_up") == [("unknown_method", "made_up")]
