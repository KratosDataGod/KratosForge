"""Notebook import and export (notebook_formats.py) behaviour that the uncapped mutation run (batch 173,
docs/quality/mutation-testing.md) showed no test checked."""

import io
import json
import zipfile

import pytest

from fabric_desktop import notebook_formats as nbf


def _dbc(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _doc(name):
    return json.dumps({"name": name, "language": "python", "commands": [{"command": "x = 1", "position": 1}]})


def test_an_archive_holds_at_most_500_files():
    assert nbf.MAX_DBC_ENTRIES == 500
    ok = _dbc([("nb0.python", _doc("nb0"))] + [(f"f{i}.txt", "x") for i in range(499)])
    assert [n["name"] for n in nbf.parse_dbc(ok)] == ["nb0"]
    over = _dbc([("nb0.python", _doc("nb0"))] + [(f"f{i}.txt", "x") for i in range(500)])
    with pytest.raises(ValueError, match="has 501 files; at most 500"):
        nbf.parse_dbc(over)


def test_an_import_makes_at_most_200_notebooks():
    assert nbf.MAX_NOTEBOOKS == 200
    assert len(nbf.parse_dbc(_dbc([(f"nb{i}.python", _doc(f"nb{i}")) for i in range(200)]))) == 200
    with pytest.raises(ValueError, match="has 201 notebooks; at most 200"):
        nbf.parse_dbc(_dbc([(f"nb{i}.python", _doc(f"nb{i}")) for i in range(201)]))


def test_an_archive_over_64_mib_is_not_read():
    assert nbf.MAX_DBC == 64 * 1024 * 1024
    with pytest.raises(ValueError, match="larger than 64 MB"):
        nbf.parse_dbc(b"x" * (64 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="isn't a zip file"):
        nbf.parse_dbc(b"x" * (64 * 1024 * 1024))


CELLS = [{"type": "code", "source": "x = 1"}]


def test_each_export_format_writes_its_own_file():
    assert nbf.export_text("databricks_py", "nb", CELLS).startswith("# Databricks notebook source\n")
    assert nbf.export_text("fabric_py", "nb", CELLS).startswith("# Fabric notebook source\n")
    assert nbf.export_text("html", "nb", CELLS).startswith("<!doctype html>\n")
    assert json.loads(nbf.export_text("ipynb", "nb", CELLS))["cells"][0]["source"] in ("x = 1", ["x = 1"])
    with pytest.raises(ValueError, match="format is one of"):
        nbf.export_text("pdf", "nb", CELLS)


def test_only_a_plotly_output_with_a_figure_becomes_a_plotly_display():
    assert nbf._nb_output({"type": "plotly", "figure": "not a figure"}) is None
    assert nbf._nb_output({"type": "something else", "figure": {"data": []}}) is None
    shown = nbf._nb_output({"type": "plotly", "figure": {"data": []}})
    assert shown["data"]["application/vnd.plotly.v1+json"] == {"data": []}


def test_a_jupytext_header_is_only_skipped_at_the_top():
    text = "# %%\nx = 1\n# ---\ny = 2\n"
    cells = nbf.parse_source(text, "s.py")["cells"]
    assert [c["source"] for c in cells] == ["x = 1\n# ---\ny = 2"]
    with_header = "# ---\n# jupyter: {}\n# ---\n# %%\nz = 3\n"
    assert [c["source"] for c in nbf.parse_source(with_header, "s.py")["cells"]] == ["z = 3"]


def test_an_unsafe_archive_path_is_false_and_a_safe_one_true():
    assert nbf._safe_entry("/abs/nb.python") is False
    assert nbf._safe_entry("a/../nb.python") is False
    assert nbf._safe_entry("folder/nb.python") is True


def test_an_archive_may_unpack_to_exactly_its_total_cap(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 1000)
    doc = _doc("nb").encode()
    monkeypatch.setattr(nbf, "MAX_DBC_TOTAL", len(doc) + 50)
    data = _dbc([("nb.python", doc), ("pad.json", b"x" * 50)])
    assert [n["name"] for n in nbf.parse_dbc(data)] == ["nb"]
    monkeypatch.setattr(nbf, "MAX_DBC_TOTAL", len(doc) + 49)
    with pytest.raises(ValueError, match="unpacks to more than 128 MB"):
        nbf.parse_dbc(data)


class _Lying:
    """zf.open for an entry that unpacks to more than its header says."""

    def __init__(self, size):
        self.data = io.BytesIO(b" " * size)

    def __enter__(self):
        return self.data

    def __exit__(self, *exc):
        return False


def test_an_entry_bigger_than_its_header_says_is_refused(monkeypatch):
    data = _dbc([("nb.python", _doc("nb"))])
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 500)
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, info, *a, **k: _Lying(501))
    with pytest.raises(ValueError, match="nb.python is larger than 16 MB unpacked"):
        nbf.parse_dbc(data)


def test_entries_that_unpack_past_the_total_are_refused_by_what_was_read(monkeypatch):
    data = _dbc([("a.python", "x"), ("b.python", "y")])
    monkeypatch.setattr(nbf, "MAX_DBC_TOTAL", 15)
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, info, *a, **k: _Lying(8))
    with pytest.raises(ValueError, match="unpacks to more than 128 MB"):
        nbf.parse_dbc(data)


def test_a_notebook_name_is_cut_at_120_characters():
    assert nbf.notebook_name("a" * 130) == "a" * 120


def test_an_ipynb_export_is_indented_by_one_space():
    text = nbf.export_text("ipynb", "nb", CELLS)
    assert text.splitlines()[1].startswith(' "') and text.endswith("}\n")


def test_dbc_commands_that_are_not_objects_are_skipped():
    doc = json.dumps({"name": "nb", "language": "python",
                      "commands": ["stray text", 7, {"command": "x = 1", "position": 1}]})
    [nb] = nbf.parse_dbc(_dbc([("nb.python", doc)]))
    assert [c["source"] for c in nb["cells"]] == ["x = 1"]


def test_an_unknown_cell_magic_is_exported_to_databricks_as_written():
    text = nbf.to_databricks_py([{"type": "code", "source": "%%timeit\nx = 1"}])
    assert text == "# Databricks notebook source\n# MAGIC %%timeit\n# MAGIC x = 1\n"


def test_an_ipynb_of_exactly_the_size_cap_is_read(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_TEXT", 20)
    with pytest.raises(ValueError, match="isn't valid JSON"):
        nbf.parse_ipynb("x" * 20)
    with pytest.raises(ValueError, match="larger than 32 MB"):
        nbf.parse_ipynb("x" * 21)


LONG = "n" * 130 + ".python"


def _named_message(exc):
    text = str(exc.value)
    assert text.startswith(LONG[:120] + " ") and LONG[:121] not in text
    return text


def test_a_long_entry_name_is_cut_at_120_characters_when_too_big(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 10)
    with pytest.raises(ValueError) as exc:
        nbf.parse_dbc(_dbc([(LONG, "x" * 11)]))
    assert "is larger than 16 MB unpacked" in _named_message(exc)


def test_a_long_entry_name_is_cut_at_120_characters_when_it_unpacks_bigger(monkeypatch):
    data = _dbc([(LONG, "x")])
    monkeypatch.setattr(nbf, "MAX_DBC_ENTRY", 10)
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, info, *a, **k: _Lying(11))
    with pytest.raises(ValueError) as exc:
        nbf.parse_dbc(data)
    assert "is larger than 16 MB unpacked" in _named_message(exc)


def test_a_long_entry_name_is_cut_at_120_characters_when_damaged(monkeypatch):
    import zlib

    def broken(self, info, *a, **k):
        raise zlib.error("bad")
    data = _dbc([(LONG, "x")])
    monkeypatch.setattr(zipfile.ZipFile, "open", broken)
    with pytest.raises(ValueError) as exc:
        nbf.parse_dbc(data)
    assert "in the archive is damaged" in _named_message(exc)
