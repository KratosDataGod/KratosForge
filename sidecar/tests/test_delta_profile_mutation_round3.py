"""Mutation pins for Delta write safety, round 3 (docs/quality/mutation-testing.md): the limits and refused
settings of each table's own choice (deletion vectors only, column mapping only), the exact sentence for each
wrong delta_ setting, saves that repair or narrow a table's settings, and the per-table policy on the store's
streamed write, its first-append retry and publish's after-commit note. Fake tables stand in for a protocol
no fixture has; the Spark fixtures (tests/fixtures/delta_spark) are written to where one does."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import CommitFailedError

from fabric_desktop import delta_profile as P
from fabric_desktop.meta import store as S
from fabric_desktop.meta.project import validate
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.service import Service

from test_delta_profile_modern import START, _frame, _row, _rows, _snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"
NAME = "gold.fact_sales"
CHOICES = ["every_reader", "deletion_vectors", "column_mapping"]


def _table(reader=1, writer=2, reader_features=None, writer_features=None, config=None):
    protocol = SimpleNamespace(min_reader_version=reader, min_writer_version=writer,
                               reader_features=reader_features, writer_features=writer_features)
    return SimpleNamespace(protocol=lambda: protocol, metadata=lambda: SimpleNamespace(configuration=config or {}))


# ---- each table choice's own profile

# A reader 3, writer 7 table as Spark makes it with the one feature the choice allows.
_WITH = {
    "deletion_vectors": dict(reader_features=["deletionVectors"], writer_features=["deletionVectors", "appendOnly"],
                             config={"delta.enableDeletionVectors": "true"}),
    "column_mapping": dict(reader_features=["columnMapping"], writer_features=["columnMapping", "invariants"],
                           config={"delta.columnMapping.mode": "name"}),
}


@pytest.mark.parametrize("choice", sorted(_WITH))
def test_a_table_choice_takes_reader_3_and_writer_7_and_refuses_one_over(choice):
    p = P.table_profile(choice)
    inside = _table(3, 7, **_WITH[choice])
    assert P.problems(inside, p) == []
    P.check_table(inside, p)  # the SQL endpoint and Direct Lake read it: no refusal
    assert P.problems(_table(4, 7, **_WITH[choice]), p) == ["reader version 4 > 3"]
    assert P.problems(_table(3, 8, **_WITH[choice]), p) == ["writer version 8 > 7"]
    with pytest.raises(P.ProfileViolation, match=rf"^Table is outside the {p.label} Delta profile: reader version 4 > 3"):
        P.check_table(_table(4, 7, **_WITH[choice]), p)


@pytest.mark.parametrize(("choice", "key", "value", "refused"), [
    ("deletion_vectors", "delta.enableDeletionVectors", "true", False),
    ("deletion_vectors", "delta.columnMapping.mode", "name", True),
    ("deletion_vectors", "delta.columnMapping.mode", "id", True),
    ("deletion_vectors", "delta.columnMapping.mode", "none", False),
    ("deletion_vectors", "delta.checkpointPolicy", "v2", True),
    ("deletion_vectors", "delta.checkpointPolicy", "classic", False),
    ("column_mapping", "delta.enableDeletionVectors", "true", True),
    ("column_mapping", "delta.enableDeletionVectors", "false", False),
    ("column_mapping", "delta.columnMapping.mode", "name", False),
    ("column_mapping", "delta.columnMapping.mode", "id", True),
    ("column_mapping", "delta.checkpointPolicy", "v2", True),
    ("every_reader", "delta.enableDeletionVectors", "true", True),
    ("every_reader", "delta.columnMapping.mode", "name", True),
    ("every_reader", "delta.columnMapping.mode", "id", True),
    ("every_reader", "delta.checkpointPolicy", "v2", True),
])
def test_each_choice_refuses_exactly_the_settings_it_doesnt_allow(choice, key, value, refused):
    # A setting on its own, with no feature listed: the setting is all that shows it.
    p = P.table_profile(choice)
    assert P.problems(_table(config={key: value}), p) == ([f"{key}={value}"] if refused else [])


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "tables", "dev")


def _place(store: LocalStore, name: str) -> Path:
    folder = Path(store.uri("LH_Bronze", "dbo", name))
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / name, folder)
    return folder


def _choose(store: LocalStore, name: str, policy: dict | tuple) -> None:
    store.delta_policies = {("LH_Bronze", "dbo", name): policy if isinstance(policy, tuple) else P.table_policy(policy)}


def test_a_legacy_column_mapped_table_is_refused_by_the_deletion_vectors_choice_and_left_as_it_was(store):
    # cm_legacy is reader 2, writer 5: it lists no features, so only its column mapping setting shows them.
    folder = _place(store, "cm_legacy")
    _choose(store, "cm_legacy", {"delta_features": "deletion_vectors"})
    before = _snapshot(folder)
    says = ("^Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses "
            "column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read$")
    with pytest.raises(P.ProfileViolation, match=says):
        store.write("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [100]), mode="append")
    with pytest.raises(P.ProfileViolation, match=says):
        store.upsert("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [0, 100]), on=["id"])
    assert _snapshot(folder) == before
    assert _rows(folder) == sorted(START["cm_legacy"])

    _choose(store, "cm_legacy", {"delta_features": "column_mapping"})
    store.write("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [100]), mode="append")
    assert _rows(folder) == sorted(START["cm_legacy"] + [tuple(_row("cm_legacy", 100).values())])


def test_the_v2_checkpoint_row_says_only_databricks_is_known_to_read_it():
    others = [P.READERS[r] for r in P.READERS if r != "databricks"]
    assert P.why_features([P.V2_CHECKPOINTS], list(P.READERS)) == [
        (f"It uses V2 checkpoints, and no source we found says whether {', '.join(others[:-1])} and {others[-1]} "
        "can read it (user reports; Microsoft's documentation doesn't say).")]


# ---- the exact sentence for each wrong delta_ setting

_LIST = 'delta_readers is a list of reader names, like delta_readers = ["sql_endpoint", "direct_lake"].'
_UNKNOWN_CHOICE = ("delta_features is ['deletion_vectors'], which isn't a choice KratosForge knows. Use one of "
                   "every_reader, deletion_vectors, column_mapping, or leave it out for the default.")


@pytest.mark.parametrize(("spec", "said"), [
    # One sentence for a setting that isn't a list: its characters aren't read as reader names too.
    ({"delta_readers": "sql_endpoint"}, [_LIST]),
    ({"delta_accepted": "pipelines"}, [_LIST.replace("delta_readers", "delta_accepted")]),
    ({"delta_readers": [["sql_endpoint"]]}, [_LIST]),
    ({"delta_readers": ["sql_endpoint", 3]}, [_LIST]),
    ({"delta_features": ["deletion_vectors"]}, [_UNKNOWN_CHOICE]),
    ({"delta_readers": ["excel", "word"]},
     ["delta_readers names 'excel', which isn't a reader KratosForge knows. Use any of " + ", ".join(P.READERS) + "."]),
    ({"delta_readers": ["sql_endpoint"], "delta_accepted": ["pipelines", "kql"]},
     ["delta_accepted names pipelines, which isn't one of this table's delta_readers. Take it out."]),
    ({"delta_features": "deletion_vectors", "delta_readers": ["pipelines", "eventstreams"]},
     [("Its Delta table features would break Fabric pipelines, one of its readers, and nobody confirmed that. Choose "
      "the setting again on the Environments tab and type the table's name to allow it.")]),
])
def test_a_wrong_setting_gets_exactly_one_sentence_naming_the_first_wrong_value(spec, said):
    assert P.choice_problems(spec) == said
    assert P.table_policy(spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)


def test_an_empty_accepted_list_is_no_problem():
    spec = {"delta_accepted": []}
    assert P.choice_problems(spec) == []
    assert P.table_policy(spec) == (P.MODERN, P.DEFAULT_READERS)


def test_a_save_with_two_wrong_values_names_the_first():
    with pytest.raises(ValueError) as refused:
        P.confirm_choice(NAME, {}, {"delta_features": "fast", "delta_readers": ["excel"]}, None)
    assert str(refused.value) == (
        "gold.fact_sales wasn't saved, because delta_features is 'fast', which isn't a choice KratosForge knows. "
        "Use one of every_reader, deletion_vectors, column_mapping, or leave it out for the default.")


# ---- saves that repair or narrow a table's settings


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def _file(svc) -> Path:
    return svc._project().entities[NAME].path


def _hand_edit(svc, lines: str) -> str:
    path = _file(svc)
    path.write_text(path.read_text(encoding="utf-8").replace("owner = ", lines + "\nowner = ", 1), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def test_choosing_the_default_readers_repairs_an_empty_reader_list(svc):
    _hand_edit(svc, "delta_readers = []")
    assert P.table_policy(svc._project().entities[NAME].spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)
    svc.meta_set_delta_features(NAME, None, list(P.DEFAULT_READERS))
    assert "delta_" not in _file(svc).read_text(encoding="utf-8")
    project = svc._project()
    assert not [x for x in validate(project) if "delta" in x.lower()]
    assert P.table_policy(project.entities[NAME].spec) == (P.MODERN, P.DEFAULT_READERS)


def test_an_unknown_reader_in_the_file_is_replaced_without_asking(svc):
    _hand_edit(svc, 'delta_readers = ["excel"]')
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint"])
    spec = svc._project().entities[NAME].spec
    assert (spec["delta_features"], spec["delta_readers"], "delta_accepted" in spec) == (
        "deletion_vectors", ["sql_endpoint"], False)
    assert P.table_policy(spec) == (P.DELETION_VECTORS_ONLY, ("sql_endpoint",))


def test_writing_delta_accepted_by_hand_on_an_unconfirmed_break_still_needs_the_name_typed(svc):
    # The file was changed outside the app to allow deletion vectors for a pipelines reader, unconfirmed,
    # so it is written as every reader reads it. Adding delta_accepted in the entity editor isn't a confirm.
    text = _hand_edit(svc, 'delta_features = "deletion_vectors"\ndelta_readers = ["sql_endpoint", "pipelines"]')
    with pytest.raises(P.ConfirmNeeded, match="This choice allows deletion vectors, which Fabric pipelines can't read"):
        svc.meta_save_entity(NAME, text.replace("owner = ", 'delta_accepted = ["pipelines"]\nowner = ', 1))
    assert _file(svc).read_text(encoding="utf-8") == text
    assert P.table_policy(svc._project().entities[NAME].spec) == (P.CONSERVATIVE, P.DEFAULT_READERS)


def test_a_new_choice_after_a_confirmed_one_takes_its_delta_accepted_line_out(svc):
    svc.meta_set_delta_features(NAME, "deletion_vectors", ["sql_endpoint", "pipelines"], confirm_name=NAME)
    assert 'delta_accepted = ["pipelines"]' in _file(svc).read_text(encoding="utf-8")
    svc.meta_set_delta_features(NAME, "every_reader", ["sql_endpoint", "pipelines"])
    text = _file(svc).read_text(encoding="utf-8")
    assert "delta_accepted" not in text
    assert [x for x in text.splitlines() if x.startswith("delta_")] == [
        'delta_features = "every_reader"', 'delta_readers = ["sql_endpoint", "pipelines"]']
    assert P.table_policy(svc._project().entities[NAME].spec) == (P.CONSERVATIVE, ("sql_endpoint", "pipelines"))


# ---- the per-table policy on every write path


@pytest.mark.parametrize(("policy", "says"), [
    ({"delta_features": "every_reader"}, "^Table is outside the Fabric-safe Delta profile"),
    ((P.MODERN, ("sql_endpoint", "pipelines")), "^Nothing was written.*Fabric pipelines can't read"),
])
def test_a_streamed_write_is_checked_with_the_tables_own_policy(store, policy, says):
    folder = _place(store, "dv")
    _choose(store, "dv", policy)
    before = _snapshot(folder)
    rows = _frame("dv", [7]).to_arrow().to_reader()
    with pytest.raises(P.ProfileViolation, match=says):
        store.write_stream("LH_Bronze", "dbo", "dv", rows, read_version=before[0])
    assert _snapshot(folder) == before
    assert _rows(folder) == sorted(START["dv"])


def test_a_first_append_that_lost_to_a_table_its_choice_refuses_is_not_made_again(store, monkeypatch):
    # Another writer created the table first, with deletion vectors; this table's choice is every_reader.
    folder = Path(store.uri("LH_Bronze", "dbo", "dv"))
    _choose(store, "dv", {"delta_features": "every_reader"})
    real, calls = S.write_deltalake, []

    def lose_to_spark(target, data, **kw):
        calls.append(target)
        if len(calls) == 1:
            shutil.copytree(FIXTURES / "dv", folder, dirs_exist_ok=True)
            raise CommitFailedError("Protocol changed since last commit")
        return real(target, data, **kw)

    monkeypatch.setattr(S, "write_deltalake", lose_to_spark)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    with pytest.raises(P.ProfileViolation, match="^Table is outside the Fabric-safe Delta profile"):
        store.write("LH_Bronze", "dbo", "dv", _frame("dv", [100]), mode="append")
    assert len(calls) == 1
    assert DeltaTable(str(folder)).version() == DeltaTable(str(FIXTURES / "dv")).version()
    assert _rows(folder) == sorted(START["dv"])


def test_publish_notes_a_table_its_write_took_outside_the_tables_own_profile(tmp_path, caplog):
    from fabric_desktop import publish
    from test_ea_write_integrity import Ref
    # A timestamp without a time zone makes delta-rs create the table with the timestampNtz feature.
    data = pa.table({"id": [1], "at": pa.array([dt.datetime(2026, 1, 1)], pa.timestamp("us"))})
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.delta_profile"):
        table = publish.publish(data, Ref((tmp_path / "t").as_posix()), "append", "tok",
                                within=P.table_profile("every_reader"))
    assert table.version() == 0
    assert [r.getMessage() for r in caplog.records] == [
        ("The write committed, but the table is now outside the Fabric-safe Delta profile: reader version 3 > 1; "
        "writer version 7 > 2; reader features ['timestampNtz']; writer features ['timestampNtz']")]
