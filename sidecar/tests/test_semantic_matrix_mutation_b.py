"""semantic_matrix: the edges the earlier tests left open (mutation batch 479). The kind and size checks on the
input, the rows that ran nothing, the note on objects the converter couldn't read, what a fidelity report's lost
objects are listed as, and the summary's security count. Nothing reaches Fabric or Snowflake."""

import copy
import json
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_snowflake, semantic_matrix as sm, semantic_parity as sp
from test_model_ossie import _bim
from test_ossie_fidelity import _with_calculation_group


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _rich():
    return _with_calculation_group(_bim())


def _export(bim):
    return yaml.safe_load(model_ossie.export(bim, translate_dax=True)["yaml"])


def _row(matrix, target):
    return next(r for r in matrix["targets"] if r["target"] == target)


@pytest.fixture(scope="module")
def rich():
    logging.getLogger("ossie_microsoft").disabled = True
    return sm.compatibility(_rich())


# ---- the input

def test_a_kind_is_read_and_an_unknown_kind_or_a_view_is_refused():
    assert _row(sm.compatibility(_rich(), kind="TMSL"), "ossie-0.2")["status"] == sm.CONVERTED
    nope = _row(sm.compatibility(_rich(), kind="nope"), "ossie-0.2")
    assert nope["status"] == sm.FAILED and "There's no kind 'nope'" in nope["reason"]
    view = _row(sm.compatibility(_rich(), kind="semantic_view"), "ossie-0.2")
    assert "A Semantic View is a target, not a source" in view["reason"]


def test_bytes_that_arent_utf8_are_named():
    row = _row(sm.compatibility(b"\xff\xfe{"), "ossie-0.2")
    assert row["status"] == sm.FAILED and "This file isn't UTF-8 text" in row["reason"]


def test_ossie_json_text_is_read_up_to_its_cap_and_refused_past_it():
    text = json.dumps(_export(_rich()))
    at_cap = text + " " * (model_ossie.MAX_CHARS - len(text))
    assert _row(sm.compatibility(at_cap), "ossie-0.2")["status"] == sm.SOURCE
    over = _row(sm.compatibility(at_cap + " "), "ossie-0.2")
    assert over["status"] == sm.FAILED
    assert "This file is 20 MB, but an Ossie model is at most 20 MB" in over["reason"]


def test_a_model_bim_past_the_ossie_cap_isnt_held_to_it():
    text = json.dumps(_rich())
    row = _row(sm.compatibility(text + " " * (model_ossie.MAX_CHARS + 1 - len(text))), "power-bi-model")
    assert row["status"] == sm.SOURCE, row["reason"]


# ---- rows that ran nothing

def test_a_refused_row_has_nothing_dropped_and_names_each_role(monkeypatch):
    from fabric_desktop import semantic_view

    def refuse(*args, **kwargs):
        raise ValueError("not today")

    monkeypatch.setattr(semantic_view, "semantic_view", refuse)
    row = _row(sm.compatibility(_rich()), "fabric-semantic-view")
    assert row["status"] == sm.REFUSED and row["reason"] == "not today"
    assert row["dropped"] == {"objects": [], "total": 0} and row["extension_only"] == {"objects": [], "total": 0}
    assert [r["name"] for r in row["roles"]] == ["EU only"]
    assert [g["name"] for g in row["calculation_groups"]] == ["Time calc"]


def test_a_source_row_says_where_its_roles_are(rich):
    assert _row(rich, "power-bi-model")["roles"][0]["why"] == "in the source as the converter read it"
    own = _row(sm.compatibility(_export(_rich())), "ossie-0.2")
    assert own["roles"][0]["why"] == "in the source only in its POWER_BI extension"


# ---- judging a conversion

def test_a_failed_parity_report_gives_its_error_as_the_reason(monkeypatch):
    real = sp.parity_l2

    def failed(*args, **kwargs):
        return real(*args, **kwargs) | {"summary": sp.TEST_FAILED, "error": "the right side broke"}

    monkeypatch.setattr(sp, "parity_l2", failed)
    row = _row(sm.compatibility(_rich()), "ossie-0.2")
    assert row["result"] == sp.TEST_FAILED and row["reason"] == "the right side broke"


def test_objects_the_converter_couldnt_read_are_counted_in_a_note(monkeypatch, rich):
    assert not [n for n in _row(rich, "ossie-0.2")["notes"] if "couldn't be read through" in n]
    real = sp.parity_l2

    def unread(*args, **kwargs):
        report = copy.deepcopy(real(*args, **kwargs))
        objects = [o for o in report["objects"] if o["kind"] == "column" and o["ids"]["left"] is not None]
        for o in objects[:2]:
            o["fidelity"]["left"] = "Unsupported"
        objects[2]["result"] = sp.TEST_FAILED  # unverified too, but read
        return report

    monkeypatch.setattr(sp, "parity_l2", unread)
    notes = _row(sm.compatibility(_rich()), "ossie-0.2")["notes"]
    assert [n for n in notes if "couldn't be read through" in n] == [(
        "2 object(s) of the source couldn't be read through the converter, so this target isn't judged on them "
        "(see the parity report)")]


def test_the_model_itself_is_never_listed_as_dropped(rich):
    for row in rich["targets"]:
        assert all(o["kind"] != "model" for o in row["dropped"]["objects"] + row["extension_only"]["objects"])


def test_the_view_notes_the_one_kept_measure_l2_cant_read(rich):
    notes = _row(rich, "fabric-semantic-view")["notes"]
    assert [n.split(" ", 1)[0] for n in notes if "show Unsupported at L2" in n] == ["1"]


def test_every_warning_on_a_role_is_prominent_security(rich):
    for row in rich["targets"]:
        assert all(w["prominent"] is True for w in row["warnings"])


def test_the_summary_counts_security_warnings_only():
    s = sm.compatibility(_bim())["summary"]
    rows = sm.compatibility(_bim())["targets"]
    assert sum(1 for r in rows for w in r["warnings"] if w["severity"] != "security") == 0
    assert s["security_warnings"] == 3


# ---- what a fidelity report lists as lost

def _source():
    src = sm._Source()
    src.read(_rich(), None)
    return src


def _listing_row():
    return {"label": "L", "_dropped": sm._Listing(), "_extension": sm._Listing()}


def test_a_lost_object_named_by_the_source_is_listed_once_by_its_key():
    src = _source()
    name = "table 'fact_sales' column 'amount'"
    row = _listing_row()
    row["_dropped"].add(model_ossie._canon(src.ids[name]), name, "column", "from parity")
    sm._from_fidelity(row, src, {"objects": [{"class": "unsupported", "kind": "column", "object": name}]}, sm.BASES[0])
    assert row["_dropped"].shown()["total"] == 1


def test_a_lost_object_says_its_reason_then_its_remediation_then_why():
    row = _listing_row()
    fid = {"objects": [{"class": "unsupported", "kind": "measure", "object": "a", "reason": "r", "remediation": "m"},
                       {"class": "unsupported", "kind": "measure", "object": "b", "remediation": "m"},
                       {"class": "unsupported", "kind": "measure", "object": "c"}]}
    sm._from_fidelity(row, _source(), fid, sm.BASES[0])
    assert [o["why"] for o in row["_dropped"].shown()["objects"]] == ["r", "m", "L has no place for it, so it is left out"]


def test_a_converter_report_lists_an_object_without_a_key(monkeypatch):
    real = model_ossie.export

    def export(*args, **kwargs):
        out = real(*args, **kwargs)
        out["fidelity"]["objects"].append({"class": "unsupported", "kind": "measure", "object": "lost one",
                                           "reason": "no place"})
        return out

    monkeypatch.setattr(model_ossie, "export", export)
    dropped = _row(sm.compatibility(_rich()), "ossie-0.2")["dropped"]["objects"]
    assert [(o["object"], o["why"]) for o in dropped] == [("lost one", "no place")]


def test_a_power_bi_import_report_lists_an_object_without_a_key(monkeypatch):
    real = model_ossie.safe_fidelity

    def safe(*args, **kwargs):
        out = real(*args, **kwargs)
        out["objects"] = [*(out.get("objects") or []), {"class": "unsupported", "kind": "measure",
                                                         "object": "lost one", "reason": "no place"}]
        return out

    monkeypatch.setattr(model_ossie, "safe_fidelity", safe)
    dropped = _row(sm.compatibility(_export(_rich())), "power-bi-model")["dropped"]["objects"]
    assert [(o["object"], o["why"]) for o in dropped] == [("lost one", "no place")]


def test_a_fidelity_report_that_cant_be_built_notes_its_error(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("no fidelity today")

    monkeypatch.setattr(model_ossie, "fidelity", broken)
    note = _row(sm.compatibility(_rich()), "ossie-0.2")["fidelity"]["note"]
    assert "no fidelity today" in note


def test_the_listing_shows_50_and_counts_the_rest():
    listing = sm._Listing()
    for i in range(51):
        listing.add(str(i), f"o{i}", "column", "why")
    shown = listing.shown()
    assert len(shown["objects"]) == 50 and shown["total"] == 51


# ---- the Snowflake row

def test_a_skipped_metric_is_a_measure_and_a_skipped_field_a_column(monkeypatch, rich):
    hit = [o for o in _row(rich, "snowflake-ossie")["dropped"]["objects"] if "skips it silently" in o["why"]]
    assert [o["kind"] for o in hit] == ["measure"]
    real = ossie_snowflake.to_snowflake

    def more(doc):
        out = real(doc)
        out["report"]["skipped_by_snowflake"].append({"path": "$.semantic_model[0].datasets[0].fields[0]",
                                                      "object": "first field", "kind": "field"})
        return out

    monkeypatch.setattr(ossie_snowflake, "to_snowflake", more)
    hit = [o for o in _row(sm.compatibility(_rich()), "snowflake-ossie")["dropped"]["objects"]
           if "skips it silently" in o["why"]]
    assert sorted(o["kind"] for o in hit) == ["column", "measure"]


@pytest.mark.parametrize("model", [{"datasets": [{"name": "d", "fields": [{"name": "f"}]}]}, {"datasets": None}, {}])
def test_a_path_to_a_dataset_that_isnt_there_has_no_key(model):
    assert sm._snowflake_key(model, "$.semantic_model[0].datasets[1].fields[0]") is None


# ---- redacted

def test_redacted_keeps_the_reason_on_a_dropped_object_that_isnt_a_role(rich):
    out = sm.redacted(rich)
    row = _row(out, "fabric-semantic-view")
    columns = [o for o in row["dropped"]["objects"] if o["kind"] == "column"]
    assert columns and all("withheld" not in o and "has no place for it" in o["why"] for o in columns)


# ---- parity reports changed by hand, for cases the fixture model doesn't reach

def _patched(monkeypatch, change):
    real = sp.parity_l2

    def patched(*args, **kwargs):
        report = copy.deepcopy(real(*args, **kwargs))
        change(report, kwargs.get("right_kind"))
        return report

    monkeypatch.setattr(sp, "parity_l2", patched)


def test_the_model_object_is_never_judged_even_when_a_target_lacks_it(monkeypatch):
    def lose_model(report, _kind):
        for o in report["objects"]:
            if o["kind"] == "model":
                o["ids"]["right"] = None

    _patched(monkeypatch, lose_model)
    for row in sm.compatibility(_rich())["targets"]:
        assert all(o["kind"] != "model" for o in row["dropped"]["objects"])


def test_the_view_note_counts_only_kept_measures_l2_cant_read(monkeypatch):
    kept = []

    def all_unsupported(report, kind):
        if kind == "semantic_view":
            measures = [o for o in report["objects"] if o["kind"] == "measure" and o["ids"]["right"] is not None]
            for o in measures:
                o["expression_result"] = sp.UNSUPPORTED
            kept.append(len(measures))

    _patched(monkeypatch, all_unsupported)
    notes = _row(sm.compatibility(_rich()), "fabric-semantic-view")["notes"]
    assert kept[0] > 1
    assert [n.split(" ", 1)[0] for n in notes if "show Unsupported at L2" in n] == [str(kept[0])]


def test_an_object_snowflake_lacks_is_dropped_unless_the_export_kept_it_in_the_extension(monkeypatch):
    lost = "table 'fact_sales' column 'amount'"

    def lose(report, _kind):
        for o in report["objects"]:
            if o["ids"]["left"] == lost:
                o["ids"]["right"] = None

    _patched(monkeypatch, lose)
    m = sm.compatibility(_rich())
    for target in ("ossie-0.2", "snowflake-ossie"):
        row = _row(m, target)
        assert lost in [o["object"] for o in row["dropped"]["objects"]], target
        assert lost not in [o["object"] for o in row["extension_only"]["objects"]], target
