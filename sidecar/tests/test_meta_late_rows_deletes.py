"""PB-019: late-arriving rows ([load] lookback) and source deletes ([load] deletes) in incremental loads.

Every scenario runs on this PC's engine twice, once with Delta MERGE writes and once with the
read-everything-and-overwrite path (Engine(merge_writes=False)); both must give the same table.
The engine is the same code a run in Fabric uses (fabric_compute.run builds an Engine), so what holds
here holds there.
"""

import datetime as dt
import json
import textwrap
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake

from fabric_desktop.meta import Engine, LocalStore, Project, validate
from fabric_desktop.meta.templates import init_project

DAY1, DAY2, DAY3 = dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3)
UTC = dt.timezone.utc


def at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 1, hour, minute, tzinfo=UTC)


def rows(ids, modified, amount=1.0):
    return pl.DataFrame({"id": list(ids), "amount": [float(amount)] * len(ids), "modified": [modified] * len(ids)},
                        schema={"id": pl.Int64, "amount": pl.Float64, "modified": pl.Datetime("us", "UTC")})


class Setup:
    """A source Delta table loaded incrementally into bronze.events, with the given [load] extras."""

    def __init__(self, tmp: Path, merge: bool, load: str = "", keys: bool = True, columns: str = ""):
        self.root, self.src, self.merge = tmp / "project", tmp / "source", merge
        init_project(self.root, "3", "Chris", sample=False)
        self.write_entity(load, keys, columns)
        self.store = LocalStore(self.root / ".local" / "dev", "dev")

    def write_entity(self, load: str = "", keys: bool = True, columns: str = "") -> None:
        key = 'keys = ["id"]' if keys else ""
        text = textwrap.dedent(f"""
            name = "bronze.events"
            owner = "Chris"
            [source]
            table = {json.dumps(self.src.as_posix())}
            [load]
            mode = "incremental"
            watermark = "modified"
            {{load}}
            [contract]
            {key}
            [contract.columns]
            id = "int64"
            amount = "float64"
            modified = "datetime"
            {{columns}}
        """).strip().replace("{load}", load).replace("{columns}", columns)
        p = self.root / "metadata" / "entities" / "bronze.events.toml"
        p.write_text(text + "\n", encoding="utf-8")

    def source(self, df: pl.DataFrame, mode: str = "append") -> None:
        write_deltalake(str(self.src), df.to_arrow(), mode=mode)

    def engine(self, day=DAY1) -> Engine:
        return Engine(Project(self.root), self.store, day, merge_writes=self.merge)

    def run(self, day=DAY1, backfill=None):
        res = self.engine(day).run("bronze.events", backfill=backfill)
        assert res.status == "ok", res.message
        return res

    def table(self) -> pl.DataFrame:
        df = self.engine().read_entity("bronze.events").drop("_run_id", "_loaded_at")
        return df.sort("id")

    def watermark(self) -> str:
        return self.store.meta("watermarks")["value"][-1]

    def step(self, res, name: str) -> str:
        return next(s.detail for s in res.steps if s.name == name)


def both(tmp_path, **kw) -> tuple[Setup, Setup]:
    return Setup(tmp_path / "merge", True, **kw), Setup(tmp_path / "rewrite", False, **kw)


# ----------------------------------------------------------------------------- defaults: as before


def test_without_the_new_settings_a_late_row_and_a_source_delete_are_left_as_before(tmp_path):
    """Criterion 4: an entity with neither lookback nor deletes reads strictly after the watermark
    and never touches a key the source dropped; the table gains no columns."""
    for s in both(tmp_path):
        s.source(rows([1, 2], at(10)))
        s.run()
        s.source(rows([3], at(9)))  # late: before the watermark
        s.source(rows([4], at(11)))
        res = s.run(DAY2)
        assert res.rows_read == 1 and "incremental, modified after" in s.step(res, "read")
        assert "lookback" not in s.step(res, "read")
        assert s.table()["id"].to_list() == [1, 2, 4]  # the late row is not read, as before
        s.source(rows([1, 4], at(12)), mode="overwrite")  # 2 is deleted at the source
        res = s.run(DAY3)
        t = s.table()
        assert t["id"].to_list() == [1, 2, 4]  # the deleted key stays, as before
        assert "is_deleted" not in t.columns and "deleted_at" not in t.columns
        assert not any(st.name == "deletes" for st in res.steps)
        assert s.watermark().startswith("2026-10-01T12:00")


def test_without_the_new_settings_the_entity_and_its_logic_are_unchanged(tmp_path):
    s = Setup(tmp_path, True)
    e = Project(s.root).entities["bronze.events"]
    assert e.deletes == "none" and not e.soft_deletes
    assert "is_deleted" not in e.mode_columns()
    before = e.logic_hash()
    # deletes = "none" spelled out is the default: no new columns, no deletes step
    s.write_entity('deletes = "none"')
    e2 = Project(s.root).entities["bronze.events"]
    assert e2.mode_columns() == e.mode_columns() and before  # a hash still works
    assert validate(Project(s.root)) == []


# ----------------------------------------------------------------------------- lookback


def test_a_late_row_inside_the_lookback_lands_exactly_once(tmp_path):
    """Criterion 1."""
    new, old = both(tmp_path, load='lookback = "2 hours"')
    for s in (new, old):
        s.source(rows([1, 2], at(10)))
        s.run()
        assert s.watermark().startswith("2026-10-01T10:00")
        s.source(rows([3], at(9, 30)))  # arrives late, half an hour before the watermark
        s.source(rows([4], at(11)))
        res = s.run(DAY2)
        assert "less the lookback 2 hours" in s.step(res, "read")
        t = s.table()
        assert t["id"].to_list() == [1, 2, 3, 4]  # the late row landed, and 1 and 2 are still one row each
        assert s.watermark().startswith("2026-10-01T11:00")
        # The next run reads the same window again: nothing lands twice.
        s.run(DAY3)
        s.run(DAY3)
        t = s.table()
        assert t["id"].to_list() == [1, 2, 3, 4] and t.height == 4
        # A row older than the lookback is still left out.
        s.source(rows([5], at(8)))
        s.run(DAY3)
        assert 5 not in s.table()["id"].to_list()
    assert new.table().equals(old.table())


def test_a_lookback_rereading_only_older_rows_never_moves_the_watermark_back(tmp_path):
    s = Setup(tmp_path, True, load='lookback = "3 hours"')
    s.source(rows([1], at(8)))
    s.source(rows([2], at(10)))
    s.run()
    s.source(rows([2], at(10), amount=5), mode="overwrite")  # only 2 is left, still at 10:00
    s.source(rows([3], at(9)))  # late, and older than the watermark
    s.run(DAY2)
    assert s.watermark().startswith("2026-10-01T10:00")
    assert s.table().filter(pl.col("id") == 3).height == 1


def test_a_lookback_in_hours_on_a_date_watermark_goes_back_a_whole_day(tmp_path):
    root, src = tmp_path / "project", tmp_path / "source"
    init_project(root, "3", "Chris", sample=False)
    (root / "metadata" / "entities" / "bronze.days.toml").write_text(textwrap.dedent(f"""
        name = "bronze.days"
        owner = "Chris"
        [source]
        table = {json.dumps(src.as_posix())}
        [load]
        mode = "incremental"
        watermark = "day"
        lookback = "2 hours"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        day = "date"
    """).strip() + "\n", encoding="utf-8")
    store = LocalStore(root / ".local" / "dev", "dev")
    write_deltalake(str(src), pl.DataFrame({"id": [1], "day": [dt.date(2026, 10, 5)]}).to_arrow())
    assert Engine(Project(root), store, DAY1).run("bronze.days").status == "ok"
    late = pl.DataFrame({"id": [2, 3], "day": [dt.date(2026, 10, 5), dt.date(2026, 10, 4)]})
    write_deltalake(str(src), late.to_arrow(), mode="append")
    assert Engine(Project(root), store, DAY2).run("bronze.days").status == "ok"
    # 2 hours rounds up to one day, so the watermark's own day (the 5th) is read again; the 4th isn't.
    # Without the rounding the read stays "after the 5th" and the late row of the 5th is missed.
    assert store.read("LH_Bronze", "dbo", "days").sort("id")["id"].to_list() == [1, 2]


def test_a_lookback_on_a_numeric_watermark_is_a_number(tmp_path):
    root, src = tmp_path / "project", tmp_path / "source"
    init_project(root, "3", "Chris", sample=False)
    (root / "metadata" / "entities" / "bronze.seq.toml").write_text(textwrap.dedent(f"""
        name = "bronze.seq"
        owner = "Chris"
        [source]
        table = {json.dumps(src.as_posix())}
        [load]
        mode = "incremental"
        watermark = "seq"
        lookback = 10
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        seq = "int64"
    """).strip() + "\n", encoding="utf-8")
    store = LocalStore(root / ".local" / "dev", "dev")
    write_deltalake(str(src), pl.DataFrame({"id": [1, 2], "seq": [100, 110]}).to_arrow())
    assert Engine(Project(root), store, DAY1).run("bronze.seq").status == "ok"
    write_deltalake(str(src), pl.DataFrame({"id": [3, 4], "seq": [105, 95]}).to_arrow(), mode="append")
    assert Engine(Project(root), store, DAY2).run("bronze.seq").status == "ok"
    t = store.read("LH_Bronze", "dbo", "seq").sort("id")
    assert t["id"].to_list() == [1, 2, 3]  # 105 is inside 110 - 10; 95 isn't


# ----------------------------------------------------------------------------- deletes


def test_a_source_delete_is_marked_with_soft_deletes(tmp_path):
    """Criterion 2, soft."""
    new, old = both(tmp_path, load='deletes = "soft"\nmax_deletes = "50%"')
    for s in (new, old):
        s.source(rows([1, 2, 3], at(10)))
        s.run()
        t = s.table()
        assert t["is_deleted"].to_list() == [False, False, False] and t["deleted_at"].null_count() == 3
        s.source(rows([1, 3], at(10)), mode="overwrite")  # 2 is deleted at the source
        eng = s.engine(DAY2)
        res = eng.run("bronze.events")
        assert res.status == "ok", res.message
        assert "1 key gone from the source to mark deleted" in s.step(res, "deletes")
        assert "marked 1 row deleted" in s.step(res, "write")
        t = s.table()
        assert t["id"].to_list() == [1, 2, 3]  # the row stays
        assert t["is_deleted"].to_list() == [False, True, False]
        assert t.filter(pl.col("id") == 2)["deleted_at"].item() == eng.run_at
        assert t.schema["deleted_at"] == pl.Datetime("us", "UTC")
        # Another run doesn't mark it again (its deleted_at stays the first one).
        first = t.filter(pl.col("id") == 2)["deleted_at"].item()
        res = s.run(DAY3)
        assert "0 keys gone" in s.step(res, "deletes")
        assert s.table().filter(pl.col("id") == 2)["deleted_at"].item() == first
        # The key comes back at the source: the row is live again.
        s.source(rows([2], at(12), amount=7))
        s.run(DAY3)
        back = s.table().filter(pl.col("id") == 2)
        assert back["is_deleted"].item() is False and back["deleted_at"].item() is None and back["amount"].item() == 7
    assert new.table().equals(old.table())


def test_a_source_delete_is_removed_with_hard_deletes(tmp_path):
    """Criterion 2, hard."""
    new, old = both(tmp_path, load='deletes = "hard"\nmax_deletes = "50%"')
    for s in (new, old):
        s.source(rows([1, 2, 3, 4], at(10)))
        s.run()
        s.source(rows([1, 3], at(10)), mode="overwrite")  # 2 and 4 are deleted at the source
        s.source(rows([5], at(11)))  # and a new row arrives in the same run
        res = s.run(DAY2)
        assert "removed 2 rows deleted at the source" in s.step(res, "write")
        t = s.table()
        assert t["id"].to_list() == [1, 3, 5]
        assert "is_deleted" not in t.columns and "__fd_delete" not in t.columns
        assert res.rows_written == 1
        assert s.watermark().startswith("2026-10-01T11:00")
    assert new.table().equals(old.table())


def test_deletes_alone_still_commit_when_no_row_is_new(tmp_path):
    for s in both(tmp_path, load='deletes = "hard"\nmax_deletes = "50%"'):
        s.source(rows([1, 2], at(10)))
        s.run()
        s.source(rows([1], at(10)), mode="overwrite")
        res = s.run(DAY2)
        assert res.delta_version is not None and s.table()["id"].to_list() == [1]


def test_lookback_and_soft_deletes_together(tmp_path):
    for s in both(tmp_path, load='lookback = "1 hour"\ndeletes = "soft"\nmax_deletes = "50%"'):
        s.source(rows([1, 2], at(10)))
        s.run()
        s.source(rows([1], at(10)), mode="overwrite")
        s.source(rows([3], at(9, 30)))  # late
        s.run(DAY2)
        t = s.table()
        assert t["id"].to_list() == [1, 2, 3] and t["is_deleted"].to_list() == [False, True, False]


def test_an_empty_source_stops_deletes_instead_of_deleting_everything(tmp_path):
    s = Setup(tmp_path, True, load='deletes = "hard"')
    s.source(rows([1, 2], at(10)))
    s.run()
    s.source(rows([], at(10)), mode="overwrite")
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and "the source has no live rows" in res.message
    assert s.table()["id"].to_list() == [1, 2]


@pytest.mark.parametrize("mode", ["soft", "hard"])
def test_more_deletes_than_max_deletes_stops_the_run(tmp_path, mode):
    """A source cut short looks like a mass delete: over max_deletes (20% of live keys unless set), the
    run stops with nothing written; raising max_deletes lets the same run go ahead."""
    for s in both(tmp_path / mode, load=f'deletes = "{mode}"'):
        s.source(rows(range(1, 11), at(10)))
        s.run()
        before = s.table()
        s.source(rows(range(1, 8), at(10)), mode="overwrite")  # 3 of 10 keys gone: 30%
        res = s.engine(DAY2).run("bronze.events")
        assert res.status == "failed" and "3 keys of 10 live keys" in res.message and "max_deletes" in res.message
        assert s.table().equals(before)
        s.write_entity(f'deletes = "{mode}"\nmax_deletes = 3')
        res = s.run(DAY2)
        assert "3 keys gone from the source" in s.step(res, "deletes")


def test_a_source_key_that_cant_be_read_as_the_key_type_stops_deletes(tmp_path):
    """Type drift: the source's id turns into text with a value that isn't a number. Cast leniently it
    would be null, look gone and be deleted; the run stops instead."""
    for s in both(tmp_path, load='deletes = "hard"\nmax_deletes = "100%"'):
        s.source(rows([1, 2, 3], at(10)))
        s.run()
        drifted = pl.DataFrame({"id": ["1", "2", "three"], "amount": [1.0] * 3, "modified": [at(10)] * 3},
                               schema={"id": pl.String, "amount": pl.Float64, "modified": pl.Datetime("us", "UTC")})
        write_deltalake(str(s.src), drifted.to_arrow(), mode="overwrite", schema_mode="overwrite")
        res = s.engine(DAY2).run("bronze.events")
        assert res.status == "failed" and "couldn't be read" in res.message and "'three'" in res.message
        assert s.table()["id"].to_list() == [1, 2, 3]


def test_ids_drifting_to_decimal_text_stop_deletes_instead_of_removing_every_row(tmp_path):
    """Quality's probe: day 2's source has ids "1.0".."10.0" and "11". Read leniently, the ten would be
    null and gone, and only 11 would be left."""
    for s in both(tmp_path, load='deletes = "hard"\nmax_deletes = "100%"'):
        s.source(rows(range(1, 11), at(10)))
        s.run()
        ids = [f"{i}.0" for i in range(1, 11)] + ["11"]
        drifted = pl.DataFrame({"id": ids, "amount": [1.0] * 11, "modified": [at(10)] * 10 + [at(11)]},
                               schema={"id": pl.String, "amount": pl.Float64, "modified": pl.Datetime("us", "UTC")})
        write_deltalake(str(s.src), drifted.to_arrow(), mode="overwrite", schema_mode="overwrite")
        res = s.engine(DAY2).run("bronze.events")
        assert res.status == "failed" and "couldn't be read" in res.message, res.message
        assert s.table()["id"].to_list() == list(range(1, 11))


def test_an_unrelated_is_deleted_column_in_a_source_is_an_ordinary_column(tmp_path):
    """Only a table written with deletes = "soft" (is_deleted and deleted_at) has its marked rows count as gone."""
    for s in both(tmp_path, load='deletes = "hard"\nmax_deletes = "100%"'):
        s.source(rows([1, 2], at(10)))
        s.run()
        own = rows([1, 2], at(10)).with_columns(pl.Series("is_deleted", [True, False]))
        write_deltalake(str(s.src), own.to_arrow(), mode="overwrite", schema_mode="overwrite")
        res = s.run(DAY2)
        assert "0 keys gone" in s.step(res, "deletes")
        assert s.table()["id"].to_list() == [1, 2]


def test_a_clean_rule_that_blanks_a_source_key_stops_deletes(tmp_path):
    s = Setup(tmp_path, True)
    p = s.root / "metadata" / "entities" / "bronze.events.toml"
    p.write_text(p.read_text(encoding="utf-8").replace('id = "int64"', 'id = "string"')
                 .replace('watermark = "modified"', 'watermark = "modified"\ndeletes = "hard"\nmax_deletes = "100%"')
                 + '[[clean]]\ncolumn = "id"\nnull_if = ["N/A"]\n', encoding="utf-8")
    assert validate(Project(s.root)) == []
    ids = lambda xs: pl.DataFrame({"id": xs, "amount": [1.0] * len(xs), "modified": [at(10)] * len(xs)},
                                  schema={"id": pl.String, "amount": pl.Float64, "modified": pl.Datetime("us", "UTC")})
    s.source(ids(["a", "b"]))
    s.run()
    s.source(ids(["a", "N/A"]), mode="overwrite")
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and "couldn't be read" in res.message and "'N/A'" in res.message
    assert s.table()["id"].to_list() == ["a", "b"]


def test_a_source_whose_rows_are_all_marked_deleted_stops_instead_of_deleting_them(tmp_path):
    """An is_deleted column in the source counts: rows marked true are gone. Every row (or more than
    max_deletes of them) marked at once stops the run."""
    for s in both(tmp_path, load='deletes = "hard"'):
        s.source(rows(range(1, 11), at(10)))
        s.run()
        for marked in (range(1, 11), range(1, 4)):  # all of them, then 3 of 10
            flagged = rows(range(1, 11), at(10)).with_columns(pl.col("id").is_in(list(marked)).alias("is_deleted"),
                                                               pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("deleted_at"))
            write_deltalake(str(s.src), flagged.to_arrow(), mode="overwrite", schema_mode="overwrite")
            res = s.engine(DAY2).run("bronze.events")
            assert res.status == "failed", res.message
            assert s.table()["id"].to_list() == list(range(1, 11))
        assert "max_deletes" in res.message


def test_max_deletes_is_checked_when_saved(tmp_path):
    for i, bad in enumerate(('"lots"', '"150%"', "-1", "true")):
        s = Setup(tmp_path / f"bad{i}", True, load=f'deletes = "hard"\nmax_deletes = {bad}')
        assert any("load max_deletes is a share" in e for e in validate(Project(s.root))), bad
    for i, good in enumerate(('"20%"', '"2.5%"', "0", "500")):
        s = Setup(tmp_path / f"ok{i}", True, load=f'deletes = "hard"\nmax_deletes = {good}')
        assert validate(Project(s.root)) == [], good


def test_turning_on_soft_deletes_marks_the_rows_already_loaded_live(tmp_path):
    for s in both(tmp_path):
        s.source(rows([1, 2], at(10)))
        s.run()
        s.write_entity('deletes = "soft"\nmax_deletes = "50%"')
        s.source(rows([1], at(11)), mode="overwrite")
        res = s.engine(DAY2).run("bronze.events", backfill="none")  # the logic changed: keep what's loaded
        assert res.status == "ok", res.message
        t = s.table()
        assert t["id"].to_list() == [1, 2] and t["is_deleted"].to_list() == [False, True]


def test_a_downstream_entity_sees_the_soft_delete_columns(tmp_path):
    s = Setup(tmp_path, True, load='deletes = "soft"\nmax_deletes = "50%"')
    (s.root / "metadata" / "entities" / "silver.events.toml").write_text(textwrap.dedent("""
        name = "silver.events"
        owner = "Chris"
        [source]
        entity = "bronze.events"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        is_deleted = "bool"
        [[check]]
        column = "id"
        not_null = true
        unique = true
    """).strip() + "\n", encoding="utf-8")
    assert validate(Project(s.root)) == []
    s.source(rows([1, 2], at(10)))
    s.run()
    s.source(rows([1], at(10)), mode="overwrite")
    s.run(DAY2)
    res = s.engine(DAY2).run("silver.events")
    assert res.status == "ok", res.message
    silver = s.engine().read_entity("silver.events").sort("id")
    assert silver["is_deleted"].to_list() == [False, True]


@pytest.mark.parametrize("deletes", ["soft", "hard"])
def test_a_downstream_entity_with_deletes_sees_a_soft_deleted_upstream_key_as_gone(tmp_path, deletes):
    """bronze marks id 2 deleted but keeps the row, and its watermark doesn't move, so silver's watermark
    never re-reads it: silver's own deletes setting finds it gone."""
    s = Setup(tmp_path, True, load='deletes = "soft"\nmax_deletes = "50%"')
    (s.root / "metadata" / "entities" / "silver.events.toml").write_text(textwrap.dedent(f"""
        name = "silver.events"
        owner = "Chris"
        [source]
        entity = "bronze.events"
        [load]
        mode = "incremental"
        watermark = "modified"
        deletes = "{deletes}"
        max_deletes = "50%"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        modified = "datetime"
        [[check]]
        column = "id"
        not_null = true
        unique = true
    """).strip() + "\n", encoding="utf-8")
    assert validate(Project(s.root)) == []
    s.source(rows([1, 2, 3], at(10)))
    s.run()
    assert s.engine().run("silver.events").status == "ok"
    s.source(rows([1, 3], at(10)), mode="overwrite")
    s.run(DAY2)
    res = s.engine(DAY2).run("silver.events")
    assert res.status == "ok", res.message
    assert "1 key gone from the source" in s.step(res, "deletes")
    silver = s.engine().read_entity("silver.events").sort("id")
    if deletes == "soft":
        assert silver["id"].to_list() == [1, 2, 3] and silver["is_deleted"].to_list() == [False, True, False]
    else:
        assert silver["id"].to_list() == [1, 3]


# ----------------------------------------------------------------------------- validation


@pytest.mark.parametrize("load, keys, columns, message", [
    ('lookback = "two hours"', True, "", "load lookback is a duration"),
    ("lookback = -5", True, "", "load lookback is a duration"),
    ('lookback = "2 hours"', False, "", "needs [contract] keys"),
    ('deletes = "sometimes"', True, "", "load deletes must be one of none, soft, hard"),
    ('deletes = "hard"', False, "", "load deletes compares keys with the source"),
    ('deletes = "soft"', True, 'is_deleted = "bool"', "adds is_deleted itself"),
])
def test_the_new_settings_are_checked_when_saved(tmp_path, load, keys, columns, message):
    s = Setup(tmp_path, True, load=load, keys=keys, columns=columns)
    errors = validate(Project(s.root))
    assert any(message in err for err in errors), errors


def test_the_new_settings_are_only_for_incremental_delta_loads(tmp_path):
    s = Setup(tmp_path, True)
    p = s.root / "metadata" / "entities" / "bronze.events.toml"
    p.write_text(p.read_text(encoding="utf-8").replace('mode = "incremental"', 'mode = "full"')
                 .replace('watermark = "modified"', 'watermark = "modified"\nlookback = "1 hour"\ndeletes = "soft"'),
                 encoding="utf-8")
    errors = validate(Project(s.root))
    assert any("lookback is for incremental loads" in err for err in errors), errors
    assert any("load deletes is for incremental loads" in err for err in errors), errors
    files = tmp_path / "files"
    files.mkdir()
    q = s.root / "metadata" / "entities" / "bronze.files.toml"
    q.write_text(textwrap.dedent("""
        name = "bronze.files"
        owner = "Chris"
        [source]
        files = "landing/x/*.csv"
        [load]
        mode = "incremental"
        watermark = "modified"
        lookback = "1 hour"
        deletes = "hard"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        modified = "datetime"
    """).strip() + "\n", encoding="utf-8")
    errors = validate(Project(s.root))
    assert any("bronze.files: load lookback needs a Delta source" in err for err in errors), errors
    assert any("bronze.files: load deletes needs a Delta source" in err for err in errors), errors


def test_a_lookback_change_is_not_a_logic_change(tmp_path):
    s = Setup(tmp_path, True)
    before = Project(s.root).entities["bronze.events"].logic_hash()
    s.write_entity('lookback = "2 hours"')
    assert Project(s.root).entities["bronze.events"].logic_hash() == before
