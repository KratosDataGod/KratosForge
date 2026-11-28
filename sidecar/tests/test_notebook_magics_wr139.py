"""WR-139: what the magics fuzz (test_notebook_magics_fuzz.py) and probing of cell and line magics found."""

import pytest

from fabric_desktop import depgraph, envprofile, magics, notebook_formats
from fabric_desktop.notebook import Kernel, configure_report


def test_a_block_that_holds_only_a_magic_still_reads_as_python():
    code = "import pandas as pd\nfor f in ['a']:\n    !cp {f} out\nif True:\n\t%pip install polars\nx = pd.DataFrame()"
    assert magics.blank(code).split("\n") == ["import pandas as pd", "for f in ['a']:", "    pass", "if True:", "\tpass",
                                              "x = pd.DataFrame()"]
    graph = depgraph.analyze(code)
    assert graph["parsed"] and "x" in graph["defines"]
    assert envprofile.imports(code)["parsed"]


def test_a_top_level_magic_is_still_blanked_to_an_empty_line():
    assert magics.blank("%time f()\nx = %sql SELECT 1") == "\nx = None"


def test_time_reads_its_code_after_more_than_one_space():
    k = Kernel()
    out = k.run("%time  x = 2\nif True:\n    %time\t y = x + 1\n%time   y")
    assert out["status"] == "ok", out
    assert out["outputs"][-1] == {"type": "text", "text": "3"}


@pytest.mark.parametrize("source, expected", [
    ("%sql\r\nSELECT 1\r\n", "%%sql\nSELECT 1\r\n"),
    ("%scala\r\nval x = 1", "%%spark\nval x = 1"),
    ("%sh\r\nls", "%%sh\nls"),
    ("%sql\r", "%%sql\n"),
])
def test_a_databricks_cell_saved_on_windows_gets_no_extra_line_in_fabric(source, expected):
    assert magics.fabric_cell({"type": "code", "source": source})["source"] == expected


def test_a_markdown_cell_saved_on_windows_has_no_stray_line_ends():
    assert magics.fabric_cell({"type": "code", "source": "%md\r\n# hi\r\n"}) == {"type": "markdown", "source": "# hi"}
    out = Kernel().run("%%markdown\r\n# hi\r\n")
    assert out["outputs"] == [{"type": "markdown", "text": "# hi"}]


@pytest.mark.parametrize("magic", ["%%sql2", "%%spark3", "%%sql-legacy", "%%md5"])
def test_a_longer_cell_magic_isnt_exported_as_the_one_it_starts_with(magic):
    cells = [{"type": "code", "source": f"{magic}\nSELECT 1"}]
    assert notebook_formats._magic(cells[0]["source"]) is None
    assert notebook_formats.to_databricks_py(cells) == f"# Databricks notebook source\n# MAGIC {magic}\n# MAGIC SELECT 1\n"
    assert not any("became" in note["what"] for note in notebook_formats.databricks_export_notes(cells))
    assert '"language": "python"' in notebook_formats.to_fabric_py(cells)


def test_quoted_quotes_followed_by_a_space_stay_in_the_word():
    assert magics.words('put Files/a.txt "say \\"hi\\" there"') == ["put", "Files/a.txt", 'say "hi" there']
    assert magics.words('"a\\" b" c') == ['a" b', "c"]
    # A quote after a backslash that closes the line's quotes still ends the word, as before.
    assert magics.words('-r "C:\\My Folder\\" -q') == ["-r", "C:\\My Folder\\", "-q"]
    assert magics.words('"C:\\My Folder\\" "b c"') == ["C:\\My Folder\\", "b c"]
    assert magics.words('"a\\" b') == ["a\\", "b"]
    with pytest.raises(ValueError, match="No closing quotation"):
        magics.words('"say \\"hi')


@pytest.mark.parametrize("body, where", [
    ("\n{bad", "line 2, column 2"),
    ("\n\n{\n  \"a\": }", "line 4, column 8"),
    (" -f\n{bad", "line 2, column 2"),
    (" -f {bad", "line 1, column 17"),
])
def test_configure_says_where_the_json_is_wrong_on_the_cells_own_lines(body, where):
    with pytest.raises(ValueError, match=f"isn't valid JSON \\({where}:"):
        configure_report(body)
    out = Kernel().run("%%configure" + body)
    assert out["status"] == "error" and f"({where}:" in out["outputs"][-1]["evalue"]
