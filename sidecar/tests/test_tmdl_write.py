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
    # Translations ride in the Power BI section with roles and perspectives; other tools won't see them in Ossie.
    assert [c["name"] for c in after["model"]["cultures"]] == ["fr-FR"]
    assert files["definition/cultures/fr-FR.tmdl"].startswith("cultureInfo fr-FR\n")
    assert any(r["object"] == "model" and r["kind"] == "kept" and "translations" in r["message"] for r in out["report"])
    assert not any(r["object"].startswith("culture") for r in out["report"])


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
    bim["model"]["cultures"] = [{"name": "fr-FR", "translations": {}}]  # an empty setting: said, not dropped
    bim["model"]["tables"][0]["someNewList"] = [{"name": "x"}]
    files, report, back = _read_back(bim)
    m = next(t for t in back["model"]["tables"] if t["name"] == "fact_sales")["measures"][0]
    assert m["expression"] == "COUNTROWS(\n  'fact_sales')"
    assert {r["object"] for r in report} == {"culture 'fr-FR' translations", "table 'fact_sales' someNewList"}
    assert all(r["kind"] == "lost" for r in report)


def test_too_many_tables_or_too_much_text_is_refused(monkeypatch):
    bim = {"name": "big", "model": {"tables": [{"name": f"t{i}"} for i in range(tmdl.MAX_TABLES + 1)]}}
    with pytest.raises(ValueError, match="at most 5000"):
        tmdl.from_bim(bim)
    monkeypatch.setattr(tmdl, "MAX_CHARS", 50)
    with pytest.raises(ValueError, match="larger than 20 MB"):
        tmdl.from_bim(_bim())


# ---- hostile models (Security QC's list for PB-110)

HOSTILE_NAMES = ["it's", 'say "hi"', "tab\there", "nul\x00here", " lead", "trail ",
                 "a = b", "key: value", "///not a description", "```", "column x", "'", "''", "table", "ref",
                 "measure 'M' = 1"]
# Characters other readers and editors may break a line on (str.splitlines), besides \n and \r.
SEPARATORS = ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_names_round_trip_exactly_and_never_start_a_line(name):
    bim = {"name": "m", "model": {"tables": [{"name": name, "columns": [{"name": name, "dataType": "string"}],
                                               "measures": [{"name": name + "!", "expression": "1"}]}]}}
    files, report, back = _read_back(bim)
    assert report == []
    t = back["model"]["tables"][0]
    assert t["name"] == name and t["columns"][0]["name"] == name and t["measures"][0]["name"] == name + "!"
    assert len(back["model"]["tables"]) == 1 and len(t["columns"]) == 1 and len(t["measures"]) == 1


@pytest.mark.parametrize("name", ["new\nline", "carriage\rreturn", "both\r\n",
                                  *(f"x{c}\tmeasure Evil = 666" for c in SEPARATORS)])
def test_names_with_line_breaks_are_refused_by_name(name):
    bim = {"name": "m", "model": {"tables": [{"name": "T", "measures": [{"name": name, "expression": "1"}]}]}}
    with pytest.raises(ValueError, match="can't be written as TMDL: .*table 'T' measure .*line break"):
        tmdl.from_bim(bim)


@pytest.mark.parametrize("sep", SEPARATORS)
def test_separators_in_values_are_fenced_so_no_reader_sees_a_new_line(sep):
    evil = f"1{sep}\tmeasure Evil = 666"
    bim = {"name": "m", "model": {"tables": [{"name": "T", "description": evil, "measures": [
        {"name": "M", "expression": evil, "formatString": evil, "description": evil}]}]}}
    files, report, back = _read_back(bim)
    assert report == []
    assert back["model"]["tables"][0]["measures"] == [{"name": "M", "expression": evil, "formatString": evil, "description": evil}]
    lines = files["definition/tables/T.tmdl"].splitlines()  # split the way any reader might
    fenced = [i for i, line in enumerate(lines) if line.strip().endswith("```")]
    for i, line in enumerate(lines):
        if "measure Evil" in line:
            assert sum(1 for f in fenced if f < i) % 2 == 1, "only inside an open fence"


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


# ---- cultures: translations and linguistic metadata

CULTURES = [
    {"name": "fr-FR", "linguisticMetadata": {"content": {"Version": "1.0.0", "Language": "fr-FR",
                                                         "DynamicImprovement": "HighConfidence", "Entities": {"a": [1, 2.5, None, True]}},
                                             "contentType": "json"},
     "annotations": [{"name": "note", "value": "kept"}],
     "translations": {"model": {"name": "Model", "translatedCaption": "Modèle", "translatedDescription": "Le modèle",
                                "tables": [{"name": "fact_sales", "translatedCaption": "Ventes",
                                            "translatedDescription": "deux\nlignes",
                                            "columns": [{"name": "amount", "translatedCaption": "true",
                                                         "translatedDisplayFolder": " Valeurs "}],
                                            "measures": [{"name": "fact_sales total amount", "translatedCaption": "Total"}]},
                                           {"name": "dim_customer", "translatedCaption": "Client",
                                            "hierarchies": [{"name": "Geography", "translatedCaption": "Géographie",
                                                             "levels": [{"name": "Country", "translatedCaption": "Pays"}]}]}],
                                "roles": [{"name": "EU only", "translatedCaption": "UE"}],
                                "perspectives": [{"name": "Finance", "translatedCaption": "Finances"}]}}},
    {"name": "en-US", "linguisticMetadata": {"content": "<Linguistic/>\n<x/>", "contentType": "xml"}},
    {"name": "de-DE"},
]


def test_cultures_write_as_tmdl_and_read_back_exactly():
    bim = _bim()
    bim["model"]["cultures"] = copy.deepcopy(CULTURES)
    files, report, back = _read_back(bim)
    assert report == []
    assert tmdl.differences(bim, back) == [] and tmdl.differences(back, bim) == []
    assert back["model"]["cultures"] == CULTURES  # in their order too, from model.tmdl's refs
    assert {p for p in files if "/cultures/" in p} == {f"definition/cultures/{c}.tmdl" for c in ("fr-FR", "en-US", "de-DE")}
    model = files["definition/model.tmdl"]
    assert model.index("ref cultureInfo fr-FR") < model.index("ref cultureInfo en-US") < model.index("ref cultureInfo de-DE")
    fr = files["definition/cultures/fr-FR.tmdl"]
    assert fr.startswith("cultureInfo fr-FR\n\n\tlinguisticMetadata =\n\t\t\t{\n") and "\t\t\t}\n\t\tcontentType: json\n" in fr
    en = files["definition/cultures/en-US.tmdl"]
    assert en == "cultureInfo en-US\n\n\tlinguisticMetadata =\n\t\t\t```\n\t\t\t<Linguistic/>\n\t\t\t<x/>\n\t\t\t```\n\t\tcontentType: xml\n\n"
    assert "\ttranslations\n\t\tmodel Model\n\t\t\tcaption: Modèle\n" in fr
    assert "\t\t\t\tcaption: Ventes\n" in fr and '\t\t\t\t\tcaption: "true"\n' in fr
    again, _ = tmdl.from_bim(back)
    assert {p: t for p, t in again.items() if "/cultures/" in p} == {p: t for p, t in files.items() if "/cultures/" in p}


def test_tmsl_to_tmdl_to_ossie_to_tmdl_keeps_cultures():
    bim = _bim()
    bim["model"]["cultures"] = copy.deepcopy(CULTURES)
    files, report = tmdl.from_bim(bim)
    assert report == []
    out = model_ossie.tmdl_to_ossie(files, "SM_Gold", translate_dax=False)
    assert not any(r["kind"] == "lost" for r in out["report"])
    files2, report2 = model_ossie.ossie_to_tmdl(out["yaml"])
    assert report2 == []
    assert {p for p in files2 if "/cultures/" in p} == {p for p in files if "/cultures/" in p}
    after = tmdl.to_bim(files2, "SM_Gold")[0]
    assert _same(bim, after) and after["model"]["cultures"] == CULTURES


def test_what_a_culture_cant_carry_is_named():
    bim = {"name": "m", "model": {"cultures": [
        {"name": "fr-FR", "translations": {"model": {"tables": [{"name": "T", "translatedCaption": 5,
                                                                "measures": [{"name": "M", "kpi": {"translatedCaption": "k"}}],
                                                                "partitions": [{"name": "p", "translatedCaption": "x"}]},
                                                               {"translatedCaption": "no name"}]},
                                           "other": {"x": 1}}},
        {"translations": {}}, "not an object"]}}
    _, report = tmdl.from_bim(bim)
    assert {r["object"] for r in report} == {
        "culture 'fr-FR' translations model table 'T' translatedCaption",
        "culture 'fr-FR' translations model table 'T' measure 'M' kpi",
        "culture 'fr-FR' translations model table 'T' partitions",
        "culture 'fr-FR' translations model table None",
        "culture 'fr-FR' translations other", "culture", "cultures"}
    assert all(r["kind"] == "lost" for r in report)


@pytest.mark.parametrize("name", HOSTILE_NAMES + ["../../evil", "CON", "a/b\\c"])
def test_hostile_culture_and_translation_names_round_trip_in_safe_files(name):
    bim = {"name": "m", "model": {"tables": [{"name": name, "columns": [{"name": name, "dataType": "string"}]}], "cultures": [
        {"name": name, "translations": {"model": {"tables": [{"name": name, "translatedCaption": name,
                                                              "columns": [{"name": name, "translatedCaption": name}]}]}}}]}}
    files, report, back = _read_back(bim)
    assert report == [] and tmdl.differences(bim, back) == []
    assert back["model"]["cultures"][0]["name"] == name
    assert back["model"]["cultures"][0]["translations"]["model"]["tables"] == bim["model"]["cultures"][0]["translations"]["model"]["tables"]
    paths = [p for p in files if "/cultures/" in p]
    assert len(paths) == 1
    assert all(PureWindowsPath(p).parts[:2] == ("definition", "cultures") and ".." not in PureWindowsPath(p).parts
               and len(PureWindowsPath(p).parts) == 3 for p in paths)


@pytest.mark.parametrize("name", ["new\nline", "carriage\rreturn", *(f"x{c}\tcultureInfo Evil" for c in SEPARATORS)])
def test_culture_and_translation_names_with_line_breaks_are_refused_by_name(name):
    with pytest.raises(ValueError, match="can't be written as TMDL: culture .*line break"):
        tmdl.from_bim({"name": "m", "model": {"cultures": [{"name": name}]}})
    bim = {"name": "m", "model": {"cultures": [{"name": "fr-FR", "translations": {"model": {"tables": [
        {"name": "T", "measures": [{"name": name, "translatedCaption": "x"}]}]}}}]}}
    with pytest.raises(ValueError, match="can't be written as TMDL: culture 'fr-FR' translations model table 'T' measure .*line break"):
        tmdl.from_bim(bim)


@pytest.mark.parametrize("sep", SEPARATORS)
def test_separators_in_translations_and_metadata_are_fenced_or_escaped(sep):
    evil = f"1{sep}\tcultureInfo Evil"
    bim = {"name": "m", "model": {"cultures": [{"name": "fr-FR",
        "linguisticMetadata": {"content": {"k": evil, evil: [evil]}, "contentType": "json"},
        "translations": {"model": {"translatedCaption": evil, "tables": [{"name": "T", "translatedDescription": evil}]}}}]}}
    files, report, back = _read_back(bim)
    assert report == [] and tmdl.differences(bim, back) == []
    assert back["model"]["cultures"][0]["linguisticMetadata"] == bim["model"]["cultures"][0]["linguisticMetadata"]
    text = files["definition/cultures/fr-FR.tmdl"]
    json_part = text[:text.index("\t\tcontentType: json")]
    assert json_part.splitlines() == json_part.split("\n")[:-1], "unfenced JSON metadata has every separator as a \\u escape"
    lines = text.splitlines()  # split the way any reader might
    fenced = [i for i, line in enumerate(lines) if line.strip().endswith("```")]
    start, end = lines.index("\tlinguisticMetadata ="), lines.index("\t\tcontentType: json")
    for i, line in enumerate(lines):
        if "cultureInfo Evil" in line:
            in_json = start < i < end and line.startswith("\t\t\t")  # metadata: deeper than any property
            assert in_json or sum(1 for f in fenced if f < i) % 2 == 1, "only inside an open fence"


# A culture file as Power BI Desktop saves it: re-saved on this PC, it shows no diff.
POWER_BI_CULTURE = "\n".join([
    "cultureInfo en-US", "",
    "\tlinguisticMetadata =", "\t\t\t{", '\t\t\t  "Version": "1.0.0",', '\t\t\t  "Language": "en-US",',
    '\t\t\t  "DynamicImprovement": "HighConfidence",', '\t\t\t  "Entities": {', '\t\t\t    "sales": {',
    '\t\t\t      "Definition": {', '\t\t\t        "Binding": {', '\t\t\t          "ConceptualEntity": "Ventes é"',
    "\t\t\t        }", "\t\t\t      },", '\t\t\t      "State": "Generated",', '\t\t\t      "Weight": 0.99,',
    '\t\t\t      "Terms": [', '\t\t\t        {', '\t\t\t          "sales": {', '\t\t\t            "State": "Generated"',
    "\t\t\t          }", "\t\t\t        }", "\t\t\t      ]", "\t\t\t    }", "\t\t\t  }", "\t\t\t}",
    "\t\tcontentType: json", "", ""])


def test_a_power_bi_culture_file_saves_back_byte_identical():
    files = {"definition/model.tmdl": "model Model\n\tculture: en-US\nref cultureInfo en-US\n",
             "definition/cultures/en-US.tmdl": POWER_BI_CULTURE}
    bim, report = tmdl.to_bim(files, "m")
    assert report == []
    written, report = tmdl.from_bim(bim)
    assert report == [] and written["definition/cultures/en-US.tmdl"] == POWER_BI_CULTURE
    assert written["definition/model.tmdl"] == files["definition/model.tmdl"]


def test_a_fence_on_the_line_after_equals_is_read_as_a_fence():
    text = "cultureInfo de-DE\n\tlinguisticMetadata =\n\t\t\t```\n\t\t\t<a>\n\t\t\t  b\n\t\t\t</a>\n\t\t\t```\n\t\tcontentType: xml\n"
    bim, report = tmdl.to_bim({"definition/cultures/de-DE.tmdl": text}, "m")
    assert report == [] and bim["model"]["cultures"][0]["linguisticMetadata"] == {"content": "<a>\n  b\n</a>", "contentType": "xml"}


def test_a_translation_or_metadata_with_a_fence_line_is_refused_by_name():
    bim = {"name": "m", "model": {"cultures": [{"name": "fr-FR", "translations": {"model": {"translatedCaption": "a\n```\nb"}}}]}}
    with pytest.raises(ValueError, match="culture 'fr-FR' translations model translatedCaption: .*three backticks"):
        tmdl.from_bim(bim)
    bim = {"name": "m", "model": {"cultures": [{"name": "fr-FR", "linguisticMetadata": {"content": "<a>\n```\n</a>", "contentType": "xml"}}]}}
    with pytest.raises(ValueError, match="culture 'fr-FR' linguisticMetadata: .*three backticks"):
        tmdl.from_bim(bim)


def test_deeply_nested_linguistic_metadata_is_refused_not_a_crash():
    deep: list = []
    here = deep
    for _ in range(100_000):
        here.append([])
        here = here[0]
    with pytest.raises(ValueError, match="nested too deeply"):
        tmdl.from_bim({"name": "m", "model": {"cultures": [{"name": "fr-FR", "linguisticMetadata": {"content": deep, "contentType": "json"}}]}})
    text = "cultureInfo fr-FR\n\tlinguisticMetadata = " + "[" * 100_000 + "]" * 100_000 + "\n\t\tcontentType: json\n"
    with pytest.raises(ValueError, match="nested too deeply"):
        tmdl.to_bim({"definition/cultures/fr-FR.tmdl": text}, "m")


# ---- file names

@pytest.mark.parametrize(("name", "expected"), [
    ("../x", "_x"), ("..", "_"), ("C:\\Windows", "C__Windows"), ("\\\\server\\share", "__server_share"),
    ("/etc/passwd", "_etc_passwd"), ('a<>:"|?*b', "a_______b"), ("tab\there", "tab_here"), ("dot.", "dot"),
    ("space ", "space"), ("x\x85y", "x_y"), ("CON", "_CON"), ("con.txt", "_con.txt"), ("Com1", "_Com1"), ("LPT9.tmdl", "_LPT9.tmdl"),
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


def test_a_model_nested_too_deeply_is_refused_not_a_crash():
    deep: dict = {}
    here = deep
    for _ in range(5000):
        here["x"] = {}
        here = here["x"]
    with pytest.raises(ValueError, match="nested too deeply"):
        tmdl.from_bim({"name": "m", "model": {"tables": [{"name": "T", "refreshPolicy": deep}]}})


def test_comparing_models_nested_too_deeply_is_refused_and_the_round_trip_check_says_it_didnt_run():
    deep: object = "x"
    for _ in range(3000):
        deep = {"a": [deep]}
    bim = {"name": "m", "model": {"annotations": [{"name": "an", "value": deep}], "tables": [
        {"name": "t", "columns": [{"name": "c", "dataType": "string", "sourceColumn": "c"}]}]}}
    assert tmdl.differences({"model": {"x": [{"a": 1}]}}, {"model": {"x": [{"a": "1"}]}}) == []
    assert tmdl.differences({"model": {"x": 1}}, {"model": {"x": 2}}) == ["x"]
    with pytest.raises(ValueError, match="nested too deeply to compare"):
        tmdl.differences(bim, bim)
    check = model_ossie.round_trip_check(bim)
    assert (check["ran"], check["passed"], check["error"]) == (
        False, None, "ValueError: This model is nested too deeply to compare")
    rows = model_ossie.export(bim, validate=True)["round_trip"]
    assert [r["message"] for r in rows] == [
        "the round-trip check didn't run: the round trip couldn't run (ValueError: This model is nested too deeply to compare)"]


def test_a_property_name_that_cant_be_written_is_reported_once():
    bim = {"name": "m", "model": {"tables": [{"name": "T", "bad key": 1}]}}
    _, report = tmdl.from_bim(bim)
    assert report == [{"object": "table 'T' property bad key", "kind": "lost", "message": "this property name can't be written as TMDL"}]
