"""Incremental runs of large tables: read only what's new, write only the files that change.

Every scenario runs twice, once with Delta MERGE writes and once with the old read-everything-and-
overwrite path (Engine(merge_writes=False)), and the two tables must hold the same rows.
"""

import datetime as dt
import json
import textwrap
from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError

from fabric_desktop import delta_profile
from fabric_desktop.meta import Engine, LocalStore, Project
from fabric_desktop.meta.templates import init_project

DAY1, DAY2, DAY3 = dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3)


def entity(root: Path, name: str, text: str) -> None:
    p = root / "metadata" / "entities" / f"{name}.toml"
    p.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def rows(ids, modified, amount=1.0, name="a"):
    return pl.DataFrame({"id": list(ids), "name": [name] * len(ids), "amount": [float(amount)] * len(ids),
                         "modified": [modified] * len(ids)},
                        schema={"id": pl.Int64, "name": pl.String, "amount": pl.Float64, "modified": pl.Date})


class Setup:
    """A source Delta table (an adopted lakehouse table) loaded incrementally into bronze, then silver."""

    def __init__(self, tmp: Path, merge: bool, keys: bool = True):
        self.root, self.src, self.merge = tmp / "project", tmp / "source", merge
        init_project(self.root, "3", "Chris", sample=False)
        key = 'keys = ["id"]' if keys else ""
        entity(self.root, "bronze.events", f"""
            name = "bronze.events"
            owner = "Chris"
            [source]
            table = {json.dumps(self.src.as_posix())}
            [load]
            mode = "incremental"
            watermark = "modified"
            [contract]
            {key}
            [contract.columns]
            id = "int64"
            name = "string"
            amount = "float64"
            modified = "date"
        """)
        # A second table uses three of the four columns, so its read can leave one out.
        entity(self.root, "bronze.amounts", f"""
            name = "bronze.amounts"
            owner = "Chris"
            [source]
            entity = "bronze.events"
            [load]
            mode = "incremental"
            watermark = "modified"
            [contract]
            {key}
            [contract.columns]
            id = "int64"
            amount = "float64"
            modified = "date"
        """)
        self.store = LocalStore(self.root / ".local" / "dev", "dev")

    def source(self, df: pl.DataFrame, mode: str = "append") -> None:
        write_deltalake(str(self.src), df.to_arrow(), mode=mode, schema_mode="merge" if mode == "append" else None)

    def engine(self, day=DAY1) -> Engine:
        return Engine(Project(self.root), self.store, day, merge_writes=self.merge)

    def run(self, name="bronze.events", day=DAY1, backfill=None):
        res = self.engine(day).run(name, backfill=backfill)
        assert res.status == "ok", res.message
        return res

    def table(self, name="bronze.events") -> pl.DataFrame:
        df = self.engine().read_entity(name)
        df = df.drop("_run_id")
        return df.sort(df.columns, nulls_last=True)

    def write_step(self, res) -> str:
        return next(s.detail for s in res.steps if s.name == "write")


def both(tmp_path, **kw) -> tuple[Setup, Setup]:
    return Setup(tmp_path / "merge", True, **kw), Setup(tmp_path / "rewrite", False, **kw)


def test_an_update_merges_into_the_table_and_rewrites_only_the_files_it_touches(tmp_path):
    new, old = both(tmp_path)
    for s in (new, old):
        for i in range(5):  # five files of 100 rows
            s.source(rows(range(i * 100, i * 100 + 100), DAY1))
        s.run()
        for i in range(4):  # spread the table over several files, as daily loads do
            s.source(rows(range(1000 + i * 10, 1010 + i * 10), DAY1 + dt.timedelta(days=i + 1)))
            s.run(day=DAY1 + dt.timedelta(days=i + 1))
        s.source(rows([5, 1100], DAY3 + dt.timedelta(days=5), amount=9))  # one update, one new row
        res = s.run(day=DAY3)
        assert res.rows_read == 2 and res.rows_written == 2
    t = new.table()
    assert t.height == 541 and t.filter(pl.col("id") == 5)["amount"].item() == 9
    assert t.equals(old.table())
    files_merge = len(DeltaTable(new.store.uri("LH_Bronze", "dbo", "events")).file_uris())
    files_rewrite = len(DeltaTable(old.store.uri("LH_Bronze", "dbo", "events")).file_uris())
    assert files_merge > files_rewrite == 1  # the untouched files were left as they were
    delta_profile.check_table(DeltaTable(new.store.uri("LH_Bronze", "dbo", "events")))


def test_merge_reports_what_it_rewrote(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(10), DAY1))
    s.run()
    s.source(rows([3, 10], DAY2, amount=5))
    detail = s.write_step(s.run(day=DAY2))
    assert detail.startswith("merged 2 rows on id: 1 updated, 1 new (Delta MERGE rewrote 1 file, added 1)")
    assert "Delta version 1" in detail


def test_incremental_reads_push_the_watermark_and_columns_into_the_scan(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(10), DAY1))
    s.run()
    s.run("bronze.amounts")
    s.source(rows([20, 21], DAY2))
    s.run(day=DAY2)
    res = s.run("bronze.amounts", day=DAY2)
    read = next(x for x in res.steps if x.name == "read")
    assert read.rows == 2 and "incremental, modified after 2026-10-01" in read.detail
    assert "read 3 columns of 4" in read.detail  # bronze.amounts doesn't use name
    assert s.table("bronze.amounts").height == 12


def test_a_run_with_nothing_new_writes_nothing(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(10), DAY1))
    s.run()
    res = s.run(day=DAY2)
    assert res.rows_read == 0 and s.write_step(res) == "nothing new to write"
    assert next(x for x in res.steps if x.name == "write").rows == 10  # counted from the Delta log
    assert len(DeltaTable(s.store.uri("LH_Bronze", "dbo", "events")).history()) == 1


def test_null_keys_and_keyless_tables_behave_as_before(tmp_path):
    new, old = both(tmp_path)
    for s in (new, old):
        s.source(rows(range(3), DAY1))
        s.run()
        s.source(pl.concat([rows([None], DAY2), rows([1], DAY2, amount=7)]))  # null keys never match
        s.run(day=DAY2)
    assert new.table().equals(old.table()) and new.table().height == 4

    new, old = both(tmp_path / "keyless", keys=False)
    for s in (new, old):
        s.source(rows(range(3), DAY1))
        s.run()
        s.source(rows([1, 2], DAY2))
        s.run(day=DAY2)
    assert len(DeltaTable(new.store.uri("LH_Bronze", "dbo", "events")).history(1)) == 1
    assert DeltaTable(new.store.uri("LH_Bronze", "dbo", "events")).history(1)[0]["operation"] == "WRITE"
    assert new.table().height == 5 and new.table().equals(old.table())


def test_repeated_keys_or_changed_types_fall_back_to_a_rewrite_and_say_why(tmp_path):
    new, old = both(tmp_path)
    results = {}
    for s in (new, old):
        s.source(rows(range(3), DAY1))
        s.run()
        s.source(pl.concat([rows([1], DAY2, amount=2), rows([1], DAY2, amount=3)]))  # no unique check
        results[s.merge] = s.run(day=DAY2)
    assert "rewrote the table because new rows repeat a key (id)" in new.write_step(results[True])
    assert new.table().equals(old.table())


def test_a_rewrite_fallback_never_drops_rows_another_writer_committed_meanwhile(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    s.run()
    s.source(pl.concat([rows([1], DAY2, amount=2), rows([1], DAY2, amount=3)]))  # repeated key: rewrite
    real, sneaked = s.store.read_at, []

    def read_then_someone_writes(lakehouse, schema, table):
        out = real(lakehouse, schema, table)
        if table == "events" and not sneaked:
            sneaked.append(1)
            extra = out[0].head(1).with_columns(pl.lit(99, dtype=pl.Int64).alias("id"))
            s.store.write(lakehouse, schema, table, extra, mode="append")  # the app or a Fabric run
        return out
    monkeypatch.setattr(s.store, "read_at", read_then_someone_writes)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda _: None)
    res = s.run(day=DAY2)
    assert "rewrote the table because new rows repeat a key" in s.write_step(res)
    assert 99 in s.table()["id"].to_list()


def test_a_new_source_column_is_added_to_the_table(tmp_path):
    new, old = both(tmp_path)
    for s in (new, old):
        s.source(rows(range(3), DAY1))
        s.run()
        s.source(rows([1, 9], DAY2).with_columns(pl.lit("eu").alias("region")))
        s.run(day=DAY2)
    t = new.table()  # bronze evolves: new source columns are kept
    assert t.filter(pl.col("id") == 9)["region"].item() == "eu" and t.filter(pl.col("id") == 0)["region"].item() is None
    assert t.equals(old.table().select(t.columns))


def test_replayed_rows_merge_on_the_keys(tmp_path):
    new, old = both(tmp_path)
    for s in (new, old):
        s.source(rows(range(5), DAY1))
        s.run()
        fixed = rows([2, 7], DAY1, amount=4).with_columns(pl.all().cast(pl.String))
        res, held = s.engine(DAY2).replay("bronze.events", fixed)
        assert res.status == "ok" and held is None
        if s is new:
            assert "replayed 2 rows on id: 1 updated, 1 new" in s.write_step(res)
    t = new.table()
    assert t.height == 6 and t.filter(pl.col("id") == 2)["amount"].item() == 4
    assert t.equals(old.table())


def test_a_lost_merge_race_is_tried_again(tmp_path, monkeypatch):
    from deltalake.exceptions import CommitFailedError

    s = Setup(tmp_path, True)
    s.source(rows(range(5), DAY1))
    s.run()
    real, calls = s.store.upsert, []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise CommitFailedError("another writer committed first")
        return real(*a, **kw)
    monkeypatch.setattr(s.store, "upsert", flaky)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda sec: None)
    s.source(rows([1, 8], DAY2, amount=3))
    s.run(day=DAY2)
    assert len(calls) == 2 and s.table().height == 6


def test_a_full_rebuild_still_replaces_the_table(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(5), DAY1))
    s.run()
    s.source(rows(range(3), DAY2, amount=6), mode="overwrite")
    res = s.run(day=DAY2, backfill="full")
    assert "rebuilt" in s.write_step(res)
    assert s.table().height == 5 and s.table().filter(pl.col("id") < 3)["amount"].to_list() == [6.0] * 3


def test_text_keys_with_quotes_merge(tmp_path):
    from fabric_desktop.meta.store import _key_bounds
    s = Setup(tmp_path, True)
    uri = s.store.uri("LH_Bronze", "dbo", "codes")
    first = pl.DataFrame({"code": ["a", "o'neil", "z"], "v": [1, 2, 3]})
    s.store.write("LH_Bronze", "dbo", "codes", first)
    _, m = s.store.upsert("LH_Bronze", "dbo", "codes", pl.DataFrame({"code": ["o'neil", "b", None], "v": [9, 8, 7]}),
                          ["code"])
    assert m["num_target_rows_updated"] == 1 and m["num_target_rows_inserted"] == 2
    t = pl.from_arrow(DeltaTable(uri).to_pyarrow_table()).sort("code", nulls_last=True)
    assert t["v"].to_list() == [1, 8, 9, 3, 7]
    assert _key_bounds(pl.DataFrame({"k": [None, None]}, schema={"k": pl.Int64}), ["k"]) == []


# ----------------------------------------------------------------------------- dimensions


class Dims:
    """A customer source table feeding a Type 1 and a Type 2 dimension."""

    def __init__(self, tmp: Path, merge: bool):
        self.root, self.src, self.merge = tmp / "project", tmp / "customers", merge
        init_project(self.root, "3", "Chris", sample=False)
        common = f"""
            [source]
            table = {json.dumps(self.src.as_posix())}
            [contract]
            keys = ["customer_id"]
            [contract.columns]
            cust_sk = "int64"
            customer_id = "int64"
            city = "string"
            email = "string"
        """
        entity(self.root, "bronze.dim1", f"""
            name = "bronze.dim1"
            owner = "Chris"
            [load]
            mode = "scd1"
            [scd]
            surrogate_key = "cust_sk"
            {common}""")
        entity(self.root, "bronze.dim2", f"""
            name = "bronze.dim2"
            owner = "Chris"
            [load]
            mode = "scd2"
            [scd]
            surrogate_key = "cust_sk"
            tracked = ["city"]
            type1 = ["email"]
            {common}""")
        self.store = LocalStore(self.root / ".local" / "dev", "dev")

    def load(self, rows_: list[tuple], day) -> list:
        df = pl.DataFrame(rows_, schema={"customer_id": pl.Int64, "city": pl.String, "email": pl.String}, orient="row")
        write_deltalake(str(self.src), df.to_arrow(), mode="overwrite")
        out = []
        for name in ("bronze.dim1", "bronze.dim2"):
            res = Engine(Project(self.root), self.store, day, merge_writes=self.merge).run(name)
            assert res.status == "ok", res.message
            out.append(res)
        return out

    def table(self, name):
        df = Engine(Project(self.root), self.store).read_entity(name).drop("_run_id")
        return df.select(sorted(df.columns)).sort("cust_sk")


def test_dimensions_merge_only_the_keys_that_arrive_and_match_a_full_rewrite(tmp_path):
    new, old = Dims(tmp_path / "merge", True), Dims(tmp_path / "rewrite", False)
    days = [
        [(i, "Oslo", f"c{i}@x") for i in range(1, 201)],                      # first load
        [(5, "Bergen", "c5@x"), (6, "Oslo", "new6@x"), (300, "Rome", None)],   # move, email fix, new
        [(5, "Bergen", "c5@y"), (7, None, "c7@x"), (6, "Oslo", None)],         # type 1 on history, null city
        [(5, "Oslo", "c5@y")],                                                  # moves back
    ]
    for i, rows_ in enumerate(days):
        for d in (new, old):
            res = d.load(rows_, DAY1 + dt.timedelta(days=i))
            if d is new:
                last = res
    for name in ("bronze.dim1", "bronze.dim2"):
        assert new.table(name).equals(old.table(name)), name
    t2 = new.table("bronze.dim2")
    assert t2.filter(pl.col("customer_id") == 5)["city"].to_list() == ["Oslo", "Bergen", "Oslo"]
    write = [next(s.detail for s in r.steps if s.name == "write") for r in last]
    assert "read 2 rows of the table for 1 business key" in write[0] and "Delta MERGE" in write[0]
    assert "1 changed (new versions)" in write[1] and "for 1 business key" in write[1]


def test_a_dimension_with_repeated_keys_still_rewrites_and_says_why(tmp_path):
    d = Dims(tmp_path, True)
    d.load([(1, "Oslo", "a"), (2, "Rome", "b")], DAY1)
    write_deltalake(str(d.src), pl.DataFrame({"customer_id": [1, 1], "city": ["Oslo", "Bergen"], "email": ["a", "a"]})
                    .to_arrow(), mode="overwrite")
    res = Engine(Project(d.root), d.store, DAY2).run("bronze.dim1")
    assert "new rows repeat a key (customer_id)" in next(s.detail for s in res.steps if s.name == "write")
    # Type 2 can't version two rows of one key in one run; it stops, as it did before (add a unique check).
    assert Engine(Project(d.root), d.store, DAY2).run("bronze.dim2").status == "failed"


def test_a_full_snapshot_into_a_type2_dimension_touches_only_what_changed(tmp_path):
    new, old = Dims(tmp_path / "merge", True), Dims(tmp_path / "rewrite", False)
    base = {i: (i, "Oslo", f"c{i}@x") for i in range(1, 301)}
    snapshots = [
        dict(base),
        base | {5: (5, "Bergen", None)},       # new version; its email is null, the old version keeps c5@x
        base | {5: (5, "Bergen", None)},       # same snapshot again: nothing changes
        base | {5: (5, "Bergen", "c5@x")},     # equal to the old version's email, not the current one's
        base | {5: (5, "Bergen", "c5@x")} | {301: (301, "Rome", "n@x")},
    ]
    details = []
    for i, snap in enumerate(snapshots):
        for d in (new, old):
            res = d.load(list(snap.values()), DAY1 + dt.timedelta(days=i))
            if d is new:
                details.append(next(s.detail for s in res[1].steps if s.name == "write"))
    for name in ("bronze.dim1", "bronze.dim2"):
        assert new.table(name).equals(old.table(name)), name
    assert "299 rows unchanged" in details[1] and "for 1 business key" in details[1]
    assert details[2].startswith("300 rows unchanged; nothing to write")
    assert "Type 1 updates" in details[3] and "for 1 business key" in details[3]
    assert "1 new" in details[4] and "for 1 business key" in details[4]
    # Type 1 dimensions take every arriving row, so a full snapshot is rewritten, and says why.
    assert "most of it changed" in next(s.detail for s in new.load(list(base.values()), DAY3)[0].steps
                                        if s.name == "write")


def test_a_dimension_merge_never_reuses_a_key_another_writer_handed_out_meanwhile(tmp_path, monkeypatch):
    d = Dims(tmp_path, True)
    d.load([(i, "Oslo", f"c{i}@x") for i in range(1, 51)], DAY1)
    real, sneaked = d.store.upsert, []

    def someone_adds_a_member_first(lakehouse, schema, table, df, on, *args, **kwargs):
        if table == "dim1" and not sneaked:
            sneaked.append(1)
            now = d.table("bronze.dim1")
            theirs = now.filter(pl.col("customer_id") == 1).with_columns(
                pl.lit(400, dtype=pl.Int64).alias("customer_id"),
                pl.lit(int(now["cust_sk"].max()) + 1, dtype=pl.Int64).alias("cust_sk"))
            d.store.write(lakehouse, schema, table, theirs, mode="append")  # another run added customer 400
        try:
            return real(lakehouse, schema, table, df, on, *args, **kwargs)
        except CommitFailedError:
            conflicts.append(table)
            raise
    conflicts: list = []
    monkeypatch.setattr(d.store, "upsert", someone_adds_a_member_first)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda _: None)
    d.load([(300, "Rome", "c300@x")], DAY2)
    t = d.table("bronze.dim1")
    assert {300, 400} <= set(t["customer_id"].to_list())
    assert not t["cust_sk"].is_duplicated().any()
    assert conflicts == ["dim1"]  # the MERGE saw the other writer's key, failed, and ran again


def test_a_renamed_watermark_filters_the_source_and_a_keyless_table_appends_only_new_rows(tmp_path):
    s = Setup(tmp_path, True, keys=False)
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = {json.dumps(s.src.as_posix())}
        rename = {{ ModifiedDate = "modified" }}
        [load]
        mode = "incremental"
        watermark = "modified"
        [contract.columns]
        id = "int64"
        name = "string"
        amount = "float64"
        modified = "date"
    """)
    s.source(rows(range(3), DAY1).rename({"modified": "ModifiedDate"}))
    s.run()
    s.source(rows([3], DAY2).rename({"modified": "ModifiedDate"}))
    res = s.run(day=DAY2)
    read = next(x for x in res.steps if x.name == "read")
    assert read.rows == 1 and "incremental, modified after 2026-10-01" in read.detail
    assert s.table()["id"].to_list() == [0, 1, 2, 3]  # nothing appended twice
    s.run(day=DAY3)
    assert s.table().height == 4


def test_a_watermark_named_as_the_source_names_it_before_the_rename_is_refused(tmp_path):
    from fabric_desktop.meta.project import validate
    s = Setup(tmp_path, True, keys=False)
    path = s.root / "metadata" / "entities" / "bronze.events.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('watermark = "modified"', 'watermark = "ModifiedDate"')
                    .replace("[load]", 'rename = { ModifiedDate = "modified" }\n[load]'), encoding="utf-8")
    errors = validate(Project(s.root), DAY1)
    assert ('bronze.events: the watermark is a contract column: the source renames ModifiedDate to '
            'modified, so use watermark = "modified"') in errors
