"""Promises on products (data mesh S3): what a product promises its readers, kept in its file and the
catalog, and an incident when it's broken."""

import datetime as dt

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.meta import mesh
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.incidents import KIND_LABELS

PORT = [{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": {"id": "int64"}}]
NOW = dt.datetime(2026, 11, 3, 9, 0).astimezone()


def _product(**kw):
    return {"project": "crm", "name": "customers", "access": "public", "status": "published", "version": "1"} | kw


def _run(status="ok", run_id="r1", hours_ago=1.0, rows=1000, quarantined=0, entity="silver.customers"):
    return {"entity": entity, "status": status, "run_id": run_id, "rows_read": rows, "quarantined": quarantined,
            "started_at": (NOW - dt.timedelta(hours=hours_ago)).isoformat(), "seconds": 0}


@pytest.fixture
def platform(tmp_path):
    return LocalPlatform(tmp_path / "platform.sqlite", lambda: "chris@example.com")


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _save(svc, promise, status="published"):
    svc.mesh_save_product({"name": "customers", "access": "public", "status": status, "version": "1",
                           "promise": promise, "ports": [{"name": "customers", "entity": "silver.customers"}]})
    svc._project().reload()


# --------------------------------------------------------------------------- the product file


def test_a_promise_is_written_read_and_described(svc):
    _save(svc, {"within_hours": 24, "by": "06:00", "max_rejected_pct": 1})
    text = (svc._project().root / "metadata" / "products" / "customers.toml").read_text()
    assert "[promise]" in text and 'by = "06:00"' in text and "envs" not in text  # Prod is the default
    p = svc._project().products["customers"]
    assert p.promise == {"within_hours": 24, "by": "06:00", "max_rejected_pct": 1, "envs": ["prod"]}
    assert p.summary()["promise"] == p.promise
    assert mesh.describe_promise(p.promise) == "every 24 h, by 06:00 each day, under 1 % rows rejected"
    assert mesh.promises(svc._project()) == {"silver.customers": [{"product": "customers", **p.promise}]}


def test_no_promise_and_drafts_promise_nothing(svc):
    _save(svc, None)
    assert svc._project().products["customers"].promise is None
    assert "[promise]" not in (svc._project().root / "metadata" / "products" / "customers.toml").read_text()
    _save(svc, {"within_hours": 24}, status="draft")
    assert mesh.promises(svc._project()) == {}


@pytest.mark.parametrize("bad, said", [
    ({"within_hours": 0}, "within_hours must be a number"),
    ({"by": "6am"}, 'like "06:00"'),
    ({"max_rejected_pct": 100}, "max_rejected_pct must be a percentage"),
    ({"max_rejected_pct": True}, "max_rejected_pct must be a percentage"),
    ({"within_hours": 1, "envs": ["qa"]}, "envs must list"),
    ({"within_hours": 1, "soon": True}, "doesn't know soon"),
    ({"envs": ["prod"]}, "needs within_hours, by or max_rejected_pct"),
])
def test_the_save_gate_checks_the_promise(bad, said):
    assert any(said in e for e in mesh.promise_problems("products/customers", bad))


def test_a_bad_promise_stops_the_save(svc):
    with pytest.raises(GateError) as err:
        _save(svc, {"within_hours": -1})
    assert "within_hours" in str(err.value)


# --------------------------------------------------------------------------- the catalog


def test_the_catalog_keeps_the_promise_readers_see(platform):
    out = platform.call("register_product", product=_product(promise={"within_hours": 24, "by": "06:00"}),
                        ports=PORT, env="prod")
    assert out["promise"] == {"within_hours": 24.0, "by": "06:00", "envs": ["prod"]}
    [p] = platform.call("list_products")
    assert p["promise"] == out["promise"]
    platform.call("register_product", product=_product(), ports=PORT, env="prod")  # the promise is taken away
    assert platform.call("list_products")[0]["promise"] is None


@pytest.mark.parametrize("bad", [{"within_hours": "soon"}, {"by": "25:00"}, {"max_rejected_pct": 150},
                                 {"within_hours": 1, "envs": ["qa"]}, "fast"])
def test_the_catalog_refuses_a_promise_nobody_can_check(platform, bad):
    with pytest.raises(core.PlatformError):
        platform.call("register_product", product=_product(promise=bad), ports=PORT, env="prod")
    assert platform.call("list_products") == []


def test_a_catalog_made_before_promises_gets_the_column_on_publish(tmp_path):
    platform = LocalPlatform(tmp_path / "platform.sqlite", lambda: "chris@example.com")
    platform.call("register_product", product=_product(), ports=PORT, env="prod")
    with platform._db() as db:  # as a platform made before promises left it
        db.run(f"ALTER TABLE {db.t('catalog', 'product')} DROP COLUMN promise")
        db.commit()
        assert "promise" not in core._columns(db, "product")
    assert platform.call("list_products")[0]["promise"] is None  # reads as none, creates nothing
    platform.call("register_product", product=_product(promise={"max_rejected_pct": 2}), ports=PORT, env="prod")
    assert platform.call("list_products")[0]["promise"] == {"max_rejected_pct": 2.0, "envs": ["prod"]}


# --------------------------------------------------------------------------- broken promises


PROMISED = {"silver.customers": [{"product": "customers", "within_hours": 24, "by": None, "max_rejected_pct": 1,
                                  "envs": ["prod"]}]}


def _signals(runs, env="prod", spec=None, promises=PROMISED):
    return [s for s in evaluate.signals("crm", {"silver.customers": spec or {}}, runs, env, now=NOW, promises=promises)
            if s["kind"] == "promise"]


def test_a_kept_promise_opens_nothing():
    assert _signals([_run(quarantined=5)]) == []  # 0.5 % rejected, loaded an hour ago


def test_a_late_product_breaks_its_promise():
    [s] = _signals([_run(hours_ago=30)])
    assert s["title"] == "customers broke its promise in Prod (silver.customers)"
    assert "promised every 24 h" in s["text"] and s["evidence"]["products"] == ["customers"]
    assert KIND_LABELS["promise"] == "Promise broken" and "promise" in evaluate.WORKLOAD_KINDS


def test_too_many_rejected_rows_break_the_promise():
    [s] = _signals([_run(quarantined=50, run_id="r9")])
    assert "5.0 % of rows rejected" in s["text"] and s["evidence_id"] == "rejected:r9"


def test_both_causes_make_one_signal():
    [s] = _signals([_run(hours_ago=30, quarantined=50)])
    assert len(s["evidence"]["reasons"]) == 2


def test_a_promise_holds_only_where_it_applies():
    assert _signals([_run(hours_ago=30)], env="test") == []


def test_the_entitys_own_lateness_isnt_reported_twice():
    spec = {"freshness": {"within_hours": 26, "envs": ["prod"]}}
    got = evaluate.signals("crm", {"silver.customers": spec}, [_run(hours_ago=30)], "prod", now=NOW, promises=PROMISED)
    assert [s["kind"] for s in got] == ["late"]


def test_a_load_that_read_nothing_or_failed_says_nothing_about_rejected_rows():
    assert _signals([_run(rows=0, quarantined=0)]) == []
    assert _signals([_run(status="failed", quarantined=900), _run(run_id="r0")]) == []


def test_without_promises_the_signals_are_as_before():
    assert _signals([_run(hours_ago=30)], promises=None) == []
