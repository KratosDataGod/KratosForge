"""Round trips through Databricks source, Fabric source and .ipynb keep every cell (#1218).

A seeded generator makes notebooks of every kind of cell the converters handle, writes each one in
a format and reads it back. The same cells must come back: as many, each with the same kind and
language, and the same text once line endings and trailing blank lines are set aside. The
generator is plain ``random`` with a fixed seed, so a failure names a notebook that fails again.
"""

import io
import json
import random
import struct
import zipfile

import pytest

from fabric_desktop import notebook_formats as nbf

SEED = 1218
NOTEBOOKS = 300

# Text that looks like a format's markers. It must stay text wherever it is: inside a string, in a
# markdown or magic cell, and alone on a line of a Python cell (which the writers then quote).
SEPARATORS = ["# COMMAND ----------", "-- COMMAND ----------", "// COMMAND ----------", "# CELL ********************",
              "# PARAMETERS CELL ********************", "# MARKDOWN ********************",
              "# METADATA ********************", "# META {", "# MAGIC %md", "# DBTITLE 1,Title",
              "# Databricks notebook source", "# Fabric notebook source", "# %%", "# %% [markdown]"]
WORDS = ["sales", "région", "東京", "naïve", "Ωmega", "emoji 🚀", "tab\there", "x", "SELECT", "café", "zero​width"]


def _word(rng):
    return rng.choice(WORDS)


def _tail(rng, line):
    """Trailing spaces or tabs, sometimes: they're part of the text and must come back."""
    return line + rng.choice(["", "", "", " ", "  ", "\t", " \t "])


def _python_line(rng):
    return rng.choice([
        lambda: f"x = {rng.randint(0, 999)}",
        lambda: f"name = {_word(rng)!r}",
        lambda: f"s = {rng.choice(SEPARATORS)!r}",
        lambda: f"print({_word(rng)!r})  # {_word(rng)}",
        lambda: "def f(a):",
        lambda: "\treturn a  # a tab-indented line",
        lambda: "    return a",
        lambda: "",
        lambda: "   ",
        lambda: f"# {_word(rng)}",
        lambda: f'q = """{rng.choice(SEPARATORS)} inside a string"""',
        lambda: f"df = spark.read.table('{_word(rng)}')",
        lambda: rng.choice(SEPARATORS),  # alone on its line: the writers must still keep it as code
    ])()


def _lines(rng, make, lo=1, hi=6):
    lines = [_tail(rng, make(rng)) for _ in range(rng.randint(lo, hi))]
    while lines and not lines[0].strip():  # a cell's leading blank lines aren't kept in .py source (see below)
        lines = lines[1:]
    return lines


def _sql_line(rng):
    return rng.choice([f"SELECT {_word(rng)!r} AS v", "FROM sales", "WHERE amount > 0", "-- a comment", "",
                       f"-- {rng.choice(SEPARATORS)}", "  AND region = 'NÖ'"])


def _scala_line(rng):
    return rng.choice(["val x = 1", f'val s = "{rng.choice(SEPARATORS)}"', "// note", "", "println(x)"])


def _r_line(rng):
    return rng.choice(["x <- 1", f'print("{_word(rng)}")', "# note", ""])


def _md_line(rng):
    return rng.choice([f"# {_word(rng)}", f"Some *text* about {_word(rng)}.", "", "- a list item", "  indented",
                       "line break after two spaces  ", rng.choice(SEPARATORS), f"`{rng.choice(SEPARATORS)}`",
                       "%md looks like a magic", "%%sql looks like a cell magic", "| a | b |", "\ttabbed"])


def _cell(rng):
    kind = rng.choice(["python", "python", "sql", "scala", "r", "md_magic", "run", "pip", "markdown", "markdown",
                       "empty", "empty_markdown", "params"])
    if kind == "python" or kind == "params":
        cell = {"type": "code", "source": "\n".join(_lines(rng, _python_line))}
        if kind == "params":
            cell["meta"] = {"tags": ["parameters"]}
        return cell
    if kind in ("sql", "scala", "r"):
        magic = {"sql": "%%sql", "scala": "%%spark", "r": "%%sparkr"}[kind]
        make = {"sql": _sql_line, "scala": _scala_line, "r": _r_line}[kind]
        return {"type": "code", "source": "\n".join([magic] + _lines(rng, make, 0))}
    if kind == "md_magic":  # a markdown magic in a code cell, as Fabric writes one
        return {"type": "code", "source": "\n".join(["%%md"] + _lines(rng, _md_line, 0))}
    if kind == "run":
        return {"type": "code", "source": rng.choice(["%run ./shared/setup", "%run helpers", f"%run ./{_word(rng)}"])}
    if kind == "pip":
        return {"type": "code", "source": "\n".join(["%pip install rich"] + rng.choice([[], ["%pip install polars"]]))}
    if kind == "markdown":
        return {"type": "markdown", "source": "\n".join(_lines(rng, _md_line))}
    if kind == "empty":
        return {"type": "code", "source": rng.choice(["", "\n", "   "])}
    return {"type": "markdown", "source": ""}


def _crlf(rng, cell):
    """Some cells come with Windows line endings, as an editor on Windows saves them."""
    if rng.random() < 0.2:
        cell = dict(cell, source=cell["source"].replace("\n", "\r\n"))
    return cell


def notebooks(seed=SEED, count=NOTEBOOKS):
    rng = random.Random(seed)
    for i in range(count):
        yield i, [_crlf(rng, _cell(rng)) for _ in range(rng.randint(1, 8))]


def _norm(source):
    """The text as a round trip must keep it: line endings and trailing blank lines set aside."""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _language(cell):
    if cell["type"] != "code":
        return None
    first = cell["source"].replace("\r\n", "\n").split("\n", 1)[0].strip()
    return first if first.startswith("%%") else "python"


def _shape(cells, params=True):
    return [(c["type"], _language(c), _norm(c["source"]), params and nbf._is_params(c)) for c in cells]


def _databricks_shape(cells):
    # Databricks has no parameter cell (export lists it as dropped), and %%md is Databricks' %md:
    # a markdown cell (export lists it as changed).
    out = []
    for kind, language, source, _ in _shape(cells, params=False):
        if language == "%%md":
            kind, language, source = "markdown", None, _norm("\n".join(source.split("\n")[1:]).strip("\n"))
        out.append((kind, language, source, False))
    return out


def _fail(i, cells, text):
    return f"notebook {i} (seed {SEED}) didn't come back whole.\ncells: {json.dumps(cells, ensure_ascii=False)}\nfile:\n{text}"


def test_the_generator_is_deterministic_and_covers_every_kind_of_cell():
    first, again = list(notebooks()), list(notebooks())
    assert first == again and len(first) == NOTEBOOKS
    sources = [c["source"] for _, cells in first for c in cells]
    joined = "\n".join(sources)
    for needle in ("%%sql", "%%spark", "%%sparkr", "%%md", "%run", "%pip", "\r\n", "\t", "東京", "  \n",
                   "# COMMAND ----------", "# CELL ********************", "# METADATA ********************"):
        assert needle in joined, needle
    assert "" in sources
    assert any(nbf._is_params(c) for _, cells in first for c in cells)
    assert any(c["type"] == "markdown" for _, cells in first for c in cells)


def test_databricks_source_round_trip_keeps_every_cell():
    for i, cells in notebooks():
        text = nbf.to_databricks_py(cells)
        back = nbf.parse_source(text, "nb.py")
        assert back["format"] == "Databricks notebook source"
        assert _shape(back["cells"], params=False) == _databricks_shape(cells), _fail(i, cells, text)


def test_fabric_source_round_trip_keeps_every_cell():
    for i, cells in notebooks():
        text = nbf.to_fabric_py(cells)
        back = nbf.parse_source(text, "notebook-content.py")
        assert back["format"] == "Fabric notebook source"
        assert _shape(back["cells"]) == _shape(cells), _fail(i, cells, text)


def test_ipynb_round_trip_keeps_every_cell_exactly():
    for i, cells in notebooks():
        text = nbf.to_ipynb_text(cells, outputs=False)
        back = nbf.parse_ipynb(text)["cells"]
        # .ipynb holds text as it is, so here even line endings and blank lines come back.
        assert [(c["type"], c["source"], nbf._is_params(c)) for c in back] == \
               [(c["type"], c["source"], nbf._is_params(c)) for c in cells], _fail(i, cells, text)


# ---- the losses the round trips found, each now kept or listed

def _back(text):
    return [(c["type"], c["source"]) for c in nbf.parse_source(text, "nb.py")["cells"]]


def test_trailing_spaces_in_markdown_and_magic_lines_are_kept():
    cells = [{"type": "markdown", "source": "Line one  \nLine two\t"}, {"type": "code", "source": "%%sql\nSELECT 1   \nFROM t"}]
    for text in (nbf.to_databricks_py(cells), nbf.to_fabric_py(cells)):
        assert _back(text) == [(c["type"], c["source"]) for c in cells]
    # A blank line still gets the bare prefix, with nothing after it.
    assert "# MAGIC\n" in nbf.to_databricks_py([{"type": "markdown", "source": "a\n\nb"}])
    assert "\n#\n" in nbf.to_fabric_py([{"type": "markdown", "source": "a\n\nb"}])


def test_an_empty_code_cell_comes_back_from_databricks_source():
    cells = [{"type": "code", "source": "x = 1"}, {"type": "code", "source": ""}, {"type": "code", "source": "y = 2"}]
    text = nbf.to_databricks_py(cells)
    assert "# MAGIC %python" in text
    assert _back(text) == [("code", "x = 1"), ("code", ""), ("code", "y = 2")]


@pytest.mark.parametrize("line", ["# COMMAND ----------", "# DBTITLE 1,Load", "# MAGIC %md"])
def test_python_that_databricks_source_would_misread_is_written_as_a_python_cell(line):
    source = f"x = 1\n{line}\ny = 2" if "MAGIC" not in line else line
    text = nbf.to_databricks_py([{"type": "code", "source": source}])
    assert text.startswith("# Databricks notebook source\n# MAGIC %python\n")
    assert _back(text) == [("code", source)] and nbf.parse_source(text, "nb.py")["notes"] == []


@pytest.mark.parametrize("line", ["# CELL ********************", "# PARAMETERS CELL ********************",
                                  "# MARKDOWN ********************", "# METADATA ********************", "# MAGIC %%sql"])
def test_python_that_fabric_source_would_misread_is_written_as_magic_lines(line):
    source = f"x = 1\n{line}\ny = 2" if "MAGIC" not in line else line
    text = nbf.to_fabric_py([{"type": "code", "source": source}])
    assert f"# MAGIC {line}" in text and '"language": "python"' in text
    assert _back(text) == [("code", source)]


def test_a_databricks_export_lists_md_magic_cells_and_dropped_magic_options():
    notes = nbf.databricks_export_notes([{"type": "code", "source": "%%md\n# Title"},
                                         {"type": "code", "source": "%%sql -o result\nSELECT 1"}])
    assert [(n["cell"], n["kind"]) for n in notes] == [(1, "changed"), (2, "changed"), (2, "dropped")]
    assert "markdown cell" in notes[0]["what"] and "(-o result)" in notes[2]["what"]
    assert nbf.parse_source(nbf.to_databricks_py([{"type": "code", "source": "%%md\n# Title"}]))["cells"] == \
           [{"type": "markdown", "source": "# Title"}]


@pytest.mark.parametrize("source", ["\n\n%%sql\nSELECT 1", "  %%sql\nSELECT 1", "\r\n \t%%sql\nSELECT 1"])
def test_a_cell_magic_after_blank_lines_or_an_indent_exports_as_that_magic(source):
    cells = [{"type": "code", "source": source}]
    databricks = nbf.to_databricks_py(cells)
    assert databricks == "# Databricks notebook source\n# MAGIC %sql\n# MAGIC SELECT 1\n"
    fabric = nbf.to_fabric_py(cells)
    assert "# MAGIC %%sql\n# MAGIC SELECT 1\n" in fabric and '"language": "sparksql"' in fabric
    for text in (databricks, fabric):
        assert _back(text) == [("code", "%%sql\nSELECT 1")]
    notes = nbf.databricks_export_notes(cells)
    assert [(n["kind"], n["what"].split(" ")[0]) for n in notes] == [("changed", "%%sql")]


def test_the_dropped_options_note_reads_the_first_non_blank_line():
    for source in ("\n%%sql -o result\nSELECT 1", "   %%sql -o result\nSELECT 1"):
        notes = nbf.databricks_export_notes([{"type": "code", "source": source}])
        assert [n["kind"] for n in notes] == ["changed", "dropped"] and "(-o result)" in notes[1]["what"]


# ---- what .py source can't hold (docs/notebook-import-export.md, "Round trips")

def test_leading_blank_lines_of_a_cell_arent_kept_in_py_source_but_are_in_ipynb():
    cells = [{"type": "code", "source": "\n\nx = 1"}, {"type": "markdown", "source": "\n# Title"}]
    for text in (nbf.to_databricks_py(cells), nbf.to_fabric_py(cells)):
        assert _back(text) == [("code", "x = 1"), ("markdown", "# Title")]
    assert [c["source"] for c in nbf.parse_ipynb(nbf.to_ipynb_text(cells, False))["cells"]] == ["\n\nx = 1", "\n# Title"]


def test_a_markdown_line_that_is_a_fabric_marker_without_its_hash_splits_the_cell():
    # Fabric writes markdown as "# " lines, so this line becomes "# CELL ****...", Fabric's own
    # marker, and Fabric reads it the same way. Databricks source keeps it.
    cells = [{"type": "markdown", "source": "Before\nCELL ********************\nAfter"}]
    assert _back(nbf.to_fabric_py(cells)) == [("markdown", "Before"), ("code", "# After")]
    assert _back(nbf.to_databricks_py(cells)) == [("markdown", cells[0]["source"])]


def test_a_language_line_magic_in_a_code_cell_becomes_that_language_in_databricks():
    # %sql or %md at the top of a code cell is Databricks' own cell magic, so it reads as one.
    text = nbf.to_databricks_py([{"type": "code", "source": "%sql SELECT 1"}, {"type": "code", "source": "%md Notes"}])
    assert _back(text) == [("code", "%%sql\nSELECT 1"), ("markdown", "Notes")]


def test_a_notebook_with_no_cells_comes_back_with_none():
    assert nbf.parse_source(nbf.to_databricks_py([]), "nb.py")["cells"] == []
    assert nbf.parse_source(nbf.to_fabric_py([]), "nb.py")["cells"] == []
    assert nbf.parse_ipynb(nbf.to_ipynb_text([], False))["cells"] == []


# ---- .dbc archives that are refused, with a message

def _zip(entries, method=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo()
            info.filename = name  # as given, as in test_notebook_formats._dbc
            info.compress_type = method
            zf.writestr(info, data)
    return bytearray(buf.getvalue())


DOC = json.dumps({"name": "Load", "language": "python", "commands": [{"command": "x = 1\n" * 40, "position": 0}]})


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04 not really", bytes(_zip([("a.python", DOC)]))[:-30]],
                         ids=["empty", "zip-signature-only", "truncated"])
def test_a_corrupt_archive_is_refused(data):
    with pytest.raises(ValueError, match="isn't a Databricks archive"):
        nbf.parse_dbc(data)


@pytest.mark.parametrize("name", ["a\x00.python", "a/b/../../../c.python", "..", "//server/share/x.python", "c:x.python"])
def test_more_unsafe_paths_are_refused(name):
    with pytest.raises(ValueError, match="unsafe path"):
        nbf.parse_dbc(bytes(_zip([(name, DOC)])))


def test_an_oversized_archive_is_refused_before_it_is_opened(monkeypatch):
    data = bytes(_zip([("a.python", DOC)]))
    monkeypatch.setattr(nbf, "MAX_DBC", len(data) - 1)
    with pytest.raises(ValueError, match="larger than 64 MB"):
        nbf.parse_dbc(data)


def test_an_archive_that_unpacks_too_big_in_all_is_refused(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_DBC_TOTAL", len(DOC) + 10)
    with pytest.raises(ValueError, match="more than 128 MB"):
        nbf.parse_dbc(bytes(_zip([("a.python", DOC), ("b.python", DOC)])))


def test_an_entry_whose_header_understates_its_size_is_refused():
    data = _zip([("a.python", DOC)], zipfile.ZIP_DEFLATED)
    struct.pack_into("<I", data, data.rfind(b"PK\x01\x02") + 24, 10)  # the uncompressed size it claims
    with pytest.raises(ValueError, match="is damaged"):
        nbf.parse_dbc(bytes(data))


def test_an_encrypted_archive_is_refused():
    data = _zip([("a.python", DOC)])  # zipfile won't write the flag, so it's set in the bytes
    struct.pack_into("<H", data, 6, 1)  # in the local header
    struct.pack_into("<H", data, data.rfind(b"PK\x01\x02") + 8, 1)  # and in the central directory
    with pytest.raises(ValueError, match="encrypted"):
        nbf.parse_dbc(bytes(data))


def test_an_archive_with_too_many_notebooks_is_refused(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_NOTEBOOKS", 1)
    with pytest.raises(ValueError, match="at most 1 can be imported"):
        nbf.parse_dbc(bytes(_zip([("a.python", DOC), ("b.python", DOC)])))
