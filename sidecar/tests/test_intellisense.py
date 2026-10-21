"""Hover docs, signature help and the as-you-type syntax check for the notebook editor."""
import pytest

from fabric_desktop.intellisense import call_help, dotted_at, open_call, syntax_problems
from fabric_desktop.notebook import Kernel
from fabric_desktop import audit, server


def test_dotted_at_cuts_the_name_at_the_hovered_word():
    code = "x = df.group_by('a')"
    assert dotted_at(code, code.index("df") + 1)[0] == "df"
    assert dotted_at(code, code.index("group") + 2)[0] == "df.group_by"
    assert dotted_at("1.5", 1) is None


def test_open_call_finds_the_argument_being_typed_and_skips_strings_and_comments():
    assert open_call("pl.read_csv(") == ("pl.read_csv", 0, None)
    assert open_call("f(a, (1, 2), ") == ("f", 2, None)
    assert open_call("f(a, sep=") == ("f", 1, "sep")
    assert open_call("f(a, x == ") == ("f", 1, None)
    assert open_call("f('(', ") == ("f", 1, None)
    assert open_call("f(a)  # g(") is None
    assert open_call("f(a)") is None
    assert open_call("[1, 2") is None
    assert open_call("f('unclosed") is None
    # After a call's result only the last name is known; the kernel can't look it up without running it.
    assert open_call("df.write.mode('overwrite').saveAsTable(\n    ") == ("saveAsTable", 0, None)


def test_call_help_marks_the_positional_or_keyword_parameter():
    def load(path, sep=",", *, header=True, **options):
        """Load a file.

        More words."""
    h = call_help(load, "load", 1, None)
    assert h["params"] == ["path", "sep=','", "header=True", "**options"]
    assert h["active"] == 1
    assert h["doc"] == "Load a file."
    assert call_help(load, "load", 0, "header")["active"] == 2
    assert call_help(load, "load", 0, "other")["active"] == 3  # an unknown keyword goes to **options


def test_kernel_inspect_reads_live_objects_without_running_code_and_hides_secrets():
    k = Kernel()
    k.run("import polars as pl\n"
          "class Thing:\n    @property\n    def boom(self):\n        raise SystemExit('ran')\n"
          "t = Thing()\napi_token = 'abc123'\nn = 42\n"
          "def total(rows, *, tax=0.2):\n    'Add up rows.'\n    return 0")
    hover = k.inspect("total(1)", 2)
    assert hover["kind"] == "function" and hover["signature"] == "(rows, *, tax=0.2)" and hover["doc"] == "Add up rows."
    assert k.inspect("n + 1", 0)["value"] == "42"
    secret = k.inspect("api_token", 3)
    assert secret["value"] == "hidden" and "abc123" not in str(secret)
    assert k.inspect("t.boom", 4) is None  # a property is never read
    assert k.inspect("pl.read_csv", 6)["kind"] == "function"
    sig = k.inspect("total(1, tax=", call=True)
    assert sig["name"] == "total" and sig["params"][sig["active"]] == "tax=0.2"
    assert k.inspect("missing(", call=True) is None


def test_syntax_problems_point_at_the_line_and_ignore_other_languages_and_magics():
    [p] = syntax_problems("a = 1\nb = (2,\nc = 3")
    assert p["line"] >= 2 and p["message"]
    [p] = syntax_problems("x = = 1")
    assert p["line"] == 1 and p["col"] >= 2
    assert syntax_problems("%%sql\nSELECT * FROM t WHERE (") == []
    assert syntax_problems("%%html\n<b>") == []
    assert syntax_problems("%run other\n!pip list\nfiles = !ls\ndf = %sql SELECT 1\nif True:\n    %time x = 1\n") == []
    assert syntax_problems("await something()") == []
    [p] = syntax_problems("%%pyspark\nx = (")
    assert p["line"] >= 2


@pytest.mark.parametrize("code", [
    'q = "id=%s" % 5',
    'print("done=!")',
    'url = f"a?x={1}&y=%d" % 2',
    "where = \"WHERE a=%(a)s\" % {'a': 1}",
    'ok = x != 1',
    'msg = "a=!b"; n = 1',
    'd["x=%s"] = 1',
    'print(d["k"], "=%d" % 3)',
])
def test_equals_percent_or_bang_inside_a_string_is_valid_python(code):
    # Quality repro r354: the IPython `x = %magic` rewrite used to fire inside strings.
    assert syntax_problems(code) == []


@pytest.mark.parametrize("code", ["x = %sql select 1", "files = !ls", "%time y = 1", "a, b = %who_ls", "self.out = !dir",
                                  'd["k"] = %sql SELECT 1', "rows[0].files = !ls", "m[a][b], n = !dir"])
def test_ipython_assignment_forms_still_pass(code):
    assert syntax_problems(code) == []


def test_hover_removes_credential_text_from_values_and_defaults():
    k = Kernel()
    k.run('conn = "Server=db;Password=abc123"\ndef connect(dsn="Server=db;Password=xyz789"):\n    pass')
    shown = k.inspect("conn", 2)
    assert "abc123" not in shown["value"] and "[removed]" in shown["value"]
    assert "xyz789" not in k.inspect("connect", 3)["signature"]


def test_the_new_calls_are_read_only_rpc_methods():
    for name in ("kernel_inspect", "notebook_syntax"):
        assert name in server.METHODS
        assert name in audit.READ_ONLY
