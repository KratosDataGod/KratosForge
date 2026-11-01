"""Compare before promote, quarantine replay and freshness promises."""

import datetime as dt
import json
import shutil
import time
import types

import polars as pl
import pytest
import requests

from fabric_desktop import dataops_service
from fabric_desktop.meta import freshness
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


def edit_entity(svc, name, old, new):
    text = svc.meta_entity(name)["text"]
    assert old in text
    return svc.meta_save_entity(name, text.replace(old, new, 1))


# ---- compare

def test_compare_dev_with_test_shows_what_a_logic_change_does(svc):
    svc.meta_set_environment("test", target="local")
    svc.meta_promote("dev", "test")
    data = svc._project().local_data  # Test reads the same landing files as Dev
    shutil.copytree(data / "dev" / "LH_Bronze" / "Files", data / "test" / "LH_Bronze" / "Files")
    assert all(r["status"] == "ok" for r in svc.meta_run(env="test"))
    assert {t["state"] for t in svc.meta_promote_impact("dev", "test")["tables"]} == {"same"}

    edit_entity(svc, "silver.orders", 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    impact = {t["entity"]: t["state"] for t in svc.meta_promote_impact("dev", "test")["tables"]}
    assert impact["silver.orders"] == "changed" and impact["silver.customers"] == "same"
    svc.meta_run(["silver.orders"], backfill="full")

    out = svc.meta_compare("silver.orders", "test", "dev")
    assert out["compared_on_keys"] and out["rows"]["left"] == out["rows"]["right"]
    assert set(out["changed"]["by_column"]) == {"net_amount"}  # surrogate keys and run stamps are left out
    assert out["only_left"]["count"] == out["only_right"]["count"] == 0
    change = out["changed"]["rows"][0]["changes"]
    assert change[0]["column"] == "net_amount" and change[0]["left"] != change[0]["right"]
    totals = {t["column"]: t for t in out["totals"]}
    assert totals["net_amount"]["diff"] > 0 and totals["amount"]["diff"] == 0
    assert "order_id" not in totals and "customer_id" not in totals


def test_compare_with_an_earlier_version_of_the_same_table(svc):
    before = svc.meta_versions("silver.customers")[0]["version"]
    proj = svc._project()
    store = svc._store(proj, "dev")
    df = store.read("LH_Silver", "dbo", "customers")
    store.write("LH_Silver", "dbo", "customers", df.filter(pl.col("customer_id") != 1), mode="overwrite")
    out = svc.meta_compare("silver.customers", "dev", "dev", left_version=before)
    assert out["only_left"]["count"] == 1 and out["only_left"]["rows"][0]["customer_id"] == "1"
    assert out["rows"]["left"] - out["rows"]["right"] == 1


def test_compare_of_a_table_above_the_row_limit_streams_a_checksum(svc, monkeypatch):
    # PB-069: a table larger than memory is never loaded; the result says it was a hashed compare.
    from fabric_desktop.meta import compare as compare_mod
    svc.meta_set_environment("test", target="local")
    svc.meta_promote("dev", "test")
    data = svc._project().local_data
    shutil.copytree(data / "dev" / "LH_Bronze" / "Files", data / "test" / "LH_Bronze" / "Files")
    assert all(r["status"] == "ok" for r in svc.meta_run(env="test"))
    monkeypatch.setattr(compare_mod, "LIMIT_ROWS", 1)
    monkeypatch.setattr(compare_mod, "compare", lambda *a: pytest.fail("the full compare loads both tables"))
    monkeypatch.setattr(type(svc), "_table", lambda *a: pytest.fail("the table was read into memory"))

    same = svc.meta_compare("silver.orders", "test", "dev")
    assert same["method"] == "hashed" and same["checksum"]["match"] and not same["compared_on_keys"]
    assert same["rows"]["left"] == same["rows"]["right"] > 1
    assert "checksums match" in same["note"]
    assert all(t["diff"] == 0 for t in same["totals"]) and "order_id" not in {t["column"] for t in same["totals"]}

    edit_entity(svc, "silver.orders", 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    svc.meta_run(["silver.orders"], backfill="full")
    moved = svc.meta_compare("silver.orders", "test", "dev")
    assert moved["method"] == "hashed" and not moved["checksum"]["match"]
    assert {t["column"]: t for t in moved["totals"]}["net_amount"]["diff"] > 0
    assert "checksums differ" in moved["note"]


def test_hashed_compare_matches_equal_numbers_of_another_type_and_skips_old_versions(svc):
    import pyarrow as pa
    import pyarrow.dataset as pads
    from fabric_desktop.meta import compare as compare_mod
    e = svc._project().entities["silver.customers"]
    key = e.keys[0]
    left = pa.table({key: ["1", "2", "3"], "amount": pa.array([10, 20, 30], pa.int64()), "is_current": [True] * 3})
    right = pa.table({key: ["3", "1", "2", "2"], "amount": pa.array([30.0, 10.0, 99.0, 20.0]),
                      "is_current": [True, True, False, True]})
    out = compare_mod.hashed(e, pads.dataset(left), pads.dataset(right))
    assert out["checksum"]["match"] and out["rows"] == {"left": 3, "right": 3}
    assert [c["column"] for c in out["columns"]["type_changed"]] == ["amount"] and out["totals"][0]["diff"] == 0
    changed = compare_mod.hashed(e, pads.dataset(left), pads.dataset(right.set_column(1, "amount", pa.array([30.0, 11.0, 99.0, 20.0]))))
    assert not changed["checksum"]["match"] and changed["totals"][0]["diff"] == 1


def test_compare_below_the_row_limit_says_every_row_was_compared(svc):
    out = svc.meta_compare("silver.customers", "dev", "dev")
    assert out["method"] == "full" and out["compared_on_keys"]


# ---- quarantine replay

def test_fix_and_replay_a_quarantined_row(svc):
    q = svc.meta_quarantine("silver.orders")
    assert q["total"] == 1 and q["checks"] == {"returns_are_negative": 1}
    assert "gold.fact_sales" in q["downstream"] and "amount" in q["editable"]
    row = q["rows"][0]
    assert row["order_id"] == "105"

    # Replayed as it is, the row fails the same check again and stays held.
    again = svc.meta_replay("silver.orders", "dev", [row["_qid"]])
    assert again["status"] == "ok" and again["held"] == 1 and again["rows_written"] == 0
    held = svc.meta_quarantine("silver.orders")["rows"][0]
    assert held["order_id"] == "105" and held["_qid"] != row["_qid"]

    # Fixed by hand (a return is a negative amount), it loads, and net_amount is worked out again.
    amount = -abs(float(held["amount"]))
    res = svc.meta_replay("silver.orders", "dev", [held["_qid"]], {held["_qid"]: {"amount": str(amount)}})
    assert res["status"] == "ok" and res["rows_written"] == 1 and res["held"] == 0
    assert svc.meta_quarantine("silver.orders")["total"] == 0
    proj = svc._project()
    orders = svc._store(proj, "dev").read("LH_Silver", "dbo", "orders")
    r105 = orders.filter(pl.col("order_id") == 105)
    assert r105.height == 1 and r105["amount"].item() == amount
    assert r105["net_amount"].item() == pytest.approx(amount - (r105["discount"].item() or 0))
    assert r105["order_sk"].item() > 0  # the key it was given before it was held
    log = svc.meta_runs("dev")[0]
    assert log["backfill"] == "replay" and log["entity"] == "silver.orders"
    # The watermark stays where the normal runs left it.
    wm = svc._store(proj, "dev").meta("watermarks").filter(pl.col("entity") == "silver.orders")
    assert wm["run_id"][-1] != log["run_id"]


def test_discard_quarantined_rows(svc):
    q = svc.meta_quarantine("silver.orders")
    assert svc.meta_discard_quarantine("silver.orders", "dev", [q["rows"][0]["_qid"]])["total"] == 0


def test_discard_keeps_rows_quarantined_meanwhile_and_old_rows_keep_their_ids(svc, monkeypatch):
    proj = svc._project()
    store = svc._store(proj, "dev")
    q = store.read("LH_Silver", "quarantine", "dbo_orders")
    # Rows held before ids existed: their ids come from their values, so they never move.
    store.write("LH_Silver", "quarantine", "dbo_orders", pl.concat([q.drop("_qid"), q.drop("_qid")
                .with_columns(pl.lit("other").alias("_check"))], how="diagonal_relaxed"))
    first = sorted(r["_qid"] for r in svc.meta_quarantine("silver.orders")["rows"])
    store.write("LH_Silver", "quarantine", "dbo_orders", store.read("LH_Silver", "quarantine", "dbo_orders").reverse())
    assert sorted(r["_qid"] for r in svc.meta_quarantine("silver.orders")["rows"]) == first
    # A run quarantines a row between the discard's read and its write: that row stays.
    real, raced = svc._quarantine_at, []

    def read_then_a_run_quarantines(*a):
        out = real(*a)
        if not raced:
            raced.append(True)
            store.write("LH_Silver", "quarantine", "dbo_orders",
                        q.with_columns(pl.lit("new-row").alias("_qid")), mode="append")
        return out
    monkeypatch.setattr(svc, "_quarantine_at", read_then_a_run_quarantines)
    left = svc.meta_discard_quarantine("silver.orders", "dev", first)
    assert [r["_qid"] for r in left["rows"]] == ["new-row"]


def test_replay_of_a_full_reload_table_adds_rows_without_replacing_the_table(svc):
    edit_entity(svc, "bronze.customers", "[contract]\n",
                '[[check]]\nname = "no_ada"\nexpr = "customer_id <> 1"\non_fail = "quarantine"\n\n[contract]\n')
    assert svc.meta_entity("bronze.customers")["mode"] == "full"
    assert svc.meta_run(["bronze.customers"])[0]["quarantined"] == 1
    proj = svc._project()
    before = svc._store(proj, "dev").read("LH_Bronze", "dbo", "customers").height
    q = svc.meta_quarantine("bronze.customers")
    assert q["total"] == 1
    edit_entity(svc, "bronze.customers", 'expr = "customer_id <> 1"', 'expr = "customer_id <> 999"')
    res = svc.meta_replay("bronze.customers", "dev", [q["rows"][0]["_qid"]])
    assert res["status"] == "ok" and res["rows_written"] == 1
    assert svc._store(proj, "dev").read("LH_Bronze", "dbo", "customers").height == before + 1


# ---- freshness

NOW = dt.datetime(2026, 10, 15, 9, 0).astimezone()


def run(hours_ago, status="ok", run_id="r1"):
    t = (NOW - dt.timedelta(hours=hours_ago)).astimezone(dt.timezone.utc)
    return {"status": status, "started_at": t.isoformat(), "seconds": 0, "run_id": run_id, "message": "boom"}


def test_freshness_within_hours():
    p = freshness.promise({"freshness": {"within_hours": 24}})
    assert p["envs"] == ["test", "prod"]
    assert freshness.status(p, [run(2)], NOW)["status"] == "fresh"
    late = freshness.status(p, [run(30)], NOW)
    assert late["status"] == "late" and "30 h ago" in late["reasons"][0]
    assert freshness.status(p, [], NOW)["status"] == "never"
    failed = freshness.status(p, [run(1, "failed", "r2"), run(3)], NOW)
    assert failed["status"] == "fresh" and failed["last_failed"] and failed["failed_run"] == "r2"


def test_freshness_by_a_time_of_day():
    p = freshness.promise({"freshness": {"by": "07:00"}})
    assert freshness.status(p, [run(1)], NOW)["status"] == "fresh"  # loaded at 08:00 today
    assert freshness.status(p, [run(12)], NOW)["status"] == "late"  # last loaded yesterday evening
    early = NOW.replace(hour=6)
    assert freshness.status(p, [run(9)], early)["status"] == "fresh"  # not 07:00 yet


def test_freshness_is_checked_by_the_save_gate_and_saved_from_the_app(svc):
    res = svc.meta_set_freshness("gold.fact_sales", within_hours=26, by="07:00", envs=["dev", "prod"])
    assert res["errors"] == [] and res["freshness"] == {"within_hours": 26, "by": "07:00", "envs": ["dev", "prod"]}
    assert "[freshness]" in res["text"]
    items = svc.meta_freshness()["items"]
    assert [(x["entity"], x["env"]) for x in items] == [("gold.fact_sales", "dev")]
    cleared = svc.meta_set_freshness("gold.fact_sales")
    assert "[freshness]" not in cleared["text"] and cleared["freshness"] is None

    bad = edit_entity(svc, "gold.fact_sales", 'name = "gold.fact_sales"', 'name = "gold.fact_sales"\n\n[freshness]\nby = "7am"\nenvs = ["qa"]')
    assert any("freshness by" in e for e in bad["errors"]) and any("freshness envs" in e for e in bad["errors"])


def test_late_tables_are_posted_to_teams_once(svc, monkeypatch):
    posts = []
    monkeypatch.setattr(dataops_service, "post_teams", lambda url, title, lines: posts.append((title, lines)))
    svc.meta_set_alerts("https://example.webhook.office.com/x")
    svc.meta_set_freshness("gold.fact_sales", within_hours=0.0001, envs=["dev"])
    time.sleep(0.5)  # 0.0001 h is 0.36 s
    out = svc.meta_freshness(notify=True)
    assert out["items"][0]["status"] == "late" and out["alerted"] == 1
    assert "gold.fact_sales" in posts[0][1][0]
    assert svc.meta_freshness(notify=True)["alerted"] == 0  # already told
    with pytest.raises(ValueError):
        svc.meta_set_alerts("http://not-secure")


def _teams_posts(svc, monkeypatch, url="https://example.webhook.office.com/x"):
    posts = []
    post = lambda url, title, lines: posts.append((title, lines))  # noqa: E731
    monkeypatch.setattr(dataops_service, "post_teams", post)
    monkeypatch.setattr("fabric_desktop.incident_service.post_teams", post)
    svc.meta_set_alerts(url)
    return posts


def _late(svc):
    svc.meta_set_freshness("gold.fact_sales", within_hours=0.0001, envs=["dev"])
    time.sleep(0.5)  # 0.0001 h is 0.36 s


def test_a_table_an_agent_posts_as_an_incident_is_not_posted_per_table(svc, monkeypatch):
    posts = _teams_posts(svc, monkeypatch)
    _late(svc)
    fresh = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=3)).isoformat()
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {"contract": 1, "kind": "agent_status", "agent": "etl-01", "host": "srv", "version": "0.21.0", "state": "running",
         "heartbeat_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stale_after": fresh, "schedules": [],
         "incidents": {"enabled": True, "seconds": 300}}).encode())
    out = svc.meta_freshness(notify=True)
    assert out["items"][0]["status"] == "late" and out["alerted"] == 0 and posts == []  # the agent posts it


def test_turning_preview_off_or_on_posts_a_late_table_once_and_its_recovery_once(svc, monkeypatch):
    posts = _teams_posts(svc, monkeypatch)
    _late(svc)
    svc.incidents_watch(notify=True)  # Preview on: its incident is posted
    assert len(posts) == 1 and posts[0][0].startswith("New: ")
    assert svc.meta_freshness(notify=True)["alerted"] == 0 and len(posts) == 1  # Preview off: not again
    svc.incidents_watch(notify=True)  # and on again
    assert len(posts) == 1
    svc.meta_set_freshness("gold.fact_sales", within_hours=24, envs=["dev"])
    svc.incidents_watch(notify=True)
    svc.incidents_watch(notify=True)
    assert len(posts) == 2 and posts[1][0].startswith("Recovered: ")


def test_a_table_posted_per_table_is_not_posted_again_when_preview_is_turned_on(svc, monkeypatch):
    posts = _teams_posts(svc, monkeypatch)
    _late(svc)
    assert svc.meta_freshness(notify=True)["alerted"] == 1 and len(posts) == 1  # Preview off
    opened = svc.incidents_watch(notify=True)["envs"][0]  # Preview on
    assert [c["change"] for c in opened["changes"]] == ["opened"] and len(posts) == 1  # the incident still opens
    svc.meta_set_freshness("gold.fact_sales", within_hours=24, envs=["dev"])
    svc.incidents_watch(notify=True)
    assert len(posts) == 2 and posts[1][0].startswith("Recovered: ")


def test_unreadable_incidents_still_post_the_late_table(svc, monkeypatch):
    posts = _teams_posts(svc, monkeypatch)
    _late(svc)
    def broken(env):
        raise OSError("LH_Meta can't be read")
    monkeypatch.setattr(svc, "_incident_log", broken)
    assert svc.meta_freshness(notify=True)["alerted"] == 1 and len(posts) == 1


def test_a_failed_freshness_post_keeps_the_webhook_signature_out(svc, monkeypatch):
    def down(url, title, lines):
        raise ConnectionError(f"Max retries exceeded with url: {url.split('.com', 1)[1]}")
    monkeypatch.setattr(dataops_service, "post_teams", down)
    svc.meta_set_alerts("https://example.webhook.office.com/workflows/x/invoke?sv=1.0&sig=SECRET123")
    svc.meta_set_freshness("gold.fact_sales", within_hours=0.0001, envs=["dev"])
    time.sleep(0.5)
    out = svc.meta_freshness(notify=True)
    assert out["alerted"] == 0 and "sig=[removed]" in out["alert_error"] and "SECRET123" not in out["alert_error"]


def test_teams_card_shape():
    card = dataops_service.teams_card("t", ["a"])
    content = card["attachments"][0]["content"]
    assert content["type"] == "AdaptiveCard" and content["body"][1]["text"] == "a"


# ---- schema drift review

def test_drift_review_shows_renamed_new_and_retyped_columns_and_settles_them(svc):
    proj = svc._project()
    files = proj.local_data / "dev" / "LH_Bronze" / "Files" / "landing" / "orders"
    for f in files.glob("*.csv"):
        f.unlink()
    (files / "orders_2026-10-05.csv").write_text(
        "order_id,cust_id,order_date,amount,discount,status,modified_at,channel\n"
        "201,1,2026-10-05,10,none,shipped,2026-10-05,web\n", encoding="utf-8")
    d = svc.meta_drift("bronze.orders")
    assert d["checked"] and d["problem"] is None
    assert {x["column"] for x in d["added"]} == {"cust_id", "channel"}
    assert [x["column"] for x in d["dropped"]] == ["customer_id"]
    assert d["retyped"] == [{"column": "discount", "contract": "float64", "source": "string"}]
    assert svc.meta_run(["bronze.orders"])[0]["status"] == "failed"  # the run stops on the missing column

    saved = svc.meta_drift_apply("bronze.orders", add={"channel": "string"}, rename={"customer_id": "cust_id"},
                                 policy="strict")
    assert saved["errors"] == []
    assert 'rename = { "cust_id" = "customer_id" }' in saved["text"] and 'channel = "string"' in saved["text"]
    assert 'drift = "strict"' in saved["text"]
    after = svc.meta_drift("bronze.orders")
    assert after["added"] == [] and after["dropped"] == [] and after["policy"] == "strict"
    res = svc.meta_run(["bronze.orders"])[0]
    assert res["status"] == "ok" and res["quarantined"] == 1  # "none" isn't a number: held, not loaded
    held = svc.meta_quarantine("bronze.orders")["rows"][0]
    assert held["customer_id"] == "1" and held["channel"] == "web"  # renamed back, and the new column kept

    # A column the source drops can also leave the contract; the save gate says who still needs it.
    gone = svc.meta_drift_apply("bronze.orders", remove=["channel"], policy="")
    assert 'channel = "string"' not in gone["text"] and "drift =" not in gone["text"]


def test_drift_on_an_entity_source_ignores_upstream_columns_it_does_not_use(svc):
    d = svc.meta_drift("silver.orders")
    assert d["checked"] and d["added"] == [] and d["dropped"] == [] and d["retyped"] == []


def test_a_project_that_cant_be_read_says_so_instead_of_no_runs_and_nothing_late(svc, tmp_path):
    # QA-027: a broken layers.toml used to read as an empty run list and no late tables.
    svc.meta_set_freshness("gold.fact_sales", within_hours=26, envs=["dev"])
    (tmp_path / "proj" / "layers.toml").write_text("[[layers]\nname = ", encoding="utf-8")
    runs = svc.meta_runs_all()
    assert runs["runs"] == [] and runs["project_error"].startswith("TOMLDecodeError: ")
    fresh = svc.meta_freshness()
    assert fresh["items"] == [] and fresh["project_error"] == runs["project_error"]


def test_no_project_yet_is_still_just_empty(tmp_path):
    s = Service(root=tmp_path / "app")
    assert s.meta_runs_all() == {"runs": [], "unavailable": {}}
    assert "project_error" not in s.meta_freshness()


def test_drift_on_an_entity_source_reads_its_columns_not_its_rows(svc, monkeypatch):
    from fabric_desktop.meta.store import LocalStore
    project = svc._project()
    up = project.entities["silver.orders"].spec["source"]["entity"]
    upstream = (project.entities[up].schema, project.entities[up].table)
    reads, real = [], LocalStore.read
    monkeypatch.setattr(LocalStore, "read", lambda self, lh, schema, table, *a, **k:
                        reads.append((schema, table)) or real(self, lh, schema, table, *a, **k))
    d = svc.meta_drift("silver.orders")
    assert d["checked"] and d["problem"] is None and upstream not in reads


def test_hashed_totals_of_floats_added_in_another_order_are_the_same(svc):
    import pyarrow as pa
    import pyarrow.dataset as pads
    from fabric_desktop.meta import compare as compare_mod
    e = svc._project().entities["silver.customers"]
    key = e.keys[0]
    import random
    values = [random.Random(i).uniform(0, 1000) for i in range(5000)]
    left = pa.table({key: [str(i) for i in range(len(values))], "amount": pa.array(values)})
    right = pa.table({key: [str(i) for i in reversed(range(len(values)))], "amount": pa.array(list(reversed(values)))})
    out = compare_mod.hashed(e, pads.dataset(left), pads.dataset(right))
    assert out["totals"][0]["diff"] == 0
    assert compare_mod._diff(1e-12, -1e-12) == 0 and compare_mod._diff(0.1 + 0.2, 0.3) == 0 and compare_mod._diff(1.0, 1.5) == 0.5 and compare_mod._diff(3, 4) == 1


def test_hashed_compare_with_no_shared_columns_still_counts_rows(svc):
    import pyarrow as pa
    import pyarrow.dataset as pads
    from fabric_desktop.meta import compare as compare_mod
    e = svc._project().entities["silver.customers"]
    out = compare_mod.hashed(e, pads.dataset(pa.table({"a": [1, 2]})), pads.dataset(pa.table({"b": [1]})))
    assert out["rows"] == {"left": 2, "right": 1} and out["columns"]["only_left"] == ["a"]
    even = compare_mod.hashed(e, pads.dataset(pa.table({"a": [1, 2]})), pads.dataset(pa.table({"b": [1, 2]})))
    assert even["checksum"]["match"] is None and "share no columns, so no rows could be compared" in even["note"]
    assert "the rows are the same" not in even["note"]


def test_a_table_missing_on_one_side_isnt_loaded_on_the_other(svc, monkeypatch):
    svc.meta_set_environment("test", target="local")
    monkeypatch.setattr(type(svc), "_table", lambda *a: pytest.fail("the table was read into memory"))
    out = svc.meta_compare("silver.orders", "dev", "test")
    assert out["rows"]["right"] is None and out["rows"]["left"] > 0 and "the right" in out["note"]



# ---- posting to Teams

def _answer(status, body=None, headers=None):
    return types.SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: body if body is not None else {})


def _teams(monkeypatch, answers):
    """post_teams against a fake Teams webhook that gives ``answers`` in turn (a response or an error)."""
    from fabric_desktop import retrying
    sent = []

    def post(url, json, timeout):
        sent.append(url)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(dataops_service.http_session, "session", lambda: types.SimpleNamespace(post=post))
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    return sent


def test_teams_throttling_is_waited_out_through_the_shared_session(monkeypatch):
    sent = _teams(monkeypatch, [_answer(429, headers={"Retry-After": "1"}), _answer(202)])
    dataops_service.post_teams("https://example.invalid/hook", "Title", ["line"])
    assert len(sent) == 2


def test_a_teams_5xx_is_sent_once_and_says_why_on_one_line(monkeypatch):
    sent = _teams(monkeypatch, [_answer(503, {"error": {"message": "Service\n  unavailable"}}), _answer(202)])
    with pytest.raises(RuntimeError) as raised:
        dataops_service.post_teams("https://example.invalid/hook", "Title", ["line"])
    assert str(raised.value) == "Teams answered 503: Service unavailable" and len(sent) == 1


def test_a_dropped_connection_to_teams_is_sent_once(monkeypatch):
    sent = _teams(monkeypatch, [requests.ConnectionError("reset"), _answer(202)])
    with pytest.raises(requests.ConnectionError):
        dataops_service.post_teams("https://example.invalid/hook", "Title", ["line"])
    assert len(sent) == 1
