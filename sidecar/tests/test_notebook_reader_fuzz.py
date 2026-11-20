"""Seeded fuzz tests for the readers of .ipynb notebooks: the import dialog's (notebook_formats.parse_ipynb),
Open notebook file's (notebook_files.import_check / read), a Fabric item's definition (notebook_files.ipynb_of)
and the folder names of notebook_folders. Every input is read with all its cells, or refused with a
ValueError, never a crash; outputs and cell types that don't carry over are said, not dropped silently.

The generators use the random module with fixed seeds, so a failure names a case that can be run again.
"""

from __future__ import annotations

import base64
import json
import random

import pytest

from fabric_desktop import envprofile
from fabric_desktop import notebook_files as nf
from fabric_desktop import notebook_formats as nbf
from fabric_desktop.notebook_folders import check_folder

CASES = 1500
JUNK = [None, 0, 1, 2.5, True, "", "s", [], {}, ["a", 1], [None], {"a": 1}, ["x\n", "y"], "\x00", "é\n日本", 10**30]
CELL_TYPES = ["code", "markdown", "raw", "heading", "", None, 5, ["code"], {"t": 1}]
OUTPUTS = [[{"output_type": "stream", "name": "stdout", "text": ["a\n", "b"]}],
           [{"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo="}, "metadata": {}}],
           [{"output_type": "error", "ename": "E", "evalue": "v", "traceback": []}], [5], [{"output_type": 5}], "text", 7]
CELL_META = [{"tags": 5}, {"tags": [1, "parameters"]}, {"jupyter": 5}, {"jupyter": {"source_hidden": 1}},
             {"run_control": "x"}, {"run_control": {"frozen": True}}, {"fabric_desktop": {"chart": 5, "breakpoints": "x"}},
             {"fabric_desktop": {"charts": [1, {"kind": "bar"}]}}, {"application/vnd.databricks.v1+cell": 5}]
NB_META = [{"fabric_desktop": 5}, {"fabric_desktop": {"compute": [1]}}, {"fabric_desktop": {"compute": {}}},
           {"kernelspec": {"name": 5}}, {"dependencies": {"lakehouse": 5}}, {"dependencies": 5}, {"language_info": []}]


def _cell(r: random.Random) -> object:
    if r.random() < 0.05:
        return r.choice(JUNK)
    c: dict[str, object] = {}
    if r.random() < 0.9:
        c["cell_type"] = r.choice(CELL_TYPES)
    if r.random() < 0.9:
        c["source"] = r.choice(JUNK + ["print(1)", ["import os\n", "os.getcwd()"], "x = '\\ud800'"])
    if r.random() < 0.5:
        c["outputs"] = r.choice(JUNK + OUTPUTS)
    if r.random() < 0.5:
        c["metadata"] = r.choice(JUNK + CELL_META)
    if r.random() < 0.3:
        c["attachments"] = r.choice(JUNK + [{"a.png": {"image/png": "AA"}}, {"a.png": 5}])
    if r.random() < 0.3:
        c["id"] = r.choice(JUNK + ["abc", "abc"])
    return c


def _notebook(r: random.Random) -> dict[str, object]:
    nb: dict[str, object] = {"cells": [_cell(r) for _ in range(r.randint(0, 6))]}
    if r.random() < 0.8:
        nb["nbformat"] = r.choice([4, 4, 4, 3, 5, "4", None, 4.0])
    if r.random() < 0.5:
        nb["metadata"] = r.choice(JUNK + NB_META)
    return nb


def _readable(cells: list[object]) -> bool:
    """What the readers must accept: every cell an object whose source is text or a list of text."""
    for c in cells:
        if not isinstance(c, dict):
            return False
        s = c.get("source", "")
        if not (isinstance(s, str) or isinstance(s, list) and all(isinstance(x, str) for x in s)):
            return False
        if "\ud800" in ("".join(s) if isinstance(s, list) else s):
            return False
    return True


def _check_imported(nb: dict[str, object], got: dict[str, object], case: object) -> None:
    cells = nb["cells"]
    assert isinstance(cells, list)
    assert len(got["cells"]) == len(cells), case  # no cell left out
    for raw, cell in zip(cells, got["cells"], strict=True):
        src = raw.get("source", "")
        assert cell["source"] == ("".join(src) if isinstance(src, list) else src), case
        assert cell["type"] == ("markdown" if raw.get("cell_type") == "markdown" else "code"), case


def _definition(text: bytes) -> dict[str, object]:
    return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": base64.b64encode(text).decode()}]}}


def test_generated_notebooks_are_read_whole_or_refused_in_every_reader():
    for case in range(CASES):
        nb = _notebook(random.Random(9000 + case))
        text = json.dumps(nb)
        readable = _readable(nb["cells"])
        try:
            got = nbf.parse_ipynb(text)
        except ValueError:
            assert not readable, case
        else:
            assert readable, case
            _check_imported(nb, got, case)
            dropped = {n["cell"] for n in got["notes"] if n["kind"] == "dropped"}
            changed = {n["cell"] for n in got["notes"] if n["kind"] == "changed"}
            for i, c in enumerate(nb["cells"], 1):
                assert (i in dropped) == bool(c.get("outputs")), (case, i)  # outputs left out are said
                assert (i in changed) == (c.get("cell_type") not in ("code", "markdown")), (case, i)
        format4 = isinstance(nb.get("nbformat"), int) and nb["nbformat"] >= 4
        for read in (lambda text=text: nf.read(nf._parse(text)),  # noqa: SLF001 - the import check's reader
                     lambda text=text: nf.read(nf.checked(nf.ipynb_of(_definition(text.encode()))))):
            try:
                got = read()
            except ValueError:
                assert not (readable and format4), case
            else:
                assert readable and format4, case
                _check_imported(nb, got, case)
                assert got["summary"]["outputs_dropped"] == any(c.get("outputs") for c in nb["cells"]), case


def _valid(r: random.Random, big: bool) -> dict[str, object]:
    cells: list[dict[str, object]] = []
    for i in range(r.randint(1, 5)):
        source = f"x{i} = {i}\n" * (r.randint(50_000, 150_000) if big and i == 0 else r.randint(1, 5))
        cell: dict[str, object] = {"cell_type": r.choice(["code", "markdown"]), "id": f"c{i}", "metadata": {},
                                   "source": source.splitlines(keepends=True) if r.random() < 0.5 else source}
        if cell["cell_type"] == "code":
            cell["execution_count"] = i
            image = base64.b64encode(r.randbytes(1_500_000 if big else 64)).decode()
            cell["outputs"] = [{"output_type": "stream", "name": "stdout", "text": ["é\x00日本\n"] * 3},
                               {"output_type": "display_data", "data": {"image/png": image}, "metadata": {}}]
        cells.append(cell)
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3"}}, "cells": cells}


def _mutate(r: random.Random, data: bytes) -> bytes:
    b = bytearray(data)
    for _ in range(r.randint(1, 4)):
        op = r.random()
        if op < 0.35 and b:
            b[r.randrange(len(b))] ^= 1 << r.randrange(8)
        elif op < 0.6:
            at = r.randint(0, len(b))
            b[at:at] = r.choice([b"\x00", b"\xff", b"\xc3", b"\xef\xbb\xbf", b"\\ud800", b"[", b"{", b'"', b"\\"])
        elif op < 0.8:
            del b[r.randint(0, len(b)):]
        else:
            b[:0] = b"\xef\xbb\xbf"
    return bytes(b)


def test_byte_flips_truncation_bom_and_invalid_utf8_are_read_or_refused():
    for case in range(400):
        r = random.Random(12000 + case)
        nb = _valid(r, big=False)
        data = _mutate(r, json.dumps(nb, ensure_ascii=r.random() < 0.5).encode())
        for errors in ("replace", "surrogateescape"):  # as a browser decodes it, and with the bad bytes kept
            text = data.decode("utf-8", errors=errors)
            for read in (lambda text=text: nbf.parse_ipynb(text), lambda text=text: nf.read(nf._parse(text)),  # noqa: SLF001
                         lambda data=data: nf.read(nf.checked(nf.ipynb_of(_definition(data))))):
                try:
                    got = read()
                except ValueError:
                    continue
                assert all(isinstance(c["source"], str) for c in got["cells"]), case
                json.dumps(got["cells"], ensure_ascii=False).encode("utf-8")  # the cells can be saved


def test_megabytes_of_source_and_outputs_come_in_whole_with_the_outputs_named():
    for case in range(3):
        nb = _valid(random.Random(13000 + case), big=True)
        text = json.dumps(nb)
        got = nbf.parse_ipynb(text)
        _check_imported(nb, got, case)
        code = [i for i, c in enumerate(nb["cells"], 1) if c["cell_type"] == "code"]
        assert sorted(n["cell"] for n in got["notes"] if n["kind"] == "dropped") == code
        _check_imported(nb, nf.read(nf._parse(text)), case)  # noqa: SLF001


def test_nesting_too_deep_is_refused_by_name():
    deep = "[" * 100_000 + "]" * 100_000
    for text in (deep, '{"cells": ' + deep + "}", '{"nbformat": 4, "cells": [{"source": ' + deep + "}]}"):
        with pytest.raises(ValueError):
            nbf.parse_ipynb(text)
        with pytest.raises(ValueError):
            nf._parse(text)  # noqa: SLF001
        with pytest.raises(ValueError, match="can't be read"):
            nf.ipynb_of(_definition(text.encode()))


# ---- regressions found by the generators above


@pytest.mark.parametrize("cells, problem", [
    (["text"], "a cell isn't readable"),
    ([{"cell_type": "code", "source": ["a", None]}], "cell 1's source isn't text"),
    ([{"cell_type": "code", "source": "ok"}, {"cell_type": "markdown", "source": 5}], "cell 2's source isn't text"),
    ([{"cell_type": "code", "source": "x = '\ud800'"}], "cell 1's source has characters that aren't valid Unicode"),
])
def test_a_cell_that_cant_be_read_is_refused_naming_it(cells, problem):
    text = json.dumps({"nbformat": 4, "cells": cells})
    with pytest.raises(ValueError, match=f"^This .ipynb file can't be imported: {problem}.$"):
        nbf.parse_ipynb(text)
    with pytest.raises(ValueError, match=f"^This file isn't a notebook: {problem}.$"):
        nf._parse(text)  # noqa: SLF001


def test_settings_of_the_wrong_type_are_dropped_and_the_cells_kept():
    nb = {"nbformat": 4, "metadata": {"fabric_desktop": 5}, "cells": [
        {"cell_type": "code", "source": "a", "metadata": [1]},
        {"cell_type": "code", "source": "b", "metadata": {"tags": 5, "jupyter": "x", "run_control": "y"}}]}
    assert [c["source"] for c in nbf.parse_ipynb(json.dumps(nb))["cells"]] == ["a", "b"]
    assert [c["source"] for c in nf.read(nf._parse(json.dumps(nb)))["cells"]] == ["a", "b"]  # noqa: SLF001


def test_import_says_which_outputs_and_cell_types_dont_come_in():
    nb = {"nbformat": 4, "cells": [{"cell_type": "code", "source": "1", "outputs": [{"output_type": "stream", "text": "1"}]},
                                   {"cell_type": "raw", "source": "plain"}, {"cell_type": "markdown", "source": "# T"}]}
    assert nbf.parse_ipynb(json.dumps(nb))["notes"] == [
        {"cell": 1, "kind": "dropped", "what": "The cell's saved outputs: imported notebooks start without outputs. "
                                               "Run the cell to get them."},
        {"cell": 2, "kind": "changed", "what": "The cell's type (raw) isn't code or markdown, so it comes in as a code cell."}]


def test_a_bom_before_the_json_is_read_past():
    text = "﻿" + json.dumps({"nbformat": 4, "cells": [{"cell_type": "code", "source": "1"}]})
    assert nbf.parse_ipynb(text)["cells"][0]["source"] == "1"
    assert nf.read(nf._parse(text))["cells"][0]["source"] == "1"  # noqa: SLF001


@pytest.mark.parametrize("definition", [
    {}, {"parts": 5}, {"parts": [5]}, {"parts": [{"path": "a.ipynb"}]}, {"parts": [{"path": "a.ipynb", "payload": 5}]},
    {"definition": 5}, {"definition": {"parts": "x"}}, [],
])
def test_a_fabric_definition_without_a_notebook_part_is_refused(definition):
    with pytest.raises(ValueError, match="^Fabric didn't return the notebook's cells.$"):
        nf.ipynb_of(definition)


@pytest.mark.parametrize("payload", [b"{trunc", b"\xff\xfe\x00", b"[" * 100_000, b""], ids=["cut", "not-utf8", "deep", "empty"])
def test_a_fabric_definition_that_isnt_a_notebook_is_refused(payload):
    with pytest.raises(ValueError, match="^Fabric returned the notebook's cells in a form that can't be read.$"):
        nf.ipynb_of(_definition(payload))


def test_folder_names_are_checked_or_refused():
    atoms = ["a", "/", "//", ".", "..", " ", "\x00", "\n", "日本", "<", ":", "\\", "con", "x" * 200, ".resources", "\ud800"]
    for case in range(2000):
        r = random.Random(14000 + case)
        path = "".join(r.choice(atoms) for _ in range(r.randint(0, 8)))
        try:
            got = check_folder(path)
        except ValueError:
            continue
        assert isinstance(got, str) and got and "\x00" not in got and ".." not in got.split("/"), (case, path)


def test_cells_too_deeply_nested_for_python_are_reported_not_raised():
    for source in ("x=" + "+".join(["1"] * 200_000), "x=" + "-" * 100_000 + "1", "x=a" + ".b" * 100_000,
                   "x = '\ud800'"):
        got = envprofile.imports(source)
        assert got["parsed"] is False and got["error"]
