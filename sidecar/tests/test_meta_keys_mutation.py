"""meta/keys.py paths that mutation testing (batch 20, docs/quality/mutation-testing.md) showed no test
checked: how many times a key commit is retried and how long it waits, the check on the keys a batch
just added, the key service's answers, key table counts, and source system defaults and files."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest
from deltalake.exceptions import CommitFailedError

from fabric_desktop.meta import keys as K
from fabric_desktop.meta.store import LocalStore


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "keys", "dev")


@pytest.fixture
def kt(tmp_path):
    return K.KeyTable("customer", {}, tmp_path / "customer.toml")


@pytest.fixture
def waits(monkeypatch):
    """The retry sleeps, with a fixed jitter of 0.2 s."""
    slept = []
    monkeypatch.setattr(K.random, "uniform", lambda a, b: 0.2)
    monkeypatch.setattr(K.time, "sleep", slept.append)
    return slept


def _always_collides(store, monkeypatch):
    tries = []

    def merge_insert(*args, **kwargs):
        tries.append(1)
        raise CommitFailedError("another run committed first")
    monkeypatch.setattr(store, "merge_insert", merge_insert)
    return tries


def _rows(kt, pairs):
    return pl.DataFrame({kt.sk: [s for s, _ in pairs], kt.bk: [b for _, b in pairs],
                         "create_date": [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)] * len(pairs)},
                        schema=kt.schema())


# ---- retries


def test_making_a_key_table_tries_ten_times_waiting_longer_each_time(store, kt, waits, monkeypatch):
    tries = _always_collides(store, monkeypatch)
    with pytest.raises(RuntimeError, match="other runs kept changing the key table"):
        K.ensure_key_table(store, kt)
    assert K.MAX_ATTEMPTS == 10 and len(tries) == 10
    assert waits == pytest.approx([0.2 * n for n in range(1, 11)])


def test_adding_keys_tries_ten_times_waiting_longer_each_time(store, kt, waits, monkeypatch):
    K.ensure_key_table(store, kt)
    tries = _always_collides(store, monkeypatch)
    with pytest.raises(RuntimeError, match="other runs kept adding keys"):
        K.merge_keys(store, kt, pl.Series(["A"]))
    assert len(tries) == 10
    assert waits == pytest.approx([0.2 * n for n in range(1, 11)])


# ---- making the key table


def test_the_unknown_member_is_dated_1900(kt):
    assert kt.seed().to_dicts() == [{"customer_sk": -1, "customer_bk": "UNKNOWN",
                                     "create_date": dt.datetime(1900, 1, 1, tzinfo=dt.timezone.utc)}]


def test_a_key_table_that_has_its_unknown_member_isnt_written_again(store, kt, monkeypatch):
    assert K.ensure_key_table(store, kt) is True
    tries = _always_collides(store, monkeypatch)
    assert K.ensure_key_table(store, kt) is False
    assert tries == []


# ---- adding keys


def test_a_batch_of_known_keys_adds_none(store, kt):
    K.merge_keys(store, kt, pl.Series(["A"]))
    assert K.merge_keys(store, kt, pl.Series(["A", "A"])) == {
        "inserted": 0, "existing": 1, "first": None, "last": None, "attempts": 1}


def test_keys_follow_a_zero_key_and_reading_an_absent_table_finds_none(store, kt):
    found, _ = K._keys_for(store, kt, ["A"])
    assert found.height == 0 and found.columns == list(kt.schema())
    K.ensure_key_table(store, kt)
    store.write(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, kt.table, _rows(kt, [(0, "Z")]), mode="append")
    assert K.merge_keys(store, kt, pl.Series(["A"]))["first"] == 1


def test_a_key_another_run_gave_the_same_business_key_meanwhile_stops_the_batch(store, kt, monkeypatch):
    K.ensure_key_table(store, kt)
    real = store.merge_insert

    def merge_and_clash(lakehouse, schema, table, rows, **kwargs):
        real(lakehouse, schema, table, rows, **kwargs)
        # Another run's key 1 for a different business key lands right after this commit.
        store.write(lakehouse, schema, table, _rows(kt, [(1, "OTHER")]), mode="append")
    monkeypatch.setattr(store, "merge_insert", merge_and_clash)
    with pytest.raises(RuntimeError, match="twice"):
        K.merge_keys(store, kt, pl.Series(["A"]))


# ---- counts


def test_a_missing_key_table_has_no_keys_and_starts_at_one(store, kt):
    assert K.key_stats(store, kt) == {"created": False, "keys": 0, "next": 1}


def test_a_key_table_with_only_its_unknown_member_has_no_keys_and_starts_at_one(store, kt):
    K.ensure_key_table(store, kt)
    assert K.key_stats(store, kt) == {"created": True, "keys": 0, "next": 1}


def test_a_key_table_without_its_unknown_member_counts_every_row(store, kt):
    store.create_if_absent(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, kt.table, kt.schema())
    assert K.key_stats(store, kt) == {"created": True, "keys": 0, "next": 1}
    store.write(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, kt.table, _rows(kt, [(0, "Z"), (1, "A")]), mode="append")
    assert K.key_stats(store, kt) == {"created": True, "keys": 2, "next": 2}


# ---- the platform's key service


class _Service:
    client = object()
    env = "dev"

    def issue(self, name, wanted):
        return {"A": 5, "B": 6, "X": -1}, 1

    def lookup(self, name, value):
        return {"sk": 5, "bk": value}

    def stats(self, name):
        return {"created": True, "keys": 2, "next": 7}


def test_only_a_store_with_both_issue_and_a_client_is_the_key_service(store):
    assert K.is_service(_Service())
    assert not K.is_service(SimpleNamespace(issue=lambda *a: None))
    assert not K.is_service(SimpleNamespace(client=object()))
    assert not K.is_service(store)


def test_the_key_service_answers_lookups_counts_and_new_keys(kt):
    service = _Service()
    assert K.lookup(service, kt, "A") == {"sk": 5, "bk": "A"}
    assert K.key_stats(service, kt) == {"created": True, "keys": 2, "next": 7}
    found, stats = K._issue(service, kt, pl.Series(["A", "B", "X", None]))
    assert sorted(found.rows()) == [("A", 5), ("B", 6)]
    assert stats == {"inserted": 1, "existing": 2, "first": 6, "last": 6}


# ---- Spark SQL and files


def test_a_one_column_business_key_in_spark_is_just_trimmed():
    assert K._spark_bk(["customer_id"]) == "NULLIF(TRIM(CAST(customer_id AS STRING)), '')"
    assert K._spark_bk(["customer_id"], "b") == "NULLIF(TRIM(CAST(b.customer_id AS STRING)), '')"


def test_source_systems_are_active_and_id_0_unless_they_say(tmp_path):
    assert K.SourceSystem(1, "CRM").active is True
    made = K.source_system_from_dict({"code": "CRM"})
    assert made.active is True and made.id == 0
    K.keys_dir(tmp_path).mkdir(parents=True)
    (K.keys_dir(tmp_path) / K.SOURCE_SYSTEMS_FILE).write_text('[[source_system]]\ncode = "CRM"\n', encoding="utf-8")
    systems, errors = K.load_source_systems(tmp_path)
    assert errors == [] and systems[0].id == 0 and systems[0].active is True


def test_files_are_written_into_a_project_that_has_no_keys_folder_yet(tmp_path):
    root = tmp_path / "new_project"
    path = K.write_source_systems(root, [K.SourceSystem(1, "CRM")])
    assert path == K.keys_dir(root) / K.SOURCE_SYSTEMS_FILE and path.is_file()
    other = tmp_path / "another_project"
    assert K.write_key_table(other, "customer").is_file()
