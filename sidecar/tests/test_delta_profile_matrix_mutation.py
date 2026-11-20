"""Mutation pins for delta_profile's reader matrix and profiles (docs/quality/mutation-testing.md): every
cell kind's why() sentence with its source, the features a table uses from its protocol and settings,
problems() and check_table() at each boundary, and the profile and reader names. Fake tables stand in
for the protocol and settings; open_target and note_after_write run on copies of the Spark fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_profile as P

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"

SRC_INTEROP = "Microsoft's Delta Lake interoperability page"
SRC_CM_ID = "Microsoft's Delta Lake interoperability page and the SQL analytics endpoint's limitations"
SRC_V2 = "user reports; Microsoft's documentation doesn't say"

# The setting that turns on each matrix feature.
ON = {
    P.DELETION_VECTORS: {"delta.enableDeletionVectors": "true"},
    P.CM_NAME: {"delta.columnMapping.mode": "name"},
    P.CM_ID: {"delta.columnMapping.mode": "id"},
    P.V2_CHECKPOINTS: {"delta.checkpointPolicy": "v2"},
}
SOURCE = {P.DELETION_VECTORS: SRC_INTEROP, P.CM_NAME: SRC_INTEROP, P.CM_ID: SRC_CM_ID, P.V2_CHECKPOINTS: SRC_V2}

NOTE_DV = "deletion vectors, which Fabric Python notebooks, pipelines and eventstreams can't read"
NOTE_CM_ID = ("column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, "
              "KQL databases, Python notebooks, pipelines and eventstreams can't read")
NOTE_V2 = ("V2 checkpoints, which users report Fabric's SQL analytics endpoint refusing "
           "(Microsoft's documentation doesn't say)")


def _table(reader=1, writer=2, reader_features=None, writer_features=None, config=None):
    protocol = SimpleNamespace(min_reader_version=reader, min_writer_version=writer,
                               reader_features=reader_features, writer_features=writer_features)
    return SimpleNamespace(protocol=lambda: protocol, metadata=lambda: SimpleNamespace(configuration=config))


def _cant(feature, *names):
    return f"It uses {feature}, which {names_of(names)} can't read ({SOURCE[feature]})."


def _unsure(feature, *names):
    return f"It uses {feature}, and no source we found says whether {names_of(names)} can read it ({SOURCE[feature]})."


def names_of(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# ---- The names the sentences use


def test_the_reader_names_and_sources_are_the_words_people_read():
    assert P.READERS == {
        "sql_endpoint": "Fabric's SQL analytics endpoint", "direct_lake": "Power BI Direct Lake",
        "fabric_spark": "Fabric Spark", "duckdb": "DuckDB", "databricks": "Databricks", "snowflake": "Snowflake",
        "python_notebooks": "Fabric Python notebooks", "pipelines": "Fabric pipelines",
        "eventstreams": "Fabric eventstreams", "lakehouse_explorer": "the lakehouse explorer",
        "dataflows": "Dataflows Gen2", "kql": "KQL databases"}
    assert P.SOURCES == SOURCE
    assert (P.READS, P.CANT, P.UNKNOWN) == ("reads", "can't read", "unknown")
    assert (P.DELETION_VECTORS, P.CM_NAME, P.CM_ID, P.V2_CHECKPOINTS) == (
        "deletion vectors", "column mapping by name", "column mapping by ID", "V2 checkpoints")
    assert P.DEFAULT_READERS == ("sql_endpoint", "direct_lake")


# ---- why(): one sentence per cell kind


CELLS = [(f, r) for f in P.MATRIX for r in P.READERS]


@pytest.mark.parametrize(("feature", "reader"), CELLS, ids=[f"{f}-{r}" for f, r in CELLS])
def test_each_cell_gives_its_own_sentence_with_its_source(feature, reader):
    got = P.why(_table(config=ON[feature]), [reader])
    kind = P.MATRIX[feature][reader]
    if kind == P.READS:
        assert got == []
    elif kind == P.CANT:
        assert got == [_cant(feature, P.READERS[reader])]
    else:
        assert kind == P.UNKNOWN
        assert got == [_unsure(feature, P.READERS[reader])]


def test_a_reader_that_reads_everything_gives_no_reason():
    every = _table(config={**ON[P.DELETION_VECTORS], **ON[P.CM_ID], **ON[P.V2_CHECKPOINTS]})
    assert P.why(every, ["databricks"]) == []
    assert P.why(_table(config={}), list(P.READERS)) == []
    assert P.why(every, []) == []


def test_two_and_three_readers_are_named_with_commas_and_and():
    cm_id = _table(config=ON[P.CM_ID])
    assert P.why(cm_id, ["sql_endpoint", "kql"]) == [
        ("It uses column mapping by ID, which Fabric's SQL analytics endpoint and KQL databases can't read "
        f"({SRC_CM_ID}).")]
    assert P.why(cm_id, ["kql", "sql_endpoint", "dataflows"]) == [
        ("It uses column mapping by ID, which KQL databases, Fabric's SQL analytics endpoint and Dataflows Gen2 "
        f"can't read ({SRC_CM_ID}).")]
    assert P.why(cm_id, ["snowflake"]) == [
        ("It uses column mapping by ID, and no source we found says whether Snowflake can read it "
        f"({SRC_CM_ID}).")]


def test_a_feature_with_both_kinds_says_cant_then_unknown():
    cm_name = _table(config=ON[P.CM_NAME])
    readers = ["snowflake", "pipelines", "sql_endpoint", "python_notebooks"]
    assert P.why(cm_name, readers) == [
        _cant(P.CM_NAME, "Fabric pipelines", "Fabric Python notebooks"),
        _unsure(P.CM_NAME, "Snowflake")]


def test_several_features_follow_the_matrix_order_and_readers_follow_the_order_given():
    table = _table(config={"delta.checkpointPolicy": "v2", "delta.columnMapping.mode": "id",
                           "delta.enableDeletionVectors": "true"})
    readers = ["eventstreams", "duckdb", "sql_endpoint"]
    want = [
        _cant(P.DELETION_VECTORS, "Fabric eventstreams"),
        _cant(P.CM_ID, "Fabric eventstreams", "Fabric's SQL analytics endpoint"),
        _unsure(P.V2_CHECKPOINTS, "Fabric eventstreams", "DuckDB", "Fabric's SQL analytics endpoint"),
    ]
    assert P.why(table, readers) == want
    assert P.why(table, iter(readers)) == want  # any iterable, read once
    assert P.why(table, tuple(readers)) == want


def test_a_reader_named_twice_is_named_once():
    table = _table(config={**ON[P.DELETION_VECTORS], **ON[P.V2_CHECKPOINTS]})
    once = P.why(table, ["pipelines", "duckdb"])
    assert P.why(table, ["pipelines", "duckdb", "pipelines", "duckdb"]) == once
    assert once == [_cant(P.DELETION_VECTORS, "Fabric pipelines"),
                    _unsure(P.V2_CHECKPOINTS, "Fabric pipelines", "DuckDB")]


def test_the_default_readers_are_the_sql_endpoint_and_direct_lake():
    v2 = _table(config=ON[P.V2_CHECKPOINTS])
    assert P.why(v2) == [_unsure(P.V2_CHECKPOINTS, "Fabric's SQL analytics endpoint", "Power BI Direct Lake")]
    assert P.why(_table(config=ON[P.CM_ID])) == [_cant(P.CM_ID, "Fabric's SQL analytics endpoint")]


@pytest.mark.parametrize("name", ["SQL_ENDPOINT", "Sql_Endpoint", " sql_endpoint", "sql_endpoint ", "sql endpoint",
                                  "", "spark"])
def test_a_reader_name_must_be_exact(name):
    with pytest.raises(ValueError) as err:
        P.why(_table(config={}), ["duckdb", name])
    assert str(err.value) == f"Unknown Delta reader {name!r}. Use one of {', '.join(P.READERS)}."


def test_the_first_unknown_reader_is_the_one_named_even_on_a_table_with_no_features():
    with pytest.raises(ValueError, match=r"^Unknown Delta reader 'a'\. Use one of sql_endpoint, direct_lake, "):
        P.why(_table(config={}), ["kql", "a", "b"])


# ---- features_used()


@pytest.mark.parametrize(("kw", "want"), [
    ({"config": {"delta.enableDeletionVectors": "true"}}, [P.DELETION_VECTORS]),
    ({"config": {"delta.enableDeletionVectors": "TRUE"}}, [P.DELETION_VECTORS]),
    ({"config": {"delta.enableDeletionVectors": True}}, [P.DELETION_VECTORS]),
    ({"config": {"delta.enableDeletionVectors": "false"}}, []),
    ({"reader_features": ["deletionVectors"]}, [P.DELETION_VECTORS]),
    ({"writer_features": ["deletionVectors"]}, [P.DELETION_VECTORS]),
    ({"reader_features": ["deletionVectors"], "writer_features": ["deletionVectors"],
      "config": {"delta.enableDeletionVectors": "true"}}, [P.DELETION_VECTORS]),
    ({"config": {"delta.columnMapping.mode": "name"}}, [P.CM_NAME]),
    ({"config": {"delta.columnMapping.mode": "Name"}}, [P.CM_NAME]),
    ({"config": {"delta.columnMapping.mode": "id"}}, [P.CM_ID]),
    ({"config": {"delta.columnMapping.mode": "ID"}}, [P.CM_ID]),
    ({"config": {"delta.columnMapping.mode": "none"}}, []),
    ({"config": {"delta.columnMapping.mode": ""}}, []),
    ({"writer_features": ["columnMapping"]}, []),  # the mode is only in the settings
    ({"reader_features": ["columnMapping"], "writer_features": ["columnMapping"]}, []),
    ({"config": {"delta.checkpointPolicy": "v2"}}, [P.V2_CHECKPOINTS]),
    ({"config": {"delta.checkpointPolicy": "V2"}}, [P.V2_CHECKPOINTS]),
    ({"config": {"delta.checkpointPolicy": "classic"}}, []),
    ({"reader_features": ["v2Checkpoint"]}, [P.V2_CHECKPOINTS]),
    ({"writer_features": ["v2Checkpoint"]}, [P.V2_CHECKPOINTS]),
    ({"reader_features": [], "writer_features": [], "config": {}}, []),
    ({"config": None}, []),
    ({"writer_features": ["appendOnly", "invariants", "timestampNtz"]}, []),
], ids=lambda v: repr(v))
def test_each_feature_is_found_from_where_it_can_be(kw, want):
    assert P.features_used(_table(**kw)) == want


def test_every_feature_together_comes_back_in_matrix_order_once():
    table = _table(reader_features=["v2Checkpoint", "deletionVectors"],
                   writer_features=["deletionVectors", "v2Checkpoint", "columnMapping"],
                   config={"delta.checkpointPolicy": "v2", "delta.columnMapping.mode": "id",
                           "delta.enableDeletionVectors": "true"})
    assert P.features_used(table) == [P.DELETION_VECTORS, P.CM_ID, P.V2_CHECKPOINTS]
    table = _table(writer_features=["v2Checkpoint"], config={"delta.columnMapping.mode": "name"})
    assert P.features_used(table) == [P.CM_NAME, P.V2_CHECKPOINTS]


@pytest.mark.parametrize("name", ["dv", "dv_part", "dv_cm", "cm_name", "cm_id"])
def test_the_spark_tables_use_what_spark_turned_on(name):
    want = {"dv": [P.DELETION_VECTORS], "dv_part": [P.DELETION_VECTORS], "dv_cm": [P.DELETION_VECTORS, P.CM_NAME],
            "cm_name": [P.CM_NAME], "cm_id": [P.CM_ID]}[name]
    assert P.features_used(DeltaTable(str(FIXTURES / name))) == want


# ---- The profiles


def test_the_profiles_hold_their_limits():
    assert (P.MAX_READER, P.MAX_WRITER) == (1, 2)
    c, m = P.CONSERVATIVE, P.MODERN
    assert (c.name, c.label, c.max_reader, c.max_writer, c.features) == ("conservative", "Fabric-safe", 1, 2,
                                                                         frozenset())
    assert c.forbidden == {"delta.enableDeletionVectors": {"true"}, "delta.columnMapping.mode": {"name", "id"},
                           "delta.checkpointPolicy": {"v2"}}
    assert (m.name, m.label, m.max_reader, m.max_writer) == ("modern", "modern", 3, 7)
    assert m.features == {"deletionVectors", "columnMapping", "appendOnly", "invariants"}
    assert m.forbidden == {"delta.columnMapping.mode": {"id"}, "delta.checkpointPolicy": {"v2"}}
    assert P.PROFILES == {"conservative": c, "modern": m}
    assert P.DEFAULT_PROFILE is m


def test_a_profile_is_found_by_its_exact_name_or_passed_through(monkeypatch):
    assert P.profile("modern") is P.MODERN
    assert P.profile("conservative") is P.CONSERVATIVE
    assert P.profile(P.CONSERVATIVE) is P.CONSERVATIVE
    custom = P.Profile("x", "x", 9, 9, frozenset(), {})
    assert P.profile(custom) is custom
    assert P.profile(None) is P.MODERN
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)
    assert P.profile(None) is P.CONSERVATIVE


@pytest.mark.parametrize("name", ["Modern", "MODERN", " modern", "modern ", "Fabric-safe", "safe", ""])
def test_a_profile_name_must_be_exact(name):
    with pytest.raises(ValueError) as err:
        P.profile(name)
    assert str(err.value) == f"Unknown Delta profile {name!r}. Use conservative or modern."


# ---- problems(): each boundary


@pytest.mark.parametrize(("within", "reader", "writer"), [("conservative", 1, 2), ("modern", 3, 7)])
def test_versions_at_the_profile_max_are_inside_and_one_over_is_not(within, reader, writer):
    assert P.problems(_table(reader=reader, writer=writer), within) == []
    assert P.problems(_table(reader=reader + 1, writer=writer), within) == [f"reader version {reader + 1} > {reader}"]
    assert P.problems(_table(reader=reader, writer=writer + 1), within) == [f"writer version {writer + 1} > {writer}"]
    assert P.problems(_table(reader=reader + 1, writer=writer + 1), within) == [
        f"reader version {reader + 1} > {reader}", f"writer version {writer + 1} > {writer}"]


@pytest.mark.parametrize("feature", ["deletionVectors", "columnMapping", "appendOnly", "invariants"])
def test_each_feature_the_modern_profile_allows_is_inside_it_and_outside_the_conservative(feature):
    table = _table(reader=3, writer=7, reader_features=[feature], writer_features=[feature])
    assert P.problems(table, "modern") == []
    assert P.problems(_table(reader_features=[feature], writer_features=[feature]), "conservative") == [
        f"reader features ['{feature}']", f"writer features ['{feature}']"]


def test_an_unknown_feature_is_outside_both_profiles_and_named_sorted():
    table = _table(reader=3, writer=7, reader_features=["timestampNtz", "deletionVectors"],
                   writer_features=["typeWidening", "appendOnly", "domainMetadata"])
    assert P.problems(table, "modern") == ["reader features ['timestampNtz']",
                                           "writer features ['domainMetadata', 'typeWidening']"]
    many = ["vacuumProtocolCheck", "typeWidening", "timestampNtz", "rowTracking", "identityColumns",
            "icebergCompatV2", "generatedColumns", "domainMetadata", "clustering", "checkConstraints"]
    assert P.problems(_table(writer_features=many), "conservative") == [f"writer features {sorted(many)}"]
    assert P.problems(_table(writer_features=["v2Checkpoint"]), "modern") == ["writer features ['v2Checkpoint']"]
    assert P.problems(_table(reader_features=["v2Checkpoint"]), "modern") == ["reader features ['v2Checkpoint']"]


@pytest.mark.parametrize(("key", "value", "conservative", "modern"), [
    ("delta.enableDeletionVectors", "true", True, False),
    ("delta.enableDeletionVectors", "True", True, False),
    ("delta.enableDeletionVectors", "false", False, False),
    ("delta.columnMapping.mode", "name", True, False),
    ("delta.columnMapping.mode", "id", True, True),
    ("delta.columnMapping.mode", "ID", True, True),
    ("delta.columnMapping.mode", "none", False, False),
    ("delta.checkpointPolicy", "v2", True, True),
    ("delta.checkpointPolicy", "classic", False, False),
    ("delta.appendOnly", "true", False, False),
])
def test_each_forbidden_setting_and_its_neighbours(key, value, conservative, modern):
    table = _table(config={key: value})
    assert P.problems(table, "conservative") == ([f"{key}={value}"] if conservative else [])
    assert P.problems(table, "modern") == ([f"{key}={value}"] if modern else [])


def test_problems_come_in_order_versions_then_features_then_settings():
    table = _table(reader=4, writer=8, reader_features=["x"], writer_features=["y"],
                   config={"delta.checkpointPolicy": "v2", "delta.columnMapping.mode": "id"})
    assert P.problems(table, P.MODERN) == ["reader version 4 > 3", "writer version 8 > 7", "reader features ['x']",
                                           "writer features ['y']", "delta.columnMapping.mode=id",
                                           "delta.checkpointPolicy=v2"]


def test_problems_with_no_profile_named_use_the_default(monkeypatch):
    dv = _table(reader=3, writer=7, reader_features=["deletionVectors"], writer_features=["deletionVectors"])
    assert P.problems(dv) == []
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)
    assert P.problems(dv) == ["reader version 3 > 1", "writer version 7 > 2", "reader features ['deletionVectors']",
                              "writer features ['deletionVectors']"]


# ---- unreadable_by()


def test_unreadable_by_names_each_setting_once_in_order():
    table = _table(reader_features=["deletionVectors", "v2Checkpoint"], writer_features=["deletionVectors"],
                   config={"delta.checkpointPolicy": "v2", "delta.columnMapping.mode": "id",
                           "delta.enableDeletionVectors": "true"})
    assert P.unreadable_by(table) == [NOTE_DV, NOTE_CM_ID, NOTE_V2]
    assert P.unreadable_by(_table(config={"delta.columnMapping.mode": "none"})) == []
    assert P.unreadable_by(_table(writer_features=["deletionVectors"])) == [NOTE_DV]


# ---- check_table()


def test_a_table_outside_the_profile_says_which_profile_and_who_cant_read_it():
    table = _table(reader=2, writer=7, writer_features=["columnMapping"], config={"delta.columnMapping.mode": "id"})
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(table, "modern", readers=["fabric_spark"])
    assert str(err.value) == f"Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses {NOTE_CM_ID}"
    assert isinstance(err.value, ValueError)


def test_several_notes_are_joined_and_a_version_alone_has_none():
    table = _table(config={"delta.columnMapping.mode": "id", "delta.checkpointPolicy": "v2"})
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(table, "conservative")
    assert str(err.value) == ("Table is outside the Fabric-safe Delta profile: delta.columnMapping.mode=id; "
                              f"delta.checkpointPolicy=v2. It uses {NOTE_CM_ID}; and {NOTE_V2}")
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(_table(reader=4), "modern")
    assert str(err.value) == "Table is outside the modern Delta profile: reader version 4 > 3"


def test_the_conservative_profile_never_asks_the_readers(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("why() asked")

    monkeypatch.setattr(P, "why", boom)
    P.check_table(_table(), "conservative", readers=["python_notebooks"])
    P.check_table(_table(), P.CONSERVATIVE)
    with pytest.raises(AssertionError, match="why"):
        P.check_table(_table(), "modern")


DV_TABLE = dict(reader=3, writer=7, reader_features=["deletionVectors"], writer_features=["deletionVectors"],
                config={"delta.enableDeletionVectors": "true"})
DV_REASON = f"It uses deletion vectors, which Fabric Python notebooks can't read ({SRC_INTEROP})."


def test_a_reader_that_cant_read_it_refuses_before_the_write():
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(_table(**DV_TABLE), "modern", readers=["python_notebooks"])
    assert str(err.value) == ("Nothing was written to this table, because one of its readers can't read a feature it "
                              f"uses. {DV_REASON} Keep writing this table from Fabric Spark.")


def test_a_reader_that_cant_read_it_after_the_commit_says_it_committed():
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(_table(**DV_TABLE), "modern", readers=["python_notebooks"], committed=True)
    assert str(err.value) == ("The write committed, but one of this table's readers can't read a feature the table "
                              f"now uses. {DV_REASON} If it was an append, don't run it again, because that would add "
                              "its rows twice. Check the table from Fabric Spark.")


def test_several_reasons_are_joined_with_a_space():
    table = _table(**{**DV_TABLE, "config": {"delta.enableDeletionVectors": "true",
                                              "delta.columnMapping.mode": "name"}})
    with pytest.raises(P.ProfileViolation) as err:
        P.check_table(table, "modern", readers=["python_notebooks", "snowflake"])
    assert (f"uses. {DV_REASON} It uses column mapping by name, which Fabric Python notebooks can't read "
            f"({SRC_INTEROP}). It uses column mapping by name, and no source we found says whether Snowflake "
            f"can read it ({SRC_INTEROP}). Keep writing") in str(err.value)


def test_no_readers_named_means_the_defaults_and_an_empty_list_means_none(monkeypatch):
    table = _table(**DV_TABLE)
    P.check_table(table, "modern")  # the SQL endpoint and Direct Lake read deletion vectors
    P.check_table(table, "modern", readers=["sql_endpoint", "direct_lake", "databricks"])
    monkeypatch.setattr(P, "DEFAULT_READERS", ("pipelines",))
    with pytest.raises(P.ProfileViolation, match="Fabric pipelines can't read"):
        P.check_table(table, "modern")
    P.check_table(table, "modern", readers=[])
    P.check_table(table, "modern", readers=())


def test_check_table_with_no_profile_uses_the_default(monkeypatch):
    table = _table(**DV_TABLE)
    P.check_table(table)
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)
    with pytest.raises(P.ProfileViolation, match="^Table is outside the Fabric-safe Delta profile: "):
        P.check_table(table)


def test_an_unknown_reader_is_refused_by_name_on_the_modern_profile():
    with pytest.raises(ValueError, match="^Unknown Delta reader 'Spark'"):
        P.check_table(_table(), "modern", readers=["Spark"])


# ---- open_target() and note_after_write(), on copies of the Spark tables


def _copy(tmp_path, name):
    shutil.copytree(FIXTURES / name, tmp_path / name)
    return str(tmp_path / name)


def test_open_target_hands_back_the_table_it_checked(tmp_path):
    uri = _copy(tmp_path, "dv_cm")
    table = P.open_target(uri)
    assert isinstance(table, DeltaTable) and table.version() == DeltaTable(uri).version()
    assert P.check_target(uri) is None


def test_open_target_on_no_table_is_none(tmp_path):
    assert P.open_target(str(tmp_path / "nothing")) is None
    assert P.check_target(str(tmp_path / "nothing")) is None


def test_open_target_refuses_a_table_outside_the_profile_it_is_given(tmp_path):
    with pytest.raises(P.ProfileViolation, match="^Table is outside the modern Delta profile: "
                                                  "delta.columnMapping.mode=id. It uses column mapping by ID"):
        P.open_target(_copy(tmp_path, "cm_id"))
    with pytest.raises(P.ProfileViolation, match="^Table is outside the Fabric-safe Delta profile: reader version 3"):
        P.open_target(_copy(tmp_path, "dv"), within="conservative")


class _Log:
    def __init__(self):
        self.said = []

    def warning(self, *args):
        self.said.append(args)


def test_note_after_write_reports_only_a_table_outside_the_default(tmp_path):
    log = _Log()
    assert P.note_after_write(DeltaTable(_copy(tmp_path, "cm_name")), log) == []
    assert log.said == []
    found = P.note_after_write(DeltaTable(_copy(tmp_path, "cm_id")), log)
    assert found == ["delta.columnMapping.mode=id"]
    assert log.said == [("The write committed, but the table is now outside the %s Delta profile: %s", "modern",
                         "delta.columnMapping.mode=id")]


def test_note_after_write_without_a_log_uses_the_modules(tmp_path, caplog):
    with caplog.at_level("WARNING", logger=P.__name__):
        found = P.note_after_write(_table(reader=4, writer=8))
    assert found == ["reader version 4 > 3", "writer version 8 > 7"]
    assert caplog.messages == [("The write committed, but the table is now outside the modern Delta profile: "
                               "reader version 4 > 3; writer version 8 > 7")]
