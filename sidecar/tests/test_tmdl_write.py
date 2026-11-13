"""Writing TMDL on this PC (tmdl.from_bim, PB-110): TMSL model.bim as TMDL files that read back as the same
model, and TMDL to and from Apache Ossie with nothing dropped silently."""

import copy
from pathlib import PureWindowsPath

import pytest

from fabric_desktop import model_ossie, tmdl
from test_model_ossie import SOURCE, _bim, _ossie
from test_tmdl import FILES


def _read_back(bim):
    files, report = tmdl.from_bim(bim)
    back, _ = tmdl.to_bim(files, bim.get("name", ""))
    return files, report, back


def _same(a, b):
    return tmdl.differences(a, b) == [] and tmdl.differences(b, a) == []


# ---- the round trips

def test_a_model_read_from_tmdl_writes_back_to_the_same_model():
    bim, _ = tmdl.to_bim(FILES, "Sales model")
    files, report, back = _read_back(bim)
    assert report == []
    assert back == bim | {"model": back["model"]}  # same database settings
    assert _same(bim, back)
    assert {t["name"] for t in back["model"]["tables"]} == {"Sales 'EU'", "Calc"}
    assert "definition/tables/Sales 'EU'.tmdl" in files and "definition/roles/EU only.tmdl" in files
    assert "ref table 'Sales ''EU'''" in files["definition/model.tmdl"]  # the model's table order, for Fabric


def test_writing_is_stable_twice_over():
    bim, _ = tmdl.to_bim(FILES, "Sales model")
    files, _ = tmdl.from_bim(bim)
    again, _ = tmdl.from_bim(tmdl.to_bim(files, "Sales model")[0])
    assert again == files


def test_tmdl_to_ossie_to_tmdl_keeps_every_object():
    out = model_ossie.tmdl_to_ossie(FILES, "Sales model", translate_dax=False)
    files, report = model_ossie.ossie_to_tmdl(out["yaml"])
    assert report == []
    before, _ = tmdl.to_bim(FILES, "Sales model")
    after, _ = tmdl.to_bim(files, "Sales model")
    assert _same(before, after)
    # What can't travel is named: translations the reader doesn't read, and what other tools won't see in Ossie.
    lost = {(r["object"], r["kind"]) for r in out["report"]}
    assert ("culture fr-FR", "lost") in lost
    assert ("model", "kept") in lost  # roles and perspectives ride in the Power BI section


def test_a_fabric_model_to_ossie_to_tmdl_is_the_same_model():
    bim = _bim()
    out = model_ossie.export(bim, translate_dax=False)
    files, report = model_ossie.ossie_to_tmdl(out["yaml"])
    assert report == []
    assert _same(bim, tmdl.to_bim(files, "SM_Gold")[0])
    assert model_ossie.round_trip(bim) == []


def test_an_ossie_file_from_another_tool_becomes_tmdl_on_this_pc():
    files, report = model_ossie.ossie_to_tmdl(_ossie(), SOURCE)
    assert set(files) == {"definition/database.tmdl", "definition/model.tmdl", "definition/relationships.tmdl",
                          "definition/expressions.tmdl", "definition/tables/fact_sales.tmdl",
                          "definition/tables/dim_channel.tmdl"}
    assert report == []
    bim, _ = tmdl.to_bim(files, "orders")
    fact = next(t for t in bim["model"]["tables"] if t["name"] == "fact_sales")
    assert [c["name"] for c in fact["columns"]] == ["customer_sk", "amount", "channel_sk"]
    assert fact["columns"][1]["description"] == "Order amount"
    assert fact["measures"][0]["name"] == "net revenue"
    assert fact["partitions"][0]["source"]["entityName"] == "fact_sales"


def test_round_trip_names_what_doesnt_come_back():
    bim = _bim()
    after = copy.deepcopy(bim)
    after["model"]["tables"][0]["measures"].pop()
    after["model"]["tables"][1]["columns"][1]["dataType"] = "int64"
    after["model"]["relationships"] = []
    after["model"]["tables"].append({"name": "extra"})
    assert sorted(tmdl.differences(bim, after)) == sorted([
        "table 'fact_sales' measure 'fact_sales total amount' (left out)",
        "table 'dim_customer' column 'country' dataType",
        "relationship 'r1' (left out)",
        "table 'extra' (added)"])


# ---- what can't be written is named, never dropped

def test_tmsl_lines_lists_cultures_and_unknown_lists():
    bim = _bim()
    bim["model"]["tables"][0]["measures"][0]["expression"] = ["COUNTROWS(", "  'fact_sales')"]
    bim["model"]["cultures"] = [{"name": "fr-FR", "translations": {}}]
    bim["model"]["tables"][0]["someNewList"] = [{"name": "x"}]
    files, report, back = _read_back(bim)
    m = next(t for t in back["model"]["tables"] if t["name"] == "fact_sales")["measures"][0]
    assert m["expression"] == "COUNTROWS(\n  'fact_sales')"
    assert {r["object"] for r in report} == {"culture 'fr-FR'", "table 'fact_sales' someNewList"}
    assert all(r["kind"] == "lost" for r in report)


def test_too_many_tables_or_too_much_text_is_refused(monkeypatch):
    bim = {"name": "big", "model": {"tables": [{"name": f"t{i}"} for i in range(tmdl.MAX_TABLES + 1)]}}
    with pytest.raises(ValueError, match="at most 5000"):
        tmdl.from_bim(bim)
    monkeypatch.setattr(tmdl, "MAX_CHARS", 50)
    with pytest.raises(ValueError, match="larger than 20 MB"):
        tmdl.from_bim(_bim())


# ---- hostile models (Security QC's list for PB-110)

HOSTILE_NAMES = ["it's", 'say "hi"', "tab\there", "line\u2028sep", "para\u2029sep", "nul\x00here", " lead", "trail ",
                 "a = b", "key: value", "///not a description", "```", "column x", "'", "''", "table", "ref",
                 "x\u0085y", "\x1c", "measure 'M' = 1"]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_names_round_trip_exactly_and_never_start_a_line(name):
    bim = {"name": "m", "model": {"tables": [{"name": name, "columns": [{"name": name, "dataType": "string"}],
                                               "measures": [{"name": name + "!", "expression": "1"}]}]}}
    files, report, back = _read_back(bim)
    assert report == []
    t = back["model"]["tables"][0]
    assert t["name"] == name and t["columns"][0]["name"] == name and t["measures"][0]["name"] == name + "!"
    assert len(back["model"]["tables"]) == 1 and len(t["columns"]) == 1 and len(t["measures"]) == 1


@pytest.mark.parametrize("name", ["new\nline", "carriage\rreturn", "both\r\n"])
def test_names_with_line_breaks_are_refused_by_name(name):
    bim = {"name": "m", "model": {"tables": [{"name": "T", "measures": [{"name": name, "expression": "1"}]}]}}
    with pytest.raises(ValueError, match="can't be written as TMDL: .*table 'T' measure .*line break"):
        tmdl.from_bim(bim)


def test_descriptions_cant_inject_objects_and_round_trip():
    evil = "fine\ncolumn injected\ndataType: string\nmeasure x = 1\n\n```\n/// more"
    bim = {"name": "m", "model": {"tables": [{"name": "T", "description": evil,
                                               "columns": [{"name": "c", "description": "a\r\nb\rc", "dataType": "string"}]}]}}
    files, report, back = _read_back(bim)
    assert report == []
    t = back["model"]["tables"][0]
    assert [c["name"] for c in t["columns"]] == ["c"] and "measures" not in t
    assert t["description"] == evil
    assert t["columns"][0]["description"] == "a\nb\nc"
    # Edge spaces are kept by writing the description fenced; a fence inside that is refused by name.
    bim["model"]["tables"][0]["description"] = "  padded  \ncolumn x"
    assert _read_back(bim)[2]["model"]["tables"][0]["description"] == "  padded  \ncolumn x"
    bim["model"]["tables"][0]["description"] = "  padded  \n```"
    with pytest.raises(ValueError, match="table 'T' description: .*three backticks"):
        tmdl.from_bim(bim)


def test_expressions_with_a_fence_are_refused_and_indented_ones_stay_in_their_block():
    bim = {"name": "m", "model": {"tables": [{"name": "T", "measures": [{"name": "M", "expression": "1\n```\ncolumn x"}]}]}}
    with pytest.raises(ValueError, match="table 'T' measure 'M'.*three backticks"):
        tmdl.from_bim(bim)
    dedent = "VAR a = 1\nRETURN\na\ncolumn x = 1\nmeasure y = 2"
    bim["model"]["tables"][0]["measures"][0]["expression"] = dedent
    bim["model"]["tables"][0]["columns"] = [{"name": "c", "dataType": "string", "expression": "  spaced  ", "type": "calculated"}]
    files, report, back = _read_back(bim)
    assert report == []
    t = back["model"]["tables"][0]
    assert t["measures"] == [{"name": "M", "expression": dedent}]
    assert t["columns"] == [{"name": "c", "dataType": "string", "expression": "  spaced  ", "type": "calculated"}]


def test_a_hostile_model_reads_back_with_the_same_objects_and_nothing_extra():
    bim = {"name": "m", "compatibilityLevel": 1604, "model": {
        "annotations": [{"name": "a", "value": "x\ntable Injected"}, {"name": "b", "value": "\nedge\n"}],
        "tables": [{"name": "T", "lineageTag": "true", "formatString": '"quoted"', "displayFolder": "",
                    "columns": [{"name": "c", "dataType": "string", "sourceColumn": "123", "isHidden": False,
                                 "summarizeBy": " none"}],
                    "partitions": [{"name": "p", "mode": "import", "source": {"type": "m", "expression": "let\n\tx = 1\nin\n\tx"}}]}],
        "relationships": [{"name": "r", "fromTable": "T", "fromColumn": "c c", "toTable": "it's", "toColumn": "d"}],
        "roles": [{"name": "../../evil", "members": [{"memberName": "a@b.c"}],
                   "tablePermissions": [{"name": "T", "filterExpression": "TRUE()"}]}]}}
    files, report, back = _read_back(bim)
    # Blank first and last lines of a fenced value don't survive TMDL: said by name, not dropped silently.
    assert report == [{"object": "annotation 'b' value", "kind": "lost", "message": "wouldn't read back from TMDL as it was"}]
    assert tmdl.differences(bim, back) == ["annotation 'b' value"]
    assert len(back["model"]["tables"]) == 1 and len(back["model"]["annotations"]) == 2
    assert back["model"]["annotations"][0]["value"] == "x\ntable Injected"
    assert "definition/roles/_.._evil.tmdl" in files
    assert all(PureWindowsPath(p).parts[0] == "definition" and ".." not in PureWindowsPath(p).parts for p in files)


# ---- file names

@pytest.mark.parametrize(("name", "expected"), [
    ("../x", "_x"), ("..", "_"), ("C:\\Windows", "C__Windows"), ("\\\\server\\share", "__server_share"),
    ("/etc/passwd", "_etc_passwd"), ('a<>:"|?*b', "a_______b"), ("tab\there", "tab_here"), ("dot.", "dot"),
    ("space ", "space"), ("CON", "_CON"), ("con.txt", "_con.txt"), ("Com1", "_Com1"), ("LPT9.tmdl", "_LPT9.tmdl"),
    ("nul ", "_nul"), ("", "_"), ("x" * 300, "x" * 100), ("line\u2028sep", "line_sep"),
])
def test_file_names_are_never_the_name_used_raw(name, expected):
    assert tmdl.file_name(name, set()) == expected


def test_file_names_that_collide_after_cleaning_or_case_folding_get_numbers():
    taken: set[str] = set()
    assert [tmdl.file_name(n, taken) for n in ("Sales", "sales", "SALES", "a/b", "a\\b")] == [
        "Sales", "sales (2)", "SALES (3)", "a_b", "a_b (2)"]
    bim = {"name": "m", "model": {"tables": [{"name": "Sales"}, {"name": "sales"}, {"name": "a?"}, {"name": "a*"}]}}
    files, report, back = _read_back(bim)
    assert report == [] and len([p for p in files if "/tables/" in p]) == 4
    assert [t["name"] for t in sorted(back["model"]["tables"], key=lambda t: t["name"])] == ["Sales", "a*", "a?", "sales"]
