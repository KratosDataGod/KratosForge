"""WR-410 / FINOPS-004: one capacity reading, every gated Optimize, Vacuum and Z-Order entry point, one answer.

The owner's rule: a capacity check of Blocked is a hard stop (nothing submitted, no Delta commit, no retention
change, and the refusal says Blocked); Warning goes ahead only past an acknowledgement; Unknown (the capacity
can't be read) doesn't stop. The gates are X-ray's Optimize and Vacuum (_xray_risk_gate over risk.analyze),
Maintain > Optimize and Optimize after a run (CloudMixin._maintain over _maintenance_capacity_refusal), and a
notebook's OPTIMIZE, ZORDER BY, VACUUM and RESTORE (lakehouse.maintain_table over _maintenance_refusal). All
three classify the reading with risk.check_capacity, so each reading below must be stopped by all or by none.

The readings are built the way _risk_capacity_read builds them (capacity.observe_env, merge_cached and
freshness over fake Fabric replies), plus a few hand-made shapes check_capacity classifies. No network, no
Fabric. Nothing here claims a saving: the assertions are about what was started or changed."""

import datetime as dt
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import app_settings, capacity, lakehouse, maintenance, risk
from fabric_desktop.audit import AuditLog
from fabric_desktop.cloud_service import CloudMixin
from fabric_desktop.meta_service import MetaMixin
from tests.test_maintenance_entry_points import ROWS, Store
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_xray import vacuum_svc  # noqa: F401 - a fixture

BLOCKED, WARNING, UNKNOWN, PASS = risk.BLOCKED, risk.WARNING, risk.UNKNOWN, risk.PASS


class HttpError(Exception):
    """A failed Fabric call as the REST client raises it: an HTTP status and Fabric's error code."""

    def __init__(self, status, code=""):
        super().__init__(f"HTTP {status} {code}".strip())
        self.status, self.code = status, code


def _cap(state):
    return {"cap1": {"id": "cap1", "displayName": "fabcapdev", "sku": "F8", "state": state}}


def _ws(reply):
    def get_workspace(_ws_id):
        if isinstance(reply, BaseException):
            raise reply
        return reply
    return get_workspace


ON_CAP = {"displayName": "Dev", "capacityId": "cap1"}
ON_CAP_READ = _ws(ON_CAP)


def read(get_workspace=ON_CAP_READ, capacities=None, capacities_error=None, age_s=60, workspace_id="ws-dev"):
    """A workspace's reading as _risk_capacity_read returns it: observe_env, then its freshness."""
    now = dt.datetime.now(dt.timezone.utc)
    obs = capacity.observe_env("", workspace_id, "Dev", get_workspace, capacities, capacities_error,
                               now - dt.timedelta(seconds=age_s))
    return obs | capacity.freshness(obs, now)


def cached(state, age_s=60):
    """The environments' reading when this read failed (offline) and the last good one is kept: merge_cached."""
    now = dt.datetime.now(dt.timezone.utc)
    prev = capacity.observe_env("dev", "ws-dev", "Dev", _ws(ON_CAP), _cap(state), None, now - dt.timedelta(seconds=age_s))
    fresh = capacity.observe_env("dev", "ws-dev", "Dev", _ws(ConnectionError("offline")), None, "offline", now)
    env = capacity.merge_cached({"environments": [fresh]}, {"environments": [prev]}, now)["environments"][0]
    assert env.get("cached") is True
    return env


class ReadFails(Exception):
    pass


def _raise():
    raise ReadFails("the capacity read itself failed")


# name -> (a factory for the reading _risk_capacity returns, or raises; what check_capacity makes of it)
READINGS = {
    # ---- Blocked: every shape check_capacity stops
    "paused": (lambda: read(capacities=_cap("Inactive")), BLOCKED),
    "paused_read_59_minutes_ago": (lambda: read(capacities=_cap("Inactive"), age_s=59 * 60), BLOCKED),
    "workspace_not_on_a_capacity": (lambda: read(get_workspace=_ws({"displayName": "Dev"})), BLOCKED),
    "workspace_no_access_403": (lambda: read(get_workspace=_ws(HttpError(403))), BLOCKED),
    "workspace_no_access_401": (lambda: read(get_workspace=_ws(HttpError(401))), BLOCKED),
    "workspace_no_access_code": (lambda: read(get_workspace=_ws(HttpError(400, "InsufficientPrivileges"))), BLOCKED),
    "workspace_not_found_404": (lambda: read(get_workspace=_ws(HttpError(404))), BLOCKED),
    "workspace_not_found_code": (lambda: read(get_workspace=_ws(HttpError(400, "WorkspaceNotFound"))), BLOCKED),
    # Hand-made: a workspace-less reading never matches a gate's workspace in _risk_capacity_read, but check_capacity
    # blocks the shape, so every gate must too.
    "no_workspace_assigned": (lambda: read(workspace_id=""), BLOCKED),
    # ---- Warning: a paused capacity whose reading is old or is the last good one kept
    "paused_read_61_minutes_ago": (lambda: read(capacities=_cap("Inactive"), age_s=61 * 60), WARNING),
    "paused_cached_after_a_failed_read": (lambda: cached("Inactive"), WARNING),
    # ---- Unknown: the capacity's state now can't be read
    "not_read": (lambda: None, UNKNOWN),
    "the_read_raises": (_raise, UNKNOWN),
    "running_read_61_minutes_ago": (lambda: read(capacities=_cap("Active"), age_s=61 * 60), UNKNOWN),
    "running_cached_after_a_failed_read": (lambda: cached("Active"), UNKNOWN),
    "capacity_not_visible": (lambda: read(capacities={}), UNKNOWN),
    "capacity_scope_not_granted": (lambda: read(capacities=None, capacities_error="scope_not_granted"), UNKNOWN),
    "capacity_scope_refused_403": (lambda: read(capacities=None, capacities_error=capacity.classify_error(
        PermissionError("InsufficientScopes"))), UNKNOWN),
    "offline": (lambda: read(get_workspace=_ws(ConnectionError("no route"))), UNKNOWN),
    "signed_out": (lambda: read(get_workspace=None), UNKNOWN),
    "workspace_read_throttled_429": (lambda: read(get_workspace=_ws(HttpError(429, "TooManyRequests"))), UNKNOWN),
    "capacity_list_throttled_429": (lambda: read(capacities=None, capacities_error=capacity.classify_error(
        HttpError(429, "TooManyRequests"))), UNKNOWN),
    "capacity_rejection_430": (lambda: read(get_workspace=_ws(HttpError(430, "CapacityLimitExceeded"))), UNKNOWN),
    "workspace_read_server_error_500": (lambda: read(get_workspace=_ws(HttpError(500))), UNKNOWN),
    "state_not_recognized_suspended": (lambda: read(capacities=_cap("Suspended")), UNKNOWN),
    "state_not_recognized_throttled": (lambda: read(capacities=_cap("Throttled")), UNKNOWN),
    "state_missing": (lambda: read(capacities={"cap1": {"id": "cap1", "displayName": "fabcapdev"}}), UNKNOWN),
    # ---- Pass
    "running": (lambda: read(capacities=_cap("Active")), PASS),
}
NAMES = list(READINGS)
STOPPED = [n for n in NAMES if READINGS[n][1] == BLOCKED]


def reader(name):
    """_risk_capacity for a gate: a fresh reading each call, so its age is measured when the gate asks."""
    factory = READINGS[name][0]
    return lambda *a, **k: factory()


def classify(name):
    try:
        reading = READINGS[name][0]()
    except ReadFails:
        return UNKNOWN, ""
    found = risk.check_capacity({"where": "fabric", "capacity": reading}, dt.datetime.now(dt.timezone.utc))
    return found["outcome"], found["title"]


def test_the_matrix_covers_every_outcome_and_every_access_check_capacity_stops():
    assert {o for _f, o in READINGS.values()} == {BLOCKED, WARNING, UNKNOWN, PASS}
    stopped_access = {READINGS[n][0]()["access"] for n in STOPPED}
    assert stopped_access == {"ok", "no_workspace", "no_capacity", "workspace_no_access", "workspace_not_found"}


@pytest.mark.parametrize("name", NAMES)
def test_check_capacity_classifies_the_reading(name):
    assert classify(name)[0] == READINGS[name][1]


# ---- X-ray: Optimize and Vacuum

XRAY = ("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
XRAY_CALLS = {"optimize": ("xray_optimize_check", "xray_optimize"), "vacuum": ("xray_vacuum_preview", "xray_vacuum")}


@pytest.fixture
def xray(vacuum_svc, monkeypatch):  # noqa: F811
    svc, uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    real = app_settings.value
    monkeypatch.setattr(app_settings, "value", lambda k: True if k == "allow_fabric_runs" else real(k))
    return svc, uri, jobs


@pytest.mark.parametrize("op", list(XRAY_CALLS))
@pytest.mark.parametrize("name", NAMES)
def test_xray(xray, monkeypatch, name, op):
    svc, uri, jobs = xray
    expected, title = classify(name)
    monkeypatch.setattr(svc, "_risk_capacity", reader(name), raising=False)
    check, start = (getattr(svc, m) for m in XRAY_CALLS[op])
    shown = check(*XRAY)["risk"]
    assert shown["result"] == expected  # nothing but the capacity differs from a pass here
    props = dict(DeltaTable(str(uri)).metadata().configuration)
    version = DeltaTable(str(uri)).version()
    if expected == BLOCKED:
        with pytest.raises(PermissionError) as said:
            start(*XRAY, finding_id="f1", risk_id=shown["id"])  # even with the id of the assessment shown
        assert "blocked" in str(said.value).lower() and title in str(said.value)
        assert jobs == [] and svc.risk_acknowledgements() == []
        assert not svc._settings().get("xray_submitted") and svc.fabric_runs(refresh=False) == []
    elif expected == WARNING:
        with pytest.raises(ValueError, match="Check the risks first"):
            start(*XRAY, finding_id="f1")
        assert jobs == [] and svc.risk_acknowledgements() == []
        start(*XRAY, finding_id="f1", risk_id=shown["id"])
        assert len(jobs) == 1 and len(svc.risk_acknowledgements()) == 1
    else:
        start(*XRAY, finding_id="f1")  # an unknown or a pass needs no acknowledgement and records none
        assert len(jobs) == 1 and svc.risk_acknowledgements() == []
        assert jobs[0][1].get("optimize", True) is (op == "optimize")
    # X-ray only submits Fabric's job: it never commits to the table or changes its retention itself.
    assert DeltaTable(str(uri)).version() == version
    assert dict(DeltaTable(str(uri)).metadata().configuration) == props


# ---- Maintain > Optimize, and Optimize after a run

def maintain_mixin(name, monkeypatch):
    """ws-dev (LH_Silver's two tables) has the reading under test; ws-serve (LH_Gold) runs, as a control."""
    jobs, retention = [], []
    monkeypatch.setattr(maintenance, "set_retention", lambda uri, days, options=None, **k: retention.append(uri))
    dev = reader(name)
    m = SimpleNamespace(
        _project=lambda: SimpleNamespace(environments={}),
        _risk_capacity=lambda ws, *a: dev() if ws == "ws-dev" else read(capacities=_cap("Active")),
        fabric_jobs=SimpleNamespace(run_table_maintenance=lambda *a, **k: jobs.append(a) or f"job-{len(jobs)}"),
        _log_fabric_run=lambda r: r | {"status": "NotStarted"},
        _wl_user=lambda: "chris", acks=[])
    m._risk_ack_write = m.acks.append
    m._maintenance_capacity = lambda ws: MetaMixin._maintenance_capacity(m, ws)
    m._maintenance_capacity_refusal = lambda *a, **k: MetaMixin._maintenance_capacity_refusal(m, *a, **k)
    m._maintenance_gate = lambda *a: CloudMixin._maintenance_gate(m, *a)
    m._maintain = lambda *a: CloudMixin._maintain(m, *a)
    return m, jobs, retention


@pytest.mark.parametrize("name", NAMES)
def test_maintain_optimize(monkeypatch, name):
    expected, title = classify(name)
    m, jobs, retention = maintain_mixin(name, monkeypatch)
    out = CloudMixin._maintain(m, Store(), ROWS, "dev")
    if expected == BLOCKED:
        assert [(r["status"], r.get("blocked", False)) for r in out] == [("Failed", True)] * 2 + [("NotStarted", False)]
        for r in out[:2]:
            assert "capacity check is Blocked" in r["message"] and title in r["message"]
            assert "Nothing was changed" in r["message"]
        assert [a[1] for a in jobs] == ["lh-gold"]
        assert retention == ["abfss://LH_Gold/dbo/sales"]
    elif expected == WARNING:
        # Without the warning's risk_id (nobody accepted it) its workspace's tables are left, each saying why.
        assert [(r["status"], r.get("warning", False)) for r in out] == [("Failed", True)] * 2 + [("NotStarted", False)]
        for r in out[:2]:
            assert r["message"].startswith("Not optimized (capacity warning)") and title in r["message"]
            assert r["risk_id"]
        assert [a[1] for a in jobs] == ["lh-gold"] and retention == ["abfss://LH_Gold/dbo/sales"]
        assert m.acks == []
    else:
        assert [r["status"] for r in out] == ["NotStarted"] * 3
        assert [a[1] for a in jobs] == ["lh-silver", "lh-silver", "lh-gold"] and len(retention) == 3
        assert m.acks == []


@pytest.mark.parametrize("name", STOPPED)
def test_optimize_after_a_run(monkeypatch, name):
    m, jobs, retention = maintain_mixin(name, monkeypatch)
    m._optimize_after_run = lambda project, env: True
    m._managed_tables = lambda project, include_meta=True: [dict(ROWS[0], entity="orders")]
    project = SimpleNamespace(entities={"orders": SimpleNamespace(schema="dbo", table="orders")})
    results = [{"entity": "orders", "status": "ok", "rows_written": 5}]
    CloudMixin._after_run(m, project, "dev", Store(), results)
    assert results[0]["maintenance"] == "Failed" and "Blocked" in results[0]["maintenance_message"]
    assert jobs == [] and retention == []


def warned_mixin(monkeypatch):
    """ws-dev's reading is a Warning (paused when read 61 minutes ago), the same reading each time it's asked,
    as the cached reading is between meta_optimize_check and meta_optimize."""
    m, jobs, retention = maintain_mixin("running", monkeypatch)
    warned = read(capacities=_cap("Inactive"), age_s=61 * 60)
    m._risk_capacity = lambda ws, *a: warned if ws == "ws-dev" else read(capacities=_cap("Active"))
    return m, jobs, retention


def test_maintain_starts_a_warned_workspace_once_its_warning_is_accepted_and_recorded(monkeypatch):
    m, jobs, retention = warned_mixin(monkeypatch)
    found = m._maintenance_capacity("ws-dev")
    assert found["outcome"] == WARNING and found["risk_id"] == m._maintenance_capacity("ws-dev")["risk_id"]
    out = CloudMixin._maintain(m, Store(), ROWS, "dev", {"ws-dev": found["risk_id"]})
    assert [r["status"] for r in out] == ["NotStarted"] * 3
    assert [a[1] for a in jobs] == ["lh-silver", "lh-silver", "lh-gold"] and len(retention) == 3
    # recorded once for the workspace, before its jobs, as X-ray records an accepted warning
    assert len(m.acks) == 1
    ack = m.acks[0]
    assert ack["risk_id"] == found["risk_id"] and ack["action"] == "meta_optimize" and ack["result"] == WARNING
    assert ack["workload_id"] == "maintain:ws-dev" and ack["user"] == "chris" and ack["warnings"] == [found["title"]]


def test_maintain_refuses_a_warning_that_changed_since_it_was_accepted(monkeypatch):
    m, jobs, retention = warned_mixin(monkeypatch)
    out = CloudMixin._maintain(m, Store(), ROWS, "dev", {"ws-dev": "an-older-reading"})
    assert [r["status"] for r in out] == ["Failed", "Failed", "NotStarted"]
    assert "changed since you looked" in out[0]["message"]
    assert [a[1] for a in jobs] == ["lh-gold"] and retention == ["abfss://LH_Gold/dbo/sales"] and m.acks == []


def test_maintain_starts_nothing_when_the_acceptance_cant_be_recorded(monkeypatch):
    m, jobs, retention = warned_mixin(monkeypatch)

    def fails(_record):
        raise OSError("disk full")
    m._risk_ack_write = fails
    out = CloudMixin._maintain(m, Store(), ROWS, "dev", {"ws-dev": m._maintenance_capacity("ws-dev")["risk_id"]})
    assert [r["status"] for r in out] == ["Failed", "Failed", "NotStarted"]
    assert "couldn't be recorded" in out[0]["message"] and "Try Optimize again" in out[0]["message"]
    assert [a[1] for a in jobs] == ["lh-gold"] and retention == ["abfss://LH_Gold/dbo/sales"]


def test_a_blocked_workspace_stays_stopped_whatever_risk_id_is_given(monkeypatch):
    m, jobs, _ = maintain_mixin("paused", monkeypatch)
    out = CloudMixin._maintain(m, Store(), ROWS, "dev", {"ws-dev": "anything"})
    assert [r.get("blocked", False) for r in out] == [True, True, False] and [a[1] for a in jobs] == ["lh-gold"]
    assert m.acks == []


def test_optimize_after_a_run_skips_a_warned_workspace_and_says_why(monkeypatch):
    m, jobs, retention = warned_mixin(monkeypatch)
    m._optimize_after_run = lambda project, env: True
    m._managed_tables = lambda project, include_meta=True: [dict(ROWS[0], entity="orders")]
    project = SimpleNamespace(entities={"orders": SimpleNamespace(schema="dbo", table="orders")})
    results = [{"entity": "orders", "status": "ok", "rows_written": 5}]
    CloudMixin._after_run(m, project, "dev", Store(), results)
    assert results[0]["maintenance"] == "skipped"
    assert results[0]["maintenance_message"].startswith("Not optimized (capacity warning)")
    assert jobs == [] and retention == [] and m.acks == []


def test_meta_optimize_check_lists_each_workspace_with_its_reading_and_starts_nothing(monkeypatch):
    m, jobs, retention = warned_mixin(monkeypatch)
    store = Store()
    m._project = lambda: SimpleNamespace(environments={})
    m._store = lambda project, env: store
    m._managed_tables = lambda project, include_meta=True: [dict(r, entity=r["table"]) for r in ROWS]
    m._env_cfg = lambda project, env: {"workspace_id": "ws-dev", "workspace_name": "Sales Silver - Dev",
                                       "workspaces": {"serve": {"id": "ws-serve", "name": "Sales Gold - Dev"}}}
    got = CloudMixin.meta_optimize_check(m, "dev")
    assert got["local"] is False and got["env"] == "dev"
    by_ws = {w["workspace_id"]: w for w in got["workspaces"]}
    assert by_ws["ws-dev"]["workspace_name"] == "Sales Silver - Dev" and by_ws["ws-dev"]["result"] == WARNING
    assert by_ws["ws-dev"]["tables"] == ["dbo.orders", "dbo.lines"]
    assert by_ws["ws-dev"]["risk_id"] == m._maintenance_capacity("ws-dev")["risk_id"]
    assert by_ws["ws-serve"]["result"] == PASS and by_ws["ws-serve"]["tables"] == ["dbo.sales"]
    assert jobs == [] and retention == [] and m.acks == []
    # only some entities: only their workspaces
    assert [w["workspace_id"] for w in CloudMixin.meta_optimize_check(m, "dev", ["sales"])["workspaces"]] == ["ws-serve"]


# ---- a notebook's OPTIMIZE, ZORDER BY, VACUUM and RESTORE (delta-rs on this PC)

NOTEBOOK_OPS = {"optimize": ("optimize", {}), "zorder": ("optimize", {"zorder": ["id"]}),
                "vacuum": ("vacuum", {}), "restore": ("restore", {"version": 0})}


@pytest.mark.parametrize("asked", list(NOTEBOOK_OPS))
@pytest.mark.parametrize("name", NAMES)
def test_notebook_maintenance(lake, tmp_path, monkeypatch, name, asked):  # noqa: F811
    expected, title = classify(name)
    m = SimpleNamespace(_project=lambda: SimpleNamespace(environments={}), audit=AuditLog(tmp_path / "audit"),
                        _risk_capacity=reader(name))
    m._maintenance_capacity = lambda ws: MetaMixin._maintenance_capacity(m, ws)
    m._maintenance_capacity_refusal = lambda *a, **k: MetaMixin._maintenance_capacity_refusal(m, *a, **k)
    m._maintenance_audit = lambda *a: MetaMixin._maintenance_audit(m, *a)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    path = str(lake / "dbo" / "sales")
    for i in range(3):
        write_deltalake(path, pa.table({"id": [10 + i], "region": ["EU"]}), mode="append")
    before = DeltaTable(path)
    version, props = before.version(), dict(before.metadata().configuration)
    op, kw = NOTEBOOK_OPS[asked]
    outcomes = lambda: [(r["outcome"], r["target"]["operation"]) for r in m.audit.recent(10, "notebook_maintenance")]  # noqa: E731
    if expected == BLOCKED:
        with pytest.raises(PermissionError, match="capacity check is Blocked") as said:
            lakehouse.maintain_table("dbo.sales", op, **kw)
        assert title in str(said.value) and "Nothing was changed" in str(said.value)
        after = DeltaTable(path)
        assert after.version() == version and dict(after.metadata().configuration) == props
        assert outcomes() == [("error", asked)]
    elif expected == WARNING:
        # Notebook code can't accept a warning, so it is refused and sent to X-ray (Chris, 2026-11-18).
        with pytest.raises(PermissionError, match="has a warning") as said:
            lakehouse.maintain_table("dbo.sales", op, **kw)
        assert title in str(said.value) and "X-ray" in str(said.value) and "nothing was changed" in str(said.value).lower()
        after = DeltaTable(path)
        assert after.version() == version and dict(after.metadata().configuration) == props
        assert outcomes() == [("error", asked)]
    else:
        lakehouse.maintain_table("dbo.sales", op, **kw)
        assert outcomes() == [("started", asked)]
        if op != "vacuum":
            assert DeltaTable(path).version() > version
