"""From a Data health alert to an incident to one Teams post, end to end, and what each step does when
something breaks: a crash between the incident write and the post, a Teams post that fails, a Delta log
read that hangs, a list or store that stays busy. Fake transports only: the Delta log, the run log and
the Teams webhook are stand-ins, so nothing here reaches Fabric, OneLake or Teams."""

import datetime as dt
import json
import threading

import pytest

from fabric_desktop import dataops_service, incident_service
from fabric_desktop.leases import _file_lock
from fabric_desktop.workloads import incidents as incidents_mod
from fabric_desktop.workloads.incidents import KEY, Conflict
from fabric_desktop.workloads.notify import BACKOFF_FIRST, BACKOFF_MAX, Outbox

from test_incident_partial_work import svc  # noqa: F401 - the app with a metadata project and a signed-in user
from test_notice_failures import HOOK, Teams

STALE = "silver.customers"  # promised every 24 hours in Dev, last written 30 hours ago
OTHER = "gold.dim_customer"


class Schema:
    def __init__(self, fields):
        self.fields = fields

    def to_json(self):
        return json.dumps({"fields": self.fields})


class Log:
    """One table's Delta log as delta-rs reads it: ``writes`` are (hours ago, rows), oldest first."""

    def __init__(self, writes, columns_from=None, hang=None):
        self.writes, self.columns_from, self.hang = writes, columns_from, hang

    def open(self, version):
        log = self

        class Table:
            def history(self, n):
                if log.hang is not None:
                    log.hang.wait(30)  # OneLake not answering
                now = dt.datetime.now(dt.timezone.utc)
                out = [{"version": v, "timestamp": int((now - dt.timedelta(hours=h)).timestamp() * 1000),
                        "operation": "WRITE", "operationParameters": {"mode": "Append"},
                        "operationMetrics": {"numOutputRows": str(rows)}} for v, (h, rows) in enumerate(log.writes)]
                return out[::-1][:n]

            def version(self):
                return len(log.writes) - 1

            def schema(self):
                v = self.version() if version is None else version
                extra = [{"name": "region", "type": "string"}] if log.columns_from is not None and v >= log.columns_from else []
                return Schema([{"name": "id", "type": "long"}] + extra)

        return Table()


@pytest.fixture
def app(svc, monkeypatch):  # noqa: F811 - the fixture imported above
    """The app with STALE promised every 24 hours in Dev and a fake Delta log per table, a Teams
    webhook set, and the run log saying STALE last loaded when its Delta log says."""
    svc.meta_set_freshness(STALE, within_hours=24, envs=["dev"])
    project = svc._project()
    store = svc._store(project, "dev")

    def uri(name):
        e = project.entities[name]
        return store.uri(project.layer(e.layer).lakehouse, e.schema, e.table)

    logs = {uri(STALE): Log([(31, 500)] * 3 + [(30, 500)]), uri(OTHER): Log([(1, 500)] * 3)}
    monkeypatch.setattr(dataops_service, "DeltaTable", lambda u, version=None, storage_options=None: logs[u].open(version))
    loaded = (dt.datetime.now().astimezone() - dt.timedelta(hours=30)).isoformat()
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [
        {"entity": STALE, "status": "ok", "run_id": "r1", "started_at": loaded, "message": ""}])
    teams = Teams()
    monkeypatch.setattr("fabric_desktop.platform_notify_service.post_teams", teams)
    settings = svc._settings
    monkeypatch.setattr(svc, "_settings", lambda: {**settings(), "teams_webhook": HOOK})
    svc.meta_data_health_watch(STALE, "dev")
    svc.meta_data_health_watch(OTHER, "dev")
    svc.test_logs, svc.test_uri, svc.teams = logs, uri, teams
    return svc


def _clocked_outbox(monkeypatch, t):
    """The app's alert outbox on a clock the test moves, so backoff is checked without waiting."""
    real = incident_service.Outbox
    monkeypatch.setattr(incident_service, "Outbox", lambda path: real(path, clock=lambda: t[0]))


def _posted(app):
    return [title for _, title, _ in app.teams.posted]


def _alert(app, name, kind):
    tables = {t["entity"]: t for t in app.meta_data_health_list("dev")["tables"]}
    return next(a for a in tables[name].get("alerts") or [] if a["kind"] == kind)


def _open_an_incident(app, name, alert):
    """What Data health's "Open an incident" button sends: the alert as the title, where it came from as the text."""
    return app.incident_report("dev", f"{name}: {alert['title']}", text="Found by Data health from the table's Delta log.",
                               name=name)


# ---- the chain when nothing breaks


def test_a_stale_table_becomes_one_incident_and_teams_hears_of_it_once(app):
    alert = _alert(app, STALE, "freshness")
    assert alert["title"].startswith("Not updated for 30") and "promise 24 h" in alert["title"]
    first = app.incidents_evaluate("dev", notify=True)
    assert [(c["change"], c["title"]) for c in first["changes"]] == [("opened", f"{STALE} is late in Dev")]
    assert first["notified"]["sent"] == 1 and _posted(app) == [f"New: {STALE} is late in Dev (KratosForge)"]
    for _ in range(3):  # the same stale table on every later check: no second incident, no second post
        again = app.incidents_evaluate("dev", notify=True)
        assert again["changes"] == [] and again["notified"]["sent"] == 0
    assert len(app.teams.posted) == 1
    [inc] = app.incidents_list("dev")["incidents"]
    assert inc["kind"] == "late" and inc["state"] == "open" and inc["name"] == STALE


def test_an_alert_opened_as_an_incident_by_hand_is_kept_and_the_app_doesnt_post_it(app):
    # A volume or schema alert has no run-log signal: the person opens the incident from Data health.
    app.test_logs[app.test_uri(OTHER)] = Log([(h, 500) for h in range(14, 2, -1)] + [(1, 5)], columns_from=12)
    volume, schema = _alert(app, OTHER, "volume"), _alert(app, OTHER, "schema")
    assert volume["version"] == schema["version"] == 12
    inc = _open_an_incident(app, OTHER, volume)
    assert inc["kind"] == "reported" and inc["number"] == "INC-DEV-0001" and inc["title"].startswith(OTHER)
    out = app.incidents_evaluate("dev", notify=True)
    # The evaluation opens the stale table's own incident and posts only that one: a reported incident
    # is never posted by the app (the report dialog says agents tell Teams, this app doesn't) and never
    # recovers by itself, so it stays open in the shared list.
    assert _posted(app) == [f"New: {STALE} is late in Dev (KratosForge)"]
    assert all(c["id"] != inc["id"] for c in out["changes"])
    kept = {i["id"]: i for i in app.incidents_list("dev")["incidents"]}
    assert kept[inc["id"]]["state"] == "open" and kept[inc["id"]]["source"] == "reported"


# ---- a Teams post that fails


def test_a_teams_post_that_fails_waits_and_goes_once_on_a_later_check_with_a_bounded_wait(app, monkeypatch):
    t = [1_000_000.0]
    _clocked_outbox(monkeypatch, t)
    app.teams.fail = 503
    first = app.incidents_evaluate("dev", notify=True)
    assert (first["notified"]["sent"], first["notified"]["queued"], first["notified"]["waiting"]) == (0, 1, 1)
    assert "503" in first["notified"]["error"] and "SECRET" not in first["notified"]["error"]
    tried = [t[0]]
    calls = app.teams.calls
    while t[0] - tried[0] < 6 * BACKOFF_MAX:  # a check every five minutes while Teams stays down
        t[0] += 300
        out = app.incidents_evaluate("dev", notify=True)
        assert out["changes"] == [] and out["notified"]["waiting"] == 1  # still kept, never dropped
        if app.teams.calls > calls:
            calls = app.teams.calls
            tried.append(t[0])
    gaps = [b - a for a, b in zip(tried, tried[1:], strict=False)]
    assert gaps[0] >= BACKOFF_FIRST and max(gaps) <= BACKOFF_MAX + 300  # it rests, but never longer than the cap
    assert gaps[-1] > BACKOFF_MAX / 2  # and the rest has grown to the cap
    app.teams.fail = None
    t[0] += BACKOFF_MAX + 1
    out = app.incidents_evaluate("dev", notify=True)
    assert out["notified"]["sent"] == 1 and out["notified"]["waiting"] == 0
    [(_, title, lines)] = app.teams.posted
    assert title == f"Delayed: New: {STALE} is late in Dev (KratosForge)" and "couldn't be reached" in lines[0]
    for _ in range(3):
        t[0] += BACKOFF_MAX + 1
        app.incidents_evaluate("dev", notify=True)
    assert len(app.teams.posted) == 1  # sent once, never again


# ---- a Data health check that hangs


def test_a_check_that_hangs_is_shown_as_not_finished_and_raises_no_alert(app, monkeypatch):
    monkeypatch.setattr(dataops_service, "DATA_HEALTH_LIST_SECONDS", 0.3)
    hang = threading.Event()
    app.test_logs[app.test_uri(STALE)].hang = hang
    try:
        tables = {t["entity"]: t for t in app.meta_data_health_list("dev")["tables"]}
    finally:
        hang.set()
    stuck = tables[STALE]
    assert stuck["state"] == "error" and "didn't finish within 0.3 s" in stuck["why"]
    assert not {"alerts", "freshness", "volume", "schema"} & set(stuck)  # nothing that could read as healthy
    assert tables[OTHER]["freshness"]["state"] == "no_promise" and tables[OTHER]["alerts"] == []


# ---- a list or store that stays busy


def test_a_busy_data_health_list_leaves_the_saved_list_as_it_was(app, monkeypatch):
    monkeypatch.setattr(dataops_service, "DATA_HEALTH_LOCK_SECONDS", 0.2)
    alert = _alert(app, STALE, "freshness")
    path = app.root / "data_health.json"
    before = path.read_bytes()
    held, release = threading.Event(), threading.Event()

    def other_window():  # flock is per open file, so another thread's open stands in for another process
        with _file_lock(app.root / "data_health.json.lock"):
            held.set()
            release.wait(5)

    other = threading.Thread(target=other_window)
    other.start()
    assert held.wait(5)
    try:
        with pytest.raises(TimeoutError, match="saving the Data health list"):
            app.meta_data_health_quiet(STALE, "dev", "freshness", alert["version"])
        with pytest.raises(TimeoutError):
            app.meta_data_health_list("dev")  # refused, never an empty or healthy list
    finally:
        release.set()
        other.join()
    assert path.read_bytes() == before and not list(app.root.glob(".data_health.json.*.tmp"))
    app.meta_data_health_quiet(STALE, "dev", "freshness", alert["version"])  # once it lets go, it saves
    assert _alert(app, STALE, "freshness")["quiet"]["until"] is None


def test_a_busy_incident_list_writes_nothing_and_posts_nothing_until_it_frees(app, monkeypatch):
    monkeypatch.setattr(incidents_mod, "ATTEMPTS", 2)  # gives up after two tries, not eight
    backend = app._incident_log("dev").backend
    real = type(backend).create
    monkeypatch.setattr(type(backend), "create", lambda self, key, data: False)  # another writer always wins
    alert = _alert(app, STALE, "freshness")
    with pytest.raises(Conflict):
        _open_an_incident(app, STALE, alert)
    with pytest.raises(Conflict):
        app.incidents_evaluate("dev", notify=True)
    assert backend.read(KEY) is None and app.teams.calls == 0  # nothing half written, nothing posted
    assert Outbox(app.root / "alerts_outbox.sqlite").count() == 0
    monkeypatch.setattr(type(backend), "create", real)
    out = app.incidents_evaluate("dev", notify=True)
    assert [c["change"] for c in out["changes"]] == ["opened"] and len(app.teams.posted) == 1
