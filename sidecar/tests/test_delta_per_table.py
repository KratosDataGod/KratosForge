"""Each metadata project table's own Delta table features (delta_features, Preview).

An entity file may say which Delta features a publish leaves on its table: "every_reader" (the
conservative profile), "deletion_vectors", "column_mapping", or nothing for the default (the modern
profile). It may name the table's readers (delta_readers). A choice that breaks one of them is saved
only with the table's full name typed to allow it, and the readers that break are kept in
delta_accepted. Every write to the table is checked with its profile and readers. A refused write
leaves the version, the Delta log and every file as they were, on real tables Spark made
(tests/fixtures/delta_spark), and an allowed one is checked row by row.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_profile as P
from fabric_desktop.meta import Engine, Project
from fabric_desktop.meta.project import validate, with_delta_choice
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.service import Service

from test_delta_profile_modern import COLUMNS, START, _frame, _last_operation, _row, _rows, _snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"
NAME = "gold.fact_sales"


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def _file(svc) -> Path:
    return svc._project().entities[NAME].path


def _entity(svc, name: str = NAME) -> dict:
    return next(e for e in svc.meta_project()["entities"] if e["name"] == name)


# ---- the choices


def test_each_choice_writes_with_its_profile_and_no_choice_is_the_modern_default():
    assert P.table_profile(None) is P.DEFAULT_PROFILE is P.MODERN
    assert P.table_profile("every_reader") is P.CONSERVATIVE
    assert P.allowed_features(P.table_profile("every_reader")) == []
    assert P.allowed_features(P.table_profile("deletion_vectors")) == [P.DELETION_VECTORS]
    assert P.allowed_features(P.table_profile("column_mapping")) == [P.CM_NAME]
    assert P.allowed_features(P.MODERN) == [P.DELETION_VECTORS, P.CM_NAME]
    with pytest.raises(ValueError, match="isn't a choice KratosForge knows"):
        P.table_profile("fast")


@pytest.mark.parametrize("choice", [None, *P.TABLE_CHOICES])
def test_no_choice_breaks_the_sql_endpoint_or_direct_lake(choice):
    assert P.choice_reasons(choice, P.DEFAULT_READERS) == []
    assert P.broken_readers(choice, P.DEFAULT_READERS) == []


def test_a_choice_that_breaks_a_reader_says_which_reader_which_feature_and_the_source():
    assert P.choice_reasons("deletion_vectors", ["sql_endpoint", "pipelines"]) == [
        ("This choice allows deletion vectors, which Fabric pipelines can't read "
        "(Microsoft's Delta Lake interoperability page).")]
    assert P.broken_readers(None, ["python_notebooks", "direct_lake"]) == ["python_notebooks"]
    assert P.choice_reasons("column_mapping", ["snowflake"]) == [
        ("This choice allows column mapping by name, and no source we found says whether Snowflake can read it "
        "(Microsoft's Delta Lake interoperability page).")]
    assert P.choice_reasons("every_reader", list(P.READERS)) == []


@pytest.mark.parametrize("spec, says", [
    ({"delta_features": "fast"}, "delta_features is 'fast', which isn't a choice KratosForge knows"),
    ({"delta_features": 3}, "delta_features is 3, which isn't a choice"),
    ({"delta_readers": "sql_endpoint"}, "delta_readers is a list of reader names"),
    ({"delta_readers": ["excel"]}, "delta_readers names 'excel', which isn't a reader KratosForge knows"),
    ({"delta_readers": []}, "delta_readers names no reader"),
    ({"delta_accepted": ["excel"]}, "delta_accepted names 'excel'"),
    ({"delta_accepted": ["pipelines"]}, "delta_accepted names pipelines, which isn't one of this table's delta_readers"),
    ({"delta_readers": ["pipelines"]}, "would break Fabric pipelines, one of its readers, and nobody confirmed that"),
])
def test_a_wrong_setting_is_named_in_a_plain_sentence(spec, says):
    problems = P.choice_problems(spec)
    assert problems and says in problems[0]
    assert P.table_policy(spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)  # written as every reader reads it


def test_a_confirmed_break_is_not_checked_again_at_publish():
    spec = {"delta_features": "deletion_vectors", "delta_readers": ["sql_endpoint", "pipelines"],
            "delta_accepted": ["pipelines"]}
    assert P.choice_problems(spec) == []
    assert P.table_policy(spec) == (P.DELETION_VECTORS_ONLY, ("sql_endpoint",))


# ---- loading and saving the entity file


def test_a_file_from_before_the_setting_loads_unchanged_with_the_default(svc):
    path = _file(svc)
    text = path.read_text(encoding="utf-8")
    project = svc._project()
    e = project.entities[NAME]
    assert "delta_" not in text
    assert P.table_policy(e.spec) == (P.MODERN, P.DEFAULT_READERS)
    assert not [x for x in validate(project) if "delta" in x.lower()]
    delta = _entity(svc)["delta"]
    assert delta["features"] is None and delta["default"] == "modern"
    assert delta["readers"] == list(P.DEFAULT_READERS)
    assert delta["reader_names"] == ["Fabric's SQL analytics endpoint", "Power BI Direct Lake"]
    assert all(r == [] for r in delta["reasons"].values())
    assert path.read_text(encoding="utf-8") == text


def test_a_choice_that_breaks_no_reader_saves_without_a_confirm_and_keeps_every_other_line(svc):
    path = _file(svc)
    before = path.read_text(encoding="utf-8")
    svc.meta_set_delta_features(NAME, "every_reader")
    after = path.read_text(encoding="utf-8")
    assert [x for x in after.splitlines() if x not in before.splitlines()] == ['delta_features = "every_reader"']
    assert [x for x in before.splitlines() if x not in after.splitlines()] == []
    assert _entity(svc)["delta"]["features"] == "every_reader"
    assert P.table_policy(svc._project().entities[NAME].spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)
    svc.meta_set_delta_features(NAME, None)  # back to the default: the line goes
    assert path.read_text(encoding="utf-8") == before


def test_an_unknown_choice_or_reader_is_refused_and_the_file_is_unchanged(svc):
    path = _file(svc)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=r"^gold.fact_sales wasn't saved, because delta_features is 'fast'"):
        svc.meta_set_delta_features(NAME, "fast")
    with pytest.raises(ValueError, match="delta_readers names 'excel', which isn't a reader KratosForge knows"):
        svc.meta_set_delta_features(NAME, "every_reader", ["excel"])
    with pytest.raises(ValueError, match="isn't an entity in this project"):
        svc.meta_set_delta_features("gold.nothing", "every_reader")
    assert path.read_text(encoding="utf-8") == before


def test_a_choice_that_breaks_a_reader_needs_the_tables_name_typed(svc):
    path = _file(svc)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(P.ConfirmNeeded) as asked:
        svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"])
    assert str(asked.value) == (
        "gold.fact_sales wasn't saved, because its Delta table features would break one of its readers. "
        "This choice allows deletion vectors, which Fabric pipelines can't read (Microsoft's Delta Lake "
        "interoperability page). Type gold.fact_sales to allow it.")
    assert asked.value.name == NAME and len(asked.value.reasons) == 1
    assert path.read_text(encoding="utf-8") == before

    with pytest.raises(P.ConfirmNeeded, match="the name typed to allow it was gold.fact, not gold.fact_sales"):
        svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"], confirm_name="gold.fact")
    with pytest.raises(P.ConfirmNeeded):
        svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"], confirm_name="GOLD.FACT_SALES")
    assert path.read_text(encoding="utf-8") == before

    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"], confirm_name=NAME)
    spec = svc._project().entities[NAME].spec
    assert (spec["delta_features"], spec["delta_readers"], spec["delta_accepted"]) == (
        "deletion_vectors", ["sql_endpoint", "pipelines"], ["pipelines"])
    assert not [x for x in validate(svc._project()) if "delta" in x.lower()]
    assert P.table_policy(spec) == (P.DELETION_VECTORS_ONLY, ("sql_endpoint",))
    # Choosing it again changes nothing, so it asks nothing.
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"])


def test_the_default_breaks_a_python_notebook_reader_so_it_asks_too(svc):
    with pytest.raises(P.ConfirmNeeded, match="This choice allows deletion vectors, which Fabric Python notebooks "
                                              "can't read.*This choice allows column mapping by name"):
        svc.meta_set_delta_features(NAME, None, ["python_notebooks"])
    svc.meta_set_delta_features(NAME, "every_reader", ["python_notebooks"])  # every reader reads it: no confirm


def test_taking_out_a_reader_the_choice_breaks_still_needs_the_name_typed(svc):
    path = _file(svc)
    svc.meta_set_delta_features(NAME, "every_reader", ["sql_endpoint", "pipelines"])
    before = path.read_text(encoding="utf-8")
    # One save that both allows deletion vectors and drops the reader they break.
    with pytest.raises(P.ConfirmNeeded, match="deletion vectors, which Fabric pipelines can't read"):
        svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint"])
    assert path.read_text(encoding="utf-8") == before
    # The entity editor is held to the same rule.
    narrowed = before.replace('delta_features = "every_reader"', 'delta_features = "deletion_vectors"').replace(
        '["sql_endpoint", "pipelines"]', '["sql_endpoint"]')
    assert narrowed != before
    with pytest.raises(P.ConfirmNeeded, match="Fabric pipelines can't read"):
        svc.meta_save_entity(NAME, narrowed)
    assert path.read_text(encoding="utf-8") == before
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint"], confirm_name=NAME)
    spec = svc._project().entities[NAME].spec
    assert (spec["delta_features"], spec["delta_readers"], spec.get("delta_accepted") or []) == (
        "deletion_vectors", ["sql_endpoint"], [])


def test_dropping_a_reader_already_confirmed_for_the_choice_asks_nothing_more(svc):
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"], confirm_name=NAME)
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint"])
    assert svc._project().entities[NAME].spec["delta_readers"] == ["sql_endpoint"]


def test_dropping_a_reader_a_new_choice_breaks_asks_even_if_an_old_choice_was_confirmed(svc):
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "eventstreams"], confirm_name=NAME)
    before = _file(svc).read_text(encoding="utf-8")
    with pytest.raises(P.ConfirmNeeded, match="column mapping by name, which Fabric eventstreams can't read"):
        svc.meta_set_delta_features(NAME, "column_mapping", ["sql_endpoint"])
    assert _file(svc).read_text(encoding="utf-8") == before


def test_dropping_a_reader_no_choice_breaks_asks_nothing(svc):
    svc.meta_set_delta_features(NAME, "every_reader", ["sql_endpoint", "pipelines"])
    svc.meta_set_delta_features(NAME, "every_reader", ["sql_endpoint"])
    assert svc._project().entities[NAME].spec["delta_readers"] == ["sql_endpoint"]


def test_a_reader_no_source_says_reads_the_choice_asks_too(svc):
    # Snowflake's column-mapping cell is unknown, not can't read: it still needs the typed name.
    before = _file(svc).read_text(encoding="utf-8")
    with pytest.raises(P.ConfirmNeeded, match="Snowflake"):
        svc.meta_set_delta_features(NAME, "column_mapping", ["sql_endpoint", "snowflake"])
    assert _file(svc).read_text(encoding="utf-8") == before
    assert P.broken_readers("column_mapping", ["sql_endpoint", "snowflake"]) == ["snowflake"]


def test_the_entity_editor_save_is_held_to_the_same_rules(svc):
    path = _file(svc)
    before = path.read_text(encoding="utf-8")
    broken = before.replace('owner = ', 'delta_features = "column_mapping"\ndelta_readers = ["eventstreams"]\nowner = ', 1)
    with pytest.raises(P.ConfirmNeeded, match="column mapping by name, which Fabric eventstreams can't read"):
        svc.meta_save_entity(NAME, broken)
    # Writing delta_accepted by hand is not a confirm.
    with pytest.raises(P.ConfirmNeeded):
        svc.meta_save_entity(NAME, broken.replace("owner = ", 'delta_accepted = ["eventstreams"]\nowner = ', 1))
    with pytest.raises(ValueError, match="wasn't saved, because delta_features is 'fast'"):
        svc.meta_save_entity(NAME, before.replace('owner = ', 'delta_features = "fast"\nowner = ', 1))
    assert path.read_text(encoding="utf-8") == before
    svc.meta_save_entity(NAME, broken, confirm_name=NAME)
    assert svc._project().entities[NAME].spec["delta_accepted"] == ["eventstreams"]
    # Another edit leaves the confirmed choice alone and asks nothing.
    saved = path.read_text(encoding="utf-8")
    svc.meta_save_entity(NAME, saved.replace('mode = "incremental"', 'mode = "full"'))
    assert svc._project().entities[NAME].spec["delta_accepted"] == ["eventstreams"]


def test_a_file_changed_outside_the_app_with_an_unconfirmed_break_fails_the_save_gate(svc):
    path = _file(svc)
    path.write_text(path.read_text(encoding="utf-8").replace('owner = ', 'delta_readers = ["pipelines"]\nowner = ', 1),
                    encoding="utf-8")
    project = svc._project()
    assert [x for x in validate(project) if x.startswith(NAME + ": Its Delta table features would break Fabric pipelines")]
    assert P.table_policy(project.entities[NAME].spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)


def test_a_reader_list_over_several_lines_is_refused_rather_than_rewritten():
    text = 'name = "gold.x"\ndelta_readers = [\n  "sql_endpoint",\n]\n\n[load]\nmode = "full"\n'
    with pytest.raises(ValueError, match="Put each delta_ setting on one line"):
        with_delta_choice(text, "every_reader", None, [])
    assert with_delta_choice('name = "gold.x"\n\n[load]\nmode = "full"\n', "every_reader", None, []) == \
        'name = "gold.x"\ndelta_features = "every_reader"\n\n[load]\nmode = "full"\n'


# ---- publishing with the table's choice


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "tables", "dev")


def _place(store: LocalStore, name: str) -> Path:
    folder = Path(store.uri("LH_Bronze", "dbo", name))
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / name, folder)
    return folder


def _choose(store: LocalStore, name: str, spec: dict | tuple) -> None:
    """The policy for an entity file's settings ``spec``, or ``spec`` itself when it is one."""
    store.delta_policies = {("LH_Bronze", "dbo", name): spec if isinstance(spec, tuple) else P.table_policy(spec)}


@pytest.mark.parametrize("name, spec", [
    ("dv", {"delta_features": "deletion_vectors"}),
    ("cm_name", {"delta_features": "column_mapping"}),
    ("dv_cm", {}),
    ("dv", {"delta_features": "deletion_vectors", "delta_readers": ["sql_endpoint", "pipelines"],
            "delta_accepted": ["pipelines"]}),
])
def test_a_table_its_choice_allows_is_written_and_reads_back_right(store, name, spec):
    folder = _place(store, name)
    _choose(store, name, spec)
    version = DeltaTable(str(folder)).version()
    store.write("LH_Bronze", "dbo", name, _frame(name, [100, 101]), mode="append")
    assert DeltaTable(str(folder)).version() == version + 1
    assert _last_operation(folder) == "WRITE"
    assert _rows(folder) == sorted(START[name] + [tuple(_row(name, i).values()) for i in (100, 101)])


@pytest.mark.parametrize("name, spec, says", [
    ("dv", {"delta_features": "every_reader"}, "outside the Fabric-safe Delta profile"),
    ("cm_name", {"delta_features": "every_reader"}, "outside the Fabric-safe Delta profile"),
    ("cm_name", {"delta_features": "deletion_vectors"}, "outside the deletion vectors only Delta profile"),
    ("dv", {"delta_features": "column_mapping"}, "outside the column mapping only Delta profile"),
    ("dv_cm", {"delta_features": "deletion_vectors"}, "outside the deletion vectors only Delta profile"),
    # An unconfirmed break in the file is written as every reader reads it.
    ("dv", {"delta_readers": ["sql_endpoint", "pipelines"]}, "outside the Fabric-safe Delta profile"),
    # The readers are checked too, after the profile.
    ("dv", (P.MODERN, ("sql_endpoint", "pipelines")), "^Nothing was written.*Fabric pipelines can't read"),
])
def test_a_table_its_choice_refuses_is_left_exactly_as_it_was(store, name, spec, says):
    folder = _place(store, name)
    _choose(store, name, spec)
    before = _snapshot(folder)
    with pytest.raises(P.ProfileViolation, match=says):
        store.write("LH_Bronze", "dbo", name, _frame(name, [100]), mode="append")
    with pytest.raises(P.ProfileViolation, match=says):
        store.write("LH_Bronze", "dbo", name, _frame(name, [7]), mode="overwrite")
    with pytest.raises(P.ProfileViolation, match=says):
        store.upsert("LH_Bronze", "dbo", name, _frame(name, [0, 100]), on=["id"])
    with pytest.raises(P.ProfileViolation, match=says):
        store.merge_insert("LH_Bronze", "dbo", name, _frame(name, [100]), on=["id"])
    assert _snapshot(folder) == before
    assert _rows(folder) == sorted(START[name])


def test_an_upsert_its_choice_allows_changes_one_row_and_adds_one(store):
    folder = _place(store, "dv")
    _choose(store, "dv", {"delta_features": "deletion_vectors"})
    version = DeltaTable(str(folder)).version()
    change = _frame("dv", [0, 100])
    store.upsert("LH_Bronze", "dbo", "dv", change, on=["id"])
    assert DeltaTable(str(folder)).version() == version + 1
    assert _last_operation(folder) == "MERGE"
    assert _rows(folder) == sorted([r for r in START["dv"] if r[0] != 0] + [tuple(change.row(0)), tuple(change.row(1))])


def test_another_table_keeps_the_default(store):
    folder = _place(store, "dv")
    store.delta_policies = {("LH_Bronze", "dbo", "other"): P.table_policy({"delta_features": "every_reader"})}
    store.write("LH_Bronze", "dbo", "dv", _frame("dv", [100]), mode="append")
    assert _rows(folder) == sorted(START["dv"] + [tuple(_row("dv", 100).values())])


def test_the_engine_checks_every_entity_table_with_its_own_choice(svc, tmp_path):
    svc.meta_set_delta_features("bronze.orders", "every_reader")
    project = Project(svc._project().root)
    store = LocalStore(tmp_path / "engine", "dev")
    Engine(project, store)
    assert store.delta_policy("LH_Bronze", "dbo", "orders") == (P.CONSERVATIVE, P.DEFAULT_READERS)
    assert store.delta_policy("LH_Gold", "dbo", "fact_sales") == (P.MODERN, P.DEFAULT_READERS)
    assert store.delta_policy("LH_Meta", "meta", "run_log") == (None, None)


def test_a_run_into_a_table_its_choice_refuses_fails_and_leaves_the_table_unchanged(svc):
    svc.meta_set_delta_features("bronze.orders", "every_reader")
    project = svc._project()
    store = svc._store(project, "dev", create=True)
    folder = Path(store.uri("LH_Bronze", "dbo", "orders"))
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / "dv", folder)  # a table Spark made with deletion vectors
    before = _snapshot(folder)
    results = svc.meta_run(["bronze.orders"], env="dev")
    run = next(r for r in results if r["entity"] == "bronze.orders")
    assert run["status"] != "ok"
    assert "Fabric-safe" in run["message"]
    assert _snapshot(folder) == before
    assert _rows(folder) == sorted(START["dv"])
    assert COLUMNS["dv"] == ["id", "amount", "label", "p"]


def test_publish_to_onelake_writes_with_the_profile_and_readers_it_is_given(tmp_path):
    from fabric_desktop import publish
    from test_ea_write_integrity import Ref
    folder = tmp_path / "dv"
    shutil.copytree(FIXTURES / "dv", folder)
    ref = Ref(folder.as_posix())
    before = _snapshot(folder)
    data = _frame("dv", [100]).to_arrow()
    with pytest.raises(P.ProfileViolation, match="outside the Fabric-safe Delta profile"):
        publish.publish(data, ref, "append", "tok", within=P.table_profile("every_reader"))
    with pytest.raises(P.ProfileViolation, match="^Nothing was written.*Fabric Python notebooks"):
        publish.publish(data, ref, "append", "tok", within=P.MODERN, readers=["python_notebooks"])
    assert _snapshot(folder) == before
    publish.publish(data, ref, "append", "tok", within=P.table_profile("deletion_vectors"))
    assert DeltaTable(str(folder)).version() == before[0] + 1
    assert _last_operation(folder) == "WRITE"
    assert _rows(folder) == sorted(START["dv"] + [tuple(_row("dv", 100).values())])
