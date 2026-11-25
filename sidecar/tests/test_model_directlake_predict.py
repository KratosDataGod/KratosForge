"""The Direct Lake fallback predictor (Preview): one verdict for the model, causes that don't depend on size, a
forecast from the Delta log's history, and the smallest capacity that fits. Pure tests over model.bim
dictionaries and numbers, the Delta history read from local tables, and the check's service with a stand-in
Fabric. Nothing reaches a tenant, and no test measures time."""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import auth
from fabric_desktop import model_directlake as dl
from fabric_desktop import model_directlake_service as mds
from tests.test_model_directlake import svc  # noqa: F401 - the service with a stand-in Fabric and local tables

GB = 1024 ** 3
DAY = 86_400_000
SQL = 'let\n    database = Sql.Database("abc", "SQL1")\nin\n    database'
ONELAKE = ('AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/11111111-1111-1111-1111-111111111111/'
           '22222222-2222-2222-2222-222222222222", [HierarchicalNavigation=true])')
MOCKUP = ["fact_sales", "fact_returns", "dim_customer", "dim_product", "v_sales_targets", "dim_date"]
FIXTURE = Path(__file__).resolve().parents[2] / "app" / "tests" / "browser" / "directlake-risk.json"
NEVER = re.compile(r"\b(does|did) fall back|fell back|has fallen back|is falling back", re.I)


def _bim(names=("FactSales", "DimCustomer"), behavior=None, source=SQL):
    def table(n):
        return {"name": n, "columns": [{"name": "k", "dataType": "int64", "sourceColumn": "k"}],
                "partitions": [{"name": n, "mode": "directLake", "source": {
                    "type": "entity", "entityName": n.lower(), "schemaName": "gold", "expressionSource": "Q"}}]}
    model = {"tables": [table(n) for n in names], "expressions": [{"name": "Q", "kind": "m", "expression": source}]}
    if behavior:
        model["directLakeBehavior"] = behavior
    return {"name": "Sales", "model": model}


def _s(rows=1_000, files=10, row_groups=10, size=GB // 10, estimated=False):
    return dl.TableStats([("k", "long")], rows=rows, files=files, bytes=size, row_groups=row_groups,
                         row_groups_estimated=estimated)


def _small():
    return {"FactSales": _s(), "DimCustomer": _s(rows=50, files=1, row_groups=1)}


def _t(out, name):
    return next(t for t in out["tables"] if t["table"] == name)


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _line(start, per_day, n, every=1, first=0):
    """n commits, one every ``every`` days, files growing by ``per_day``."""
    return [dl.Commit(first + i, (first + i * every) * DAY, start + round(per_day * i * every), None) for i in range(n)]


# ---- the guardrail table

def test_the_guardrail_table_is_pinned_to_microsofts_list():
    """A reviewed change: these are the numbers at the source link in model_directlake's docstring."""
    small = (1_000, 1_000, 300_000_000)
    assert dl.GUARDRAILS == {
        "F2": (*small, 10 * GB), "F4": (*small, 10 * GB), "F8": (*small, 10 * GB), "F16": (*small, 20 * GB),
        "F32": (*small, 40 * GB), "F64": (5_000, 5_000, 1_500_000_000, None),
        "F128": (5_000, 5_000, 3_000_000_000, None), "F256": (5_000, 5_000, 6_000_000_000, None),
        "F512": (10_000, 10_000, 12_000_000_000, None), "F1024": (10_000, 10_000, 24_000_000_000, None),
        "F2048": (10_000, 10_000, 24_000_000_000, None), "F4096": (10_000, 10_000, 24_000_000_000, None),
        "F8192": (10_000, 10_000, 24_000_000_000, None)}
    assert dl.ALIASES == {"FT1": "F64", "P1": "F64", "P2": "F128", "P3": "F256", "P4": "F512", "P5": "F1024"}
    assert "learn.microsoft.com/fabric/fundamentals/direct-lake-overview#fabric-capacity-guardrails" in dl.__doc__


def test_the_docs_limits_table_says_what_the_guardrail_table_says():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "direct-lake-check.md").read_text(encoding="utf-8")
    words = {"million": 10 ** 6, "billion": 10 ** 9}
    seen = {}
    for line in doc.splitlines():
        m = re.match(r"\| (F[0-9][^|]*) \| ([0-9,]+) \| ([0-9,]+) \| ([0-9.]+) (million|billion) \| ([^|]+) \|", line)
        if not m:
            continue
        files, groups = int(m[2].replace(",", "")), int(m[3].replace(",", ""))
        rows = round(float(m[4]) * words[m[5]])
        size = None if m[6].strip() == "No limit" else int(m[6].split()[0]) * GB
        for sku in re.findall(r"F[0-9]+", m[1].split("(")[0]):
            seen[sku] = (files, groups, rows, size)
    assert seen and all(dl.GUARDRAILS[s] == v for s, v in seen.items())


# ---- one verdict for the model

@pytest.mark.parametrize("stats,security,verdict", [
    ({"FactSales": _s(files=1_200), "DimCustomer": _s()}, {}, "will"),
    ({"FactSales": _s(files=950), "DimCustomer": _s()}, {}, "might"),
    ({"FactSales": _s(), "DimCustomer": _s()}, {}, "within"),
    ({"FactSales": _s()}, {}, "not_checked"),
], ids=["will", "might", "within", "unread"])
def test_golden_model_verdicts(stats, security, verdict):
    out = dl.predict(_bim(), stats, None, "F32", endpoint_security=security)
    assert out["model"]["verdict"] == verdict
    if verdict == "might":
        assert out["model"]["message"] == "FactSales is within 10% of F32's Parquet file limit, so a few more loads could pass it."
    assert out["label"] == "Predicted from metadata on this PC. Fabric hasn't run a query."
    assert out["model"]["label"] == {"will": "Will fall back", "might": "Might fall back", "within": "Within limits",
                                     "not_checked": "Not checked"}[verdict]


def test_a_table_nobody_queries_still_sends_the_whole_model_to_directquery():
    bim = _bim(("FactSales", "DimCustomer", "OldSnapshot"))
    stats = _small() | {"OldSnapshot": _s(row_groups=1_240)}
    out = dl.predict(bim, stats, None, "F32", endpoint_security={})
    assert out["model"]["verdict"] == "will" and out["model"]["table"] == "OldSnapshot"
    assert "sends every query on the model to DirectQuery, including queries that don't read OldSnapshot" in out["model"]["message"]
    assert _t(out, "FactSales")["status"] == "within"


def test_the_deciding_table_is_the_one_furthest_past_a_limit():
    stats = {"FactSales": _s(files=1_100), "DimCustomer": _s(rows=600_000_000)}
    out = dl.predict(_bim(), stats, None, "F32", endpoint_security={})
    assert out["model"]["table"] == "DimCustomer" and "row limit" in out["model"]["message"]


@pytest.mark.parametrize("behavior,source,label,says", [
    (None, SQL, "Will fall back", "including queries that don't read"),
    ("DirectLakeOnly", SQL, "Will fail", "will fail instead of going to DirectQuery"),
    ("DirectQueryOnly", SQL, "Will fall back", "every query goes to DirectQuery anyway"),
    (None, ONELAKE, "Will fail", "refresh will fail"),
], ids=["automatic", "dl-only", "dq-only", "onelake"])
def test_the_behavior_setting_says_what_past_a_limit_means(behavior, source, label, says):
    out = dl.predict(_bim(behavior=behavior, source=source), _small() | {"FactSales": _s(files=2_000)}, None, "F32",
                     endpoint_security={})
    assert out["model"]["label"] == label and says in out["model"]["message"]
    assert out["behavior_meaning"] == dl.behavior_meaning(out["behavior"], out["kind"])[1]


def test_without_a_capacity_nothing_is_judged_by_size():
    out = dl.predict(_bim(), _small(), None, "")
    assert out["model"]["verdict"] == "not_checked" and "Pick the capacity" in out["model"]["message"]
    assert {t["status"] for t in out["tables"]} == {"not_checked"}


def test_the_model_size_decides_when_no_table_is_past_a_limit():
    stats = {"FactSales": _s(size=30 * GB), "DimCustomer": _s(size=12 * GB)}
    out = dl.predict(_bim(), stats, None, "F32", endpoint_security={})
    assert out["model"]["verdict"] == "will" and out["model"]["table"] == ""
    assert out["size"] == {"bytes": 42 * GB, "limit": 40 * GB, "level": "over"}
    assert dl.predict(_bim(), stats, None, "F64", endpoint_security={})["model"]["verdict"] == "within"


# ---- fallback that doesn't depend on size

def test_a_view_source_sends_the_queries_that_read_it_to_directquery():
    out = dl.predict(_bim(), {"FactSales": _s(), "DimCustomer": None}, None, "F64", endpoint_security={})
    view = _t(out, "DimCustomer")
    assert view["view"] and view["status"] == "will" and view["checks"][0]["check"] == "Source is a view"
    assert out["model"]["table"] == "DimCustomer" and "source is a SQL view" in out["model"]["message"]
    assert out["skus"]["every_sku"] == ["DimCustomer"], "no capacity fixes a view"
    off = dl.predict(_bim(behavior="DirectLakeOnly"), {"FactSales": _s(), "DimCustomer": None}, None, "F64",
                     endpoint_security={})
    assert "doesn't allow DirectQuery" in _t(off, "DimCustomer")["checks"][0]["message"]


def test_a_onelake_model_has_no_view_or_endpoint_security_causes():
    out = dl.predict(_bim(source=ONELAKE), {"FactSales": _s(), "DimCustomer": None}, None, "F64")
    missing = _t(out, "DimCustomer")
    assert out["kind"] == "onelake" and not missing["view"] and missing["causes"] == ["missing"]
    assert not [x for x in out["not_checked"] if "SQL endpoint" in x] and out["caveat"] == ""
    within = dl.predict(_bim(source=ONELAKE), _small(), None, "F64")
    assert within["model"]["verdict"] == "within"


CAVEAT = ("SQL endpoint security (row-level or object-level) can't be read from this PC, and it alone can make "
          "queries fall back.")


def test_endpoint_security_that_wasnt_read_is_within_limits_with_the_caveat():
    assert dl.ENDPOINT_SECURITY_UNREAD_VERDICT == "within", "pending Chris's pick; the other value is tested below"
    out = dl.predict(_bim(), _small(), None, "F64")
    assert out["model"]["verdict"] == "within" and out["model"]["label"] == "Within limits"
    assert out["caveat"] == CAVEAT and CAVEAT in out["not_checked"]
    assert {t["status"] for t in out["tables"]} == {"within"}


def test_the_switch_makes_unread_endpoint_security_not_checked(monkeypatch):
    monkeypatch.setattr(dl, "ENDPOINT_SECURITY_UNREAD_VERDICT", "not_checked")
    out = dl.predict(_bim(), _small(), None, "F64")
    assert out["model"]["verdict"] == "not_checked" and "security at the SQL analytics endpoint" in out["model"]["message"]
    assert out["caveat"] == CAVEAT and CAVEAT in out["not_checked"]
    assert {t["status"] for t in out["tables"]} == {"within"}, "each table's numbers are still judged"
    assert dl.predict(_bim(), _small(), None, "F64", endpoint_security={})["model"]["verdict"] == "within", \
        "security that was read and found none is within limits either way"


@pytest.mark.parametrize("behavior,source,says", [
    (None, SQL, "Past this limit, every query on this model goes to DirectQuery, not just queries on this table."),
    ("DirectLakeOnly", SQL, "Past this limit, queries on this model fail instead of falling back."),
    ("DirectQueryOnly", SQL, "The model is set to DirectQuery only, so this limit doesn't change where its queries run."),
    (None, ONELAKE, "Past this limit, refresh of this model fails, because Direct Lake on OneLake doesn't fall back."),
], ids=["automatic", "dl-only", "dq-only", "onelake"])
def test_a_tables_check_says_what_its_limit_means_for_the_whole_model(behavior, source, says):
    out = dl.predict(_bim(behavior=behavior, source=source), _small() | {"FactSales": _s(row_groups=1_240)}, None, "F32")
    groups = next(c for c in _t(out, "FactSales")["checks"] if c["check"] == "Row groups")
    assert groups["message"] == f"The table has more row groups than F32 allows. Read from the Delta log and Parquet footers. {says}"
    assert not [s for s in _strings(out) if "Queries on this table go to DirectQuery" in s]


@pytest.mark.parametrize("kind", ["rls", "ols"])
def test_endpoint_security_on_a_table_sends_its_queries_to_directquery(kind):
    out = dl.predict(_bim(), _small(), None, "F64", endpoint_security={kind: ["DimCustomer"]})
    assert _t(out, "DimCustomer")["status"] == "will" and out["model"]["table"] == "DimCustomer"
    assert "security on DimCustomer" in out["model"]["message"]
    assert out["caveat"] == "" and CAVEAT not in out["not_checked"]


# ---- the forecast

def test_linear_growth_gives_the_day_the_limit_is_passed():
    commits = _line(800, 10, 17)  # 800 files on day 0, 960 on day 16
    out = dl.predict(_bim(), _small() | {"FactSales": _s(files=960)}, {"FactSales": commits}, "F32", endpoint_security={})
    f = _t(out, "FactSales")["forecast"]
    # 1,000 on day 4 is still inside; 1,010 on day 5 is past it.
    assert (f["status"], f["days"], f["metric"]) == ("crossing", 5, "files")
    assert f["message"] == "Crosses F32's Parquet file limit in about 5 days at the current rate (17 commits over 16 days)."
    assert _t(out, "FactSales")["at_horizon"]["files"] == 960 + 900


def test_shrinking_after_optimize_is_no_growth():
    commits = _line(100, 50, 10) + [dl.Commit(10, 10 * DAY, 12, None)]
    out = dl.predict(_bim(), _small() | {"FactSales": _s(files=12)}, {"FactSales": commits}, "F32", endpoint_security={})
    f = _t(out, "FactSales")["forecast"]
    assert f["status"] == "no_growth" and "since the last compaction" in f["message"]
    assert _t(out, "FactSales")["at_horizon"]["files"] == 12


@pytest.mark.parametrize("commits,status", [
    (_line(10, 5, 2, every=10), "not_enough"),
    (_line(10, 5, 3, every=3), "not_enough"),   # 3 commits over 6 days
    (_line(10, 5, 3, every=4), "growing"),      # 3 commits over 8 days
    ([], "not_enough"),
], ids=["two-commits", "six-days", "eight-days", "none"])
def test_a_forecast_needs_three_commits_over_seven_days(commits, status):
    g = dl.growth([(c.timestamp, c.files) for c in commits])
    assert g["status"] == status and g["commits"] == len(commits)


def test_too_little_history_says_how_much_was_used():
    out = dl.predict(_bim(), _small(), {"FactSales": _line(10, 1, 2)}, "F32", endpoint_security={})
    assert _t(out, "FactSales")["forecast"]["message"].startswith("Not enough history (2 commits over 1 day")
    assert "2 tables had no forecast, because too little history was read. At the horizon, they're counted at today's " \
        "numbers." in out["notes"]
    assert "Model size at the horizon is counted at today's size, because the forecast covers only files, row groups " \
        "and rows." in out["notes"]


def test_growing_again_too_briefly_after_a_compaction_is_not_enough_history():
    commits = _line(100, 50, 10) + [dl.Commit(10, 10 * DAY, 12, None), dl.Commit(11, 11 * DAY, 40, None)]
    assert dl.growth([(c.timestamp, c.files) for c in commits])["status"] == "not_enough"


def test_rows_a_writer_didnt_count_are_not_checked():
    out = dl.predict(_bim(), _small() | {"FactSales": _s(rows=None)}, None, "F32", endpoint_security={})
    rows = next(c for c in _t(out, "FactSales")["checks"] if c["check"] == "Rows")
    assert rows["message"] == "Not checked, because the Delta log doesn't hold every file's row count."


def test_rows_forecast_only_from_commits_whose_rows_are_known():
    commits = [dl.Commit(i, i * DAY, 1, None if i < 5 else 1_000 * i) for i in range(10)]
    g = dl.growth([(c.timestamp, c.rows) for c in commits])
    assert g["commits"] == 5 and g["status"] == "not_enough", "4 days of known rows"


def test_row_groups_follow_the_files_at_todays_row_groups_per_file():
    commits = _line(400, 20, 20)
    out = dl.predict(_bim(), _small() | {"FactSales": _s(files=780, row_groups=1_560)}, {"FactSales": commits}, "F64",
                     endpoint_security={})
    assert _t(out, "FactSales")["at_horizon"]["row_groups"] == round((780 + 20 * 90) * 2)


# ---- the boundary, the SKUs, and determinism

def test_exactly_at_the_limit_is_still_inside():
    at = dl.predict(_bim(), _small() | {"FactSales": _s(files=5_000)}, None, "F64", endpoint_security={})
    files = next(c for c in _t(at, "FactSales")["checks"] if c["check"] == "Parquet files")
    assert files["level"] == "near" and _t(at, "FactSales")["status"] == "might"
    past = dl.predict(_bim(), _small() | {"FactSales": _s(files=5_001)}, None, "F64", endpoint_security={})
    assert _t(past, "FactSales")["status"] == "will"
    assert "1 more Parquet file would" in next(c["message"] for c in _t(dl.predict(
        _bim(), _small() | {"FactSales": _s(files=1_000)}, None, "F32", endpoint_security={}), "FactSales")["checks"]
        if c["check"] == "Parquet files")


@pytest.mark.parametrize("alias,sku", sorted(dl.ALIASES.items()))
def test_premium_and_trial_skus_are_judged_as_their_f_sku(alias, sku):
    stats = _small() | {"FactSales": _s(rows=2_000_000_000)}
    assert dl.predict(_bim(), stats, None, alias) == dl.predict(_bim(), stats, None, sku)
    assert alias in next(r for r in dl.predict(_bim(), stats, None, sku)["skus"]["rows"] if r["sku"] == sku)["aliases"]


def test_the_what_if_names_the_smallest_capacity_today_and_at_the_horizon():
    stats = _small() | {"FactSales": _s(rows=412_000_000, files=900)}
    growing = {"FactSales": _line(500, 10, 41)}  # 10 files a day: 1,800 in 90 days
    out = dl.predict(_bim(), stats, growing, "F32", endpoint_security={})
    assert (out["skus"]["smallest_today"], out["skus"]["smallest_horizon"]) == ("F64", "F64")
    faster = {"FactSales": _line(500, 50, 41)}  # 50 a day: 5,400 in 90 days
    out = dl.predict(_bim(), stats, faster, "F32", endpoint_security={})
    assert (out["skus"]["smallest_today"], out["skus"]["smallest_horizon"]) == ("F64", "F512")
    assert [r["sku"] for r in out["skus"]["rows"]] == list(dl.GUARDRAILS)
    assert not [s for s in _strings(out) if re.search(r"\bCU\b|\$|cost", s)], "no cost or CU figure"


def test_the_prediction_is_deterministic_and_doesnt_depend_on_the_history_order():
    commits = _line(100, 7, 30)
    shuffled = commits[:]
    random.Random(4).shuffle(shuffled)
    a = dl.predict(_bim(), _small(), {"FactSales": commits}, "F32")
    b = dl.predict(_bim(), _small(), {"FactSales": shuffled}, "F32")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_large_model_is_judged_by_count_not_by_the_clock():
    names = [f"T{i:04d}" for i in range(1_500)]
    stats = {n: _s(files=i % 1_100) for i, n in enumerate(names)}
    history = {n: _line(1, 1, 200) for n in names[:100]}
    out = dl.predict(_bim(names), stats, history, "F32", endpoint_security={})
    assert len(out["tables"]) == 1_500 and sum(out["counts"].values()) == 1_500
    assert out["counts"]["will"] == sum(1 for i in range(1_500) if i % 1_100 > 1_000)


@pytest.mark.parametrize("behavior", [None, "DirectLakeOnly", "DirectQueryOnly"])
@pytest.mark.parametrize("source", [SQL, ONELAKE], ids=["sql", "onelake"])
def test_it_never_says_a_model_does_fall_back(behavior, source):
    for stats in (_small(), {"FactSales": _s(files=5_001), "DimCustomer": None}, {}):
        for sku in ("F2", "F64", ""):
            out = dl.predict(_bim(behavior=behavior, source=source), stats, {"FactSales": _line(1, 9, 20)}, sku)
            assert not [s for s in _strings(out) if NEVER.search(s)]
            assert out["label"].startswith("Predicted from metadata on this PC")
            assert "SQL1" not in json.dumps(out) and "Sql.Database" not in json.dumps(out), "no source expression"


def test_predicting_does_not_change_the_model():
    bim = _bim()
    before = json.dumps(bim, sort_keys=True)
    dl.predict(bim, _small(), None, "F32")
    assert json.dumps(bim, sort_keys=True) == before


# ---- the Delta history, read on this PC

def _append(uri, n):
    write_deltalake(uri, pa.table({"k": list(range(n))}), mode="append")


def test_history_rebuilds_files_and_rows_after_each_commit(tmp_path):
    uri = str(tmp_path / "t")
    for n in (10, 11, 12, 13):
        _append(uri, n)
    DeltaTable(uri).optimize.compact()
    _append(uri, 1)
    commits = mds.history(uri, None)
    assert [(c.version, c.files, c.rows) for c in commits] == [
        (0, 1, 10), (1, 2, 21), (2, 3, 33), (3, 4, 46), (4, 1, 46), (5, 2, 47)]
    assert all(b.timestamp >= a.timestamp for a, b in zip(commits, commits[1:], strict=False))


def test_history_is_bounded_and_stops_where_the_log_was_cleaned_up(tmp_path):
    uri = str(tmp_path / "t")
    for n in range(6):
        _append(uri, n + 1)
    assert [c.version for c in mds.history(uri, None, max_commits=2)] == [4, 5]
    DeltaTable(uri, version=3).create_checkpoint()  # log retention keeps the checkpoint and drops what's before it
    for v in range(3):
        (tmp_path / "t" / "_delta_log" / f"{v:020d}.json").unlink()
    assert [c.version for c in mds.history(uri, None)] == [3, 4, 5], "only commits actually read"
    assert mds.history(str(tmp_path / "none"), None) is None



def test_history_stops_at_a_commit_file_too_big_to_read(tmp_path, monkeypatch):
    uri = str(tmp_path / "t")
    for n in range(4):
        _append(uri, n + 1)
    log = tmp_path / "t" / "_delta_log"
    limit = max((log / f"{v:020d}.json").stat().st_size for v in range(4))
    monkeypatch.setattr(mds, "LOG_FILE_BYTES", limit)
    padded = log / f"{1:020d}.json"
    padded.write_bytes(padded.read_bytes() + b"\n" * (limit + 1 - padded.stat().st_size))
    assert [c.version for c in mds.history(uri, None)] == [2, 3], "the oversized commit ends the history"

def test_rows_before_a_compaction_whose_files_werent_read_are_unknown(tmp_path):
    uri = str(tmp_path / "t")
    for n in (5, 6, 7):
        _append(uri, n)
    DeltaTable(uri).optimize.compact()
    _append(uri, 1)
    commits = mds.history(uri, None, max_commits=2)
    assert [(c.version, c.files, c.rows) for c in commits] == [(3, 1, 18), (4, 2, 19)]
    window = mds.history(uri, None, max_commits=3)
    assert window[0].version == 2 and window[0].rows is None, "the removed files were added before the window"


def test_history_writes_nothing(tmp_path):
    uri = str(tmp_path / "t")
    for n in (1, 2, 3):
        _append(uri, n)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "t").rglob("*") if p.is_file()}
    mds.history(uri, None)
    assert before == {p: p.stat().st_mtime_ns for p in (tmp_path / "t").rglob("*") if p.is_file()}


# ---- the check's RPC carries the prediction

def test_the_check_carries_the_prediction_for_every_sku(svc):  # noqa: F811 - the fixture
    out = svc.meta_model_directlake_check("test", "SM_Gold")
    p = out["prediction"]
    assert p["sku"] == "F2" and p["kind"] == "sql" and p["label"].startswith("Predicted from metadata")
    assert set(p["by_sku"]) == set(dl.SKUS) and p["by_sku"]["P1"] == p["by_sku"]["F64"]
    assert p["by_sku"]["F2"]["model"] == p["model"]
    assert _t(p, "FactSales")["forecast"]["message"].startswith("Not enough history (1 commit")
    assert p["model"]["verdict"] == "within" and p["caveat"] == CAVEAT, "the endpoint's security isn't read from this PC"


def test_without_a_onelake_sign_in_the_tables_are_not_read(svc, monkeypatch):  # noqa: F811 - the fixture
    def refused(*a):
        raise auth.SignInRequired("Sign in first")
    monkeypatch.setattr(svc, "_directlake_uri", refused)
    out = svc.meta_model_directlake_check("test", "SM_Gold")
    p = out["prediction"]
    assert p["read_problem"] == "no_signin" and p["counts"]["not_checked"] == 2
    assert [u["reason"] for u in out["unread"]] == ["There's no OneLake sign-in on this PC."] * 2
    assert p["not_checked"][0].startswith("The tables' Delta logs, because there's no OneLake sign-in")


def test_a_table_whose_history_cant_be_read_still_gets_todays_verdict(svc, monkeypatch):  # noqa: F811 - the fixture
    def broken(*a, **k):
        raise OSError("log unreadable")
    monkeypatch.setattr(mds, "history", broken)
    p = svc.meta_model_directlake_check("test", "SM_Gold")["prediction"]
    assert {t["forecast"]["status"] for t in p["tables"]} == {"no_history"}
    assert {t["status"] for t in p["tables"]} == {"within"}


def test_the_history_budget_is_shared_by_the_models_tables():
    assert mds.HISTORY_COMMITS == 200 and mds.HISTORY_BUDGET == 2_000 and mds.MIN_HISTORY == 20


# ---- the card's browser test reads the engine's own output for the mockup's states

def _mockup(state):
    bim = _bim(MOCKUP, behavior="DirectLakeOnly" if state == "dlonly" else None)
    for t in bim["model"]["tables"]:
        t["partitions"][0]["source"]["entityName"] = t["name"]
    stats = {"fact_sales": _s(412_000_000, 840, 1_240, 30 * GB), "fact_returns": _s(31_000_000, 960, 610, 5 * GB),
             "dim_customer": _s(8_200_000, 12, 14, GB // 2), "dim_product": _s(48_000, 3, 3, GB // 4),
             "v_sales_targets": None, "dim_date": _s(7_305, 1, 1, GB // 4)}
    history = {"fact_returns": [dl.Commit(i, 1_795_000_000_000 + i * DAY, 900 + i * 4, 31_000_000) for i in range(16)],
               "dim_date": [dl.Commit(0, 1_795_000_000_000, 1, 7_305)]}
    unread = []
    if state == "nc":
        stats, history = {}, {}
        unread = [{"table": n, "reason": "There's no OneLake sign-in on this PC."} for n in MOCKUP]
    lim = dl.limits("F32")
    check = dl.check(bim, stats, lim)
    prediction = mds.predict_every_sku(bim, stats, history, "F32", layer="gold",
                                       read_problem="no_signin" if state == "nc" else "")
    prediction["by_sku"] = {k: v for k, v in prediction["by_sku"].items() if k in ("F32", "F64")}  # what the card test picks
    return {"env": "test", "name": "Sales", "workspace_name": "Sales Test", "checked_at": "2026-11-24T08:00:00+00:00",
            "sku": "F32", "direct_lake": True, "unread": unread, "keys_checked": False, "prediction": prediction} | check


def test_the_cards_browser_fixture_is_the_engines_output():
    """REGENERATE_DL_FIXTURE=1 rewrites app/tests/browser/directlake-risk.json after a reviewed change."""
    fresh = {s: _mockup(s) for s in ("f32", "dlonly", "nc")}
    text = json.dumps(fresh, indent=1, sort_keys=True) + "\n"
    if os.environ.get("REGENERATE_DL_FIXTURE"):
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text
    f32 = fresh["f32"]["prediction"]
    assert f32["counts"] == {"will": 2, "might": 1, "within": 3, "not_checked": 0}, "the mockup's F32 numbers"
    assert f32["by_sku"]["F64"]["counts"] == {"will": 1, "might": 0, "within": 5, "not_checked": 0}
    assert f32["size"] == {"bytes": 36 * GB, "limit": 40 * GB, "level": "near"}
