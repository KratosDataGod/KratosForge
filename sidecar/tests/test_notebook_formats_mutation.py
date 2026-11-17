"""Mutation testing batch 9 (docs/quality/mutation-testing.md): notebook formats.

Each test pins a behavior a surviving mutant changed without any other test noticing: the size
limits on a source file and on a .dbc archive (checked from the header before anything is unpacked,
and an entry exactly at the limit still read whole), the unsafe-path message, what a Fabric .py
export escapes, the language a Fabric METADATA block gives its own cell, the options note on a cell
magic, whole-notebook notes first, a table's plain text, empty markdown in a percent script, the
order of .dbc commands without a position, and a .dbc notebook's name."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from fabric_desktop import notebook_formats as nbf

MiB = 1024 * 1024


def _dbc(entries, deflate=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo()
            info.filename = name
            info.compress_type = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
            zf.writestr(info, data)
    return buf.getvalue()


def _doc(commands, **extra):
    return json.dumps({"language": "python", "commands": commands, **extra})


# Size limits


def test_a_source_file_over_32_mb_isnt_read():
    with pytest.raises(ValueError, match=r"^The file is larger than 32 MB, so it wasn't read\.$"):
        nbf.parse_source("x" * (32 * MiB + 1), "big.py")


def test_a_source_file_of_32_mb_is_read():
    assert nbf.parse_source("x" * (32 * MiB), "big.py")["format"] == "Script (one cell)"


def test_a_dbc_entry_over_16_mb_is_refused():
    data = _dbc([("big.python", " " * (16 * MiB + 1))])
    with pytest.raises(ValueError, match="big.python is larger than 16 MB unpacked"):
        nbf.parse_dbc(data)


def _no_unpacking(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("an entry was unpacked")
    monkeypatch.setattr(zipfile.ZipFile, "open", refuse)


def test_an_entry_too_big_by_its_header_is_refused_before_it_is_unpacked(monkeypatch):
    data = _dbc([("big.python", " " * 200)])
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 100)
    _no_unpacking(monkeypatch)
    with pytest.raises(ValueError, match="big.python is larger than 16 MB unpacked"):
        nbf.parse_dbc(data)


def test_an_archive_too_big_in_all_by_its_headers_is_refused_before_it_is_unpacked(monkeypatch):
    data = _dbc([("a.python", " " * 200), ("b.python", " " * 200)])
    monkeypatch.setattr(nbf, "MAX_DBC_TOTAL", 300)
    opened = []
    real_open = zipfile.ZipFile.open

    def counting(self, info, *a, **k):
        opened.append(info.filename)
        return real_open(self, info, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "open", counting)
    with pytest.raises(ValueError, match="unpacks to more than 128 MB"):
        nbf.parse_dbc(data)
    assert opened == ["a.python"]


def test_an_archive_one_byte_over_128_mb_in_all_is_refused():
    entries = [(f"part{i}", " " * (16 * MiB)) for i in range(8)] + [("last", " ")]
    with pytest.raises(ValueError, match="unpacks to more than 128 MB"):
        nbf.parse_dbc(_dbc(entries))


def test_an_entry_exactly_at_the_limit_is_read_whole(monkeypatch):
    doc = _doc([{"command": "x = 1", "position": 0}])
    doc = doc[:-1] + " " * (1000 - len(doc)) + "}"
    assert len(doc) == 1000
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 1000)
    (got,) = nbf.parse_dbc(_dbc([("proj/Load.python", doc)]))
    assert [c["source"] for c in got["cells"]] == ["x = 1"]


def test_an_unsafe_path_is_named_by_its_first_120_characters():
    name = "../" + "a" * 200
    with pytest.raises(ValueError) as caught:
        nbf.parse_dbc(_dbc([(name, "{}")]))
    assert str(caught.value) == f"The archive has a file with an unsafe path ({name[:120]!r}), so it wasn't imported."


# Fabric .py export and import


def test_a_databricks_title_line_in_a_python_cell_is_written_as_it_is_for_fabric():
    out = nbf.to_fabric_py([{"type": "code", "source": "x = 1\n# DBTITLE 1,t"}])
    assert "\nx = 1\n# DBTITLE 1,t\n" in out
    assert "# MAGIC" not in out


def test_a_fabric_cell_marker_in_python_code_is_escaped_and_reads_back_as_one_cell():
    source = "x = 1\n" + nbf.FABRIC_CELL + "\ny = 2"
    out = nbf.to_fabric_py([{"type": "code", "source": source}])
    assert f"# MAGIC {nbf.FABRIC_CELL}" in out
    assert [c["source"] for c in nbf.parse_source(out)["cells"]] == [source]


def _fabric(*blocks):
    out = [nbf.FABRIC_HEADER, ""]
    for kind, body, language in blocks:
        out += [kind, "", body, ""]
        if language:
            out += [nbf.FABRIC_METADATA, "", f'# META {{"language": "{language}"}}', ""]
    return "\n".join(out)


def test_a_metadata_block_gives_its_language_to_its_own_cell():
    text = _fabric((nbf.FABRIC_CELL, "x = 1", "python"), (nbf.FABRIC_CELL, "select 1", "sparksql"))
    assert [c["source"] for c in nbf.parse_source(text)["cells"]] == ["x = 1", "%%sql\nselect 1"]


def test_a_metadata_block_after_markdown_and_code_marks_the_code():
    text = _fabric((nbf.FABRIC_MARKDOWN, "# Title", None), (nbf.FABRIC_CELL, "select 1", "sparksql"))
    cells = nbf.parse_source(text)["cells"]
    assert [(c["type"], c["source"]) for c in cells] == [("markdown", "Title"), ("code", "%%sql\nselect 1")]


# Databricks notes


def test_the_options_on_a_cell_magic_line_are_named_whole():
    notes = nbf.databricks_export_notes([{"type": "code", "source": "%%sql -n x\nselect 1"}])
    assert notes[-1] == {"cell": 1, "kind": "dropped",
                         "what": "The options on the %%sql line (-n x): the export writes %sql on its own."}


def test_a_whole_notebook_note_comes_before_the_cell_notes():
    doc = _doc([{"command": "x = 1", "position": 0, "commandTitle": "Load"}], dashboards=[{"name": "d"}])
    (got,) = nbf.parse_dbc(_dbc([("proj/Load.python", doc)]))
    assert [n["cell"] for n in got["notes"]] == [None, 1]


# Other formats


def test_a_tables_plain_text_has_its_header_and_blank_nulls():
    assert nbf._table_plain({"columns": ["a", "b"], "rows": [[1, None], [None, "x"]]}) == "a\tb\n1\t\n\tx"


def test_an_empty_markdown_chunk_in_a_percent_script_is_dropped():
    cells = nbf.parse_source("# %% [markdown]\n\n# %%\nx = 1\n")["cells"]
    assert cells == [{"type": "code", "source": "x = 1"}]


def test_dbc_commands_without_a_position_come_first():
    doc = _doc([{"command": "b = 2", "position": 0.5}, {"command": "a = 1"}])
    (got,) = nbf.parse_dbc(_dbc([("proj/Load.python", doc)]))
    assert [c["source"] for c in got["cells"]] == ["a = 1", "b = 2"]


@pytest.mark.parametrize("name, expected", [("Sales", "Sales"), ("   ", "Load"), (5, "Load")])
def test_a_dbc_notebook_is_named_by_its_document_or_else_its_file(name, expected):
    doc = _doc([{"command": "x = 1", "position": 0}], name=name)
    (got,) = nbf.parse_dbc(_dbc([("proj/Load.python", doc)]))
    assert got["name"] == expected
