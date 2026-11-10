"""The app's service (service.py), the paths the main tests don't reach: signing in and out, the
background clean-up and usage send at start, Home's readiness checks, capacities a sign-in can't list,
the catalog with a workspace that can't be read, tokens for notebookutils, a result that is gone, a
download that fails, and sessions that can't be shared. Sign-in, Fabric and OneLake are stand-ins."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest

import fabric_desktop.service as service_mod
from fabric_desktop import readiness, retention, schema_cache
from fabric_desktop.frames import GONE, FrameGone
from fabric_desktop.service import Service

PAT = "dapi" + "0123456789abcdef" * 2


class Auth:
    """A signed-in person, or not: ``user`` None raises as a PC with no sign-in does."""

    def __init__(self, user="chris@contoso.com", silent="silent-token"):
        self.user, self.silent, self.asked, self.signed_out = user, silent, [], False

    def signed_in_user(self):
        if self.user is None:
            raise RuntimeError(f"No client ID yet ({PAT})")
        return self.user

    def signed_in_tenant(self):
        return "tenant-1"

    def fabric_token(self):
        self.asked.append("fabric")
        return "fabric-token"

    def storage_token(self):
        self.asked.append("storage")
        return "storage-token"

    def _token(self, scopes):
        self.asked.append(tuple(scopes))
        return "scoped-token"

    def silent_token(self, scopes):
        return self.silent

    def sign_out(self):
        self.signed_out = True


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.test_auth = Auth()
    monkeypatch.setattr(Service, "auth", property(lambda self: self.test_auth))
    return s


class Fabric:
    """Fabric's API: workspaces, their lakehouses or items, capacities. ``broken`` workspaces raise."""

    def __init__(self, token=None, refresh=None, broken=(), capacities=None, error=None):
        self.broken, self.capacities, self.error = broken, capacities, error

    def list_workspaces(self):
        return [{"id": "w1", "displayName": "Sales"}, {"id": "w2", "displayName": "Paused", "type": "Personal"}]

    def list_lakehouses(self, ws):
        if ws in self.broken:
            raise RuntimeError("CapacityNotActive")
        return [{"id": f"{ws}-lh", "displayName": "LH"}]

    def list_items(self, ws):
        if ws in self.broken:
            raise RuntimeError("CapacityNotActive")
        return [{"id": f"{ws}-lh", "displayName": "LH", "type": "Lakehouse"},
                {"id": f"{ws}-nb", "displayName": "Load", "type": "Notebook", "description": "loads"}]

    def list_capacities(self):
        if self.error:
            raise self.error
        return self.capacities


# ---- signing in and out, and what starts with the engine


def test_sign_in_asks_for_both_tokens_and_sign_out_forgets_the_table_list(svc):
    assert svc.sign_in() == {"user": "chris@contoso.com", "tenant": "tenant-1"}
    assert svc.test_auth.asked == ["fabric", "storage"]
    svc._tables = {"pending": False, "items": {"lh": ["dbo.t"]}}
    assert svc.sign_out() == {"user": None}
    assert svc.test_auth.signed_out and svc._tables is None


def test_the_environment_without_a_sign_in_starts_the_clean_up_and_usage_send_once(svc, monkeypatch, caplog):
    svc.test_auth.user = None
    swept, sent = threading.Event(), threading.Event()
    calls = []

    def sweep(root, days):
        calls.append(("sweep", root, days))
        swept.set()
        return {"removed": ["lakehouses/a"], "bytes": 10, "days": days}
    monkeypatch.setattr(retention, "sweep", sweep)
    monkeypatch.setattr(svc.telemetry, "send_due", lambda: calls.append(("send",)) or sent.set())
    with caplog.at_level(logging.INFO, logger=service_mod.__name__):
        env = svc.environment()
        assert swept.wait(10) and sent.wait(10)
        svc.environment()  # once per engine start
    assert env["user"] is None and env["tenant"] is None
    assert calls.count(("send",)) == 1 and [c for c in calls if c[0] == "sweep"] == [("sweep", svc.root, 30)]
    assert "Not signed in" in caplog.text and PAT not in caplog.text


def test_a_clean_up_or_usage_send_that_fails_is_only_logged(svc, monkeypatch, caplog):
    done = threading.Event()

    def sweep(root, days):
        raise OSError("the cache folder is locked")

    def send():
        try:
            raise ConnectionError("offline")
        finally:
            done.set()
    monkeypatch.setattr(retention, "sweep", sweep)
    monkeypatch.setattr(svc.telemetry, "send_due", send)
    with caplog.at_level(logging.INFO, logger=service_mod.__name__):
        svc._start_retention_sweep()
        svc._start_usage_send()
        assert done.wait(10)
        svc._sweep_stop.set()  # the sweep repeats daily: end its wait
        for t in threading.enumerate():
            if t.name == "usage-send" or t is svc._sweeper:
                t.join(10)
    assert "Local copy cleanup failed" in caplog.text and "Usage data not sent: ConnectionError" in caplog.text


def test_the_local_copy_sweep_repeats_while_the_engine_runs_under_the_policy_as_it_is_then(svc, monkeypatch):
    swept = []
    two = threading.Event()

    def sweep(root, d):
        swept.append(d)
        if len(swept) >= 2:
            two.set()
        return {"removed": [], "bytes": 0, "days": d}
    monkeypatch.setattr(retention, "sweep", sweep)
    monkeypatch.setattr(service_mod.app_settings, "value", lambda key: 7 if swept else 30)  # IT shortens it
    monkeypatch.setattr(service_mod, "SWEEP_EVERY", 0.05)
    svc._start_retention_sweep()
    svc._start_retention_sweep()  # a later call starts no second sweeper
    try:
        assert two.wait(10)
    finally:
        svc._sweep_stop.set()
    svc._sweeper.join(10)
    assert swept[:2] == [30, 7] and not svc._sweeper.is_alive()


def test_a_scheduled_run_never_sends_usage_data(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app", headless=True)
    monkeypatch.setattr(s.telemetry, "send_due", lambda: pytest.fail("sent from a scheduled run"))
    s._start_usage_send()
    assert not getattr(s, "_usage_sent", False)


# ---- Home's readiness checks


@pytest.fixture
def checked(monkeypatch):
    seen = {}

    def checks(user, list_workspaces, capacities, settings):
        seen.update(user=user, workspaces=list_workspaces(), capacities=capacities)
        return [{"id": "signed_in", "ok": bool(user)}]
    monkeypatch.setattr(readiness, "checks", checks)
    monkeypatch.setattr(service_mod, "FabricClient", lambda token, refresh=None: Fabric(capacities=[{"id": "c1"}]))
    return seen


def test_readiness_without_a_client_id_shows_not_signed_in(svc, checked):
    svc.test_auth.user = None
    assert svc.readiness_check() == [{"id": "signed_in", "ok": False}]
    assert checked["user"] is None and checked["workspaces"] == []


def test_readiness_with_an_expired_sign_in_offers_sign_in(svc, checked, monkeypatch):
    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: None)
    svc.readiness_check()
    assert checked["user"] is None and checked["workspaces"] == []


def test_readiness_reads_capacities_only_with_a_silent_token(svc, checked, monkeypatch):
    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: Fabric())
    svc.readiness_check()
    assert checked["user"] == "chris@contoso.com" and [w["id"] for w in checked["workspaces"]] == ["w1", "w2"]
    assert checked["capacities"]() == [{"id": "c1"}]
    svc.test_auth.silent = None  # Capacity.Read.All not consented: no sign-in window
    with pytest.raises(PermissionError, match="not consented"):
        checked["capacities"]()


def test_the_write_test_writes_with_the_storage_token(svc, monkeypatch):
    from fabric_desktop import onelake
    made = []
    monkeypatch.setattr(onelake, "FileStore", lambda token: made.append(token()) or "store")
    monkeypatch.setattr(readiness, "write_test", lambda store, ws, lh, name: {"store": store, "at": (ws, lh, name)})
    assert svc.readiness_write_test("w1", "lh1", "Sandbox") == {"store": "store", "at": ("w1", "lh1", "Sandbox")}
    assert made == ["storage-token"]


# ---- capacities and the catalog


def test_capacities_list_active_ones_first_by_name(svc, monkeypatch):
    caps = [{"displayName": "zeta", "state": "Active"}, {"displayName": "Alpha", "state": "Paused"},
            {"displayName": "beta", "state": "Active"}]
    monkeypatch.setattr(service_mod, "FabricClient", lambda token, refresh=None: Fabric(capacities=caps))
    assert [c["displayName"] for c in svc.list_capacities()] == ["beta", "zeta", "Alpha"]


@pytest.mark.parametrize("error", ["InsufficientScopes", "AADSTS65001: The user has not consented"])
def test_a_sign_in_that_cant_list_capacities_says_where_to_pick_one(svc, monkeypatch, error):
    monkeypatch.setattr(service_mod, "FabricClient", lambda token, refresh=None: Fabric(error=RuntimeError(error)))
    with pytest.raises(PermissionError, match="pick the workspace's capacity in Fabric"):
        svc.list_capacities()


def test_other_capacity_errors_pass_through(svc, monkeypatch):
    monkeypatch.setattr(service_mod, "FabricClient", lambda token, refresh=None: Fabric(error=ConnectionError("down")))
    with pytest.raises(ConnectionError):
        svc.list_capacities()


@pytest.mark.parametrize("all_types", [False, True])
def test_the_catalog_lists_the_rest_when_a_workspace_cant_be_read(svc, monkeypatch, all_types):
    monkeypatch.setattr(service_mod, "FabricClient", lambda token, refresh=None: Fabric(broken={"w2"}))
    svc.set_favorite("w1-lh", True)
    out = svc.catalog(all_types=all_types)
    assert out["skipped"] == ["Paused"] and out["favorites"] == ["w1-lh"]
    assert out["workspaces"][1] == {"id": "w2", "displayName": "Paused", "type": "Personal"}
    lakehouse = out["items"][0]
    assert lakehouse["lakehouse_id"] == "w1-lh" and lakehouse["type"] == "Lakehouse"
    if all_types:
        assert out["items"][1] == {"workspace_id": "w1", "workspace_name": "Sales", "item_id": "w1-nb", "name": "Load",
                                   "type": "Notebook", "description": "loads"}
    assert svc.test_auth.asked == ["fabric"]  # one token for every thread


def test_a_star_comes_off_again(svc):
    assert svc.set_favorite("lh1", True) == ["lh1"]
    assert svc.set_favorite("lh2", True) == ["lh1", "lh2"]
    assert svc.set_favorite("lh1", False) == ["lh2"]


class Catalog:
    def __init__(self, token):
        pass

    def list_schemas(self, ws, lh):
        if lh == "lh-locked":
            raise PermissionError(f"403 for {PAT}")
        return ["dbo", "sales"]

    def list_tables(self, ws, lh, schema):
        return [f"{schema}_t"]


def test_table_names_are_read_in_the_background_and_a_lakehouse_that_cant_be_read_has_none(svc, monkeypatch, caplog):
    monkeypatch.setattr(service_mod, "TableCatalog", Catalog)
    refs = [{"workspace_id": "w1", "lakehouse_id": "lh1"}, {"workspace_id": "w1", "lakehouse_id": "lh-locked"}]
    with caplog.at_level(logging.WARNING, logger=service_mod.__name__):
        assert svc.catalog_tables(refs) == {"pending": True, "items": {}}
        deadline = time.monotonic() + 10
        while (out := svc.catalog_tables(refs))["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
    assert out == {"pending": False, "items": {"lh1": ["dbo.dbo_t", "sales.sales_t"], "lh-locked": []}}
    assert "Couldn't list the tables of lakehouse lh-locked" in caplog.text and PAT not in caplog.text


def test_listing_a_lakehouses_tables_keeps_them_for_offline_completion(svc, monkeypatch):
    monkeypatch.setattr(service_mod, "TableCatalog", Catalog)
    recorded = []
    monkeypatch.setattr(schema_cache, "record_tables", lambda root, lh, tables: recorded.append((lh, tables)))
    tables = svc.list_tables("w1", "lh1")
    assert tables == {"dbo": ["dbo_t"], "sales": ["sales_t"]} and recorded == [("lh1", tables)]


# ---- notebookutils tokens


def test_notebookutils_tokens_come_from_the_persons_sign_in(svc):
    from fabric_desktop.notebookutils_local import _AUDIENCES
    assert svc._token_for("storage") == "storage-token"
    assert svc._token_for("pbi") == "fabric-token"
    known = next(a for a in _AUDIENCES if a not in ("storage", "pbi"))
    svc._token_for(known)
    svc._token_for("https://contoso.example/")
    assert svc.test_auth.asked == ["storage", "fabric", tuple(_AUDIENCES[known]), ("https://contoso.example/.default",)]


# ---- a cell's result, once gone


class Gone:
    """A kernel whose results are all gone, or that fails another way when ``error`` is set."""

    def __init__(self, error=None):
        self.error = error

    def __getattr__(self, name):
        if not name.startswith("frame_"):
            raise AttributeError(name)

        def call(**kw):
            raise self.error or FrameGone(GONE)
        return call


@pytest.mark.parametrize("ask", [
    lambda s: s.notebook_frame_page("f1"),
    lambda s: s.notebook_frame_chart("f1"),
    lambda s: s.notebook_frame_profile("f1"),
    lambda s: s.notebook_frame_query_profile("f1"),
    lambda s: s.notebook_frame_wrangle("f1", [{"op": "drop"}]),
])
def test_a_result_thats_gone_says_so_and_other_errors_pass_through(svc, monkeypatch, ask):
    monkeypatch.setattr(Service, "kernel", property(lambda self: self.test_kernel))
    for svc.test_kernel in (Gone(), Gone(RuntimeError(GONE))):
        assert ask(svc) == {"frame": "f1", "gone": True, "message": GONE}
    svc.test_kernel = Gone(RuntimeError("DuckDB ran out of memory"))
    with pytest.raises(RuntimeError, match="out of memory"):
        ask(svc)


def test_a_query_profile_explains_what_it_measured(svc, monkeypatch):
    from fabric_desktop import service as s
    monkeypatch.setattr(s.evidence, "notes", lambda profile: ["Most time went to a full scan."])
    kernel = SimpleNamespace(frame_query_profile=lambda frame: {"steps": [], "gone": False})
    monkeypatch.setattr(Service, "kernel", property(lambda self: kernel))
    assert svc.notebook_frame_query_profile("f1")["notes"] == ["Most time went to a full scan."]


def test_a_download_refuses_other_formats_and_leaves_nothing_when_it_fails(svc, monkeypatch, tmp_path):
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(service_mod.known_folders, "downloads", lambda: downloads)
    with pytest.raises(ValueError, match="Download as"):
        svc.notebook_frame_download("f1", "xml")

    def gone(frame, format, path):
        (downloads / (path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] + ".partial")).write_text("half")
        raise FrameGone(GONE)
    monkeypatch.setattr(Service, "kernel", property(lambda self: SimpleNamespace(frame_download=gone)))
    assert svc.notebook_frame_download("f1", "csv", "Orders")["gone"] is True
    assert list(downloads.iterdir()) == []

    def failed(frame, format, path):
        raise OSError("disk full")
    monkeypatch.setattr(Service, "kernel", property(lambda self: SimpleNamespace(frame_download=failed)))
    with pytest.raises(OSError, match="disk full"):
        svc.notebook_frame_download("f1", "parquet")
    assert list(downloads.iterdir()) == []


# ---- shared sessions


def test_a_session_isnt_shared_while_a_proof_runs_or_when_the_notebooks_differ(svc, monkeypatch):
    monkeypatch.setattr(Service, "proof_busy", lambda self: True)
    with pytest.raises(RuntimeError, match="Share sessions when it has finished"):
        svc.kernel_session_share("B", "A")
    with pytest.raises(RuntimeError, match="Cancel the proof first"):
        svc.stop_kernel_process()
    monkeypatch.setattr(Service, "proof_busy", lambda self: False)
    lakehouses = {"A": "lh-1", "B": "lh-2"}
    monkeypatch.setattr(Service, "_saved_lakehouse", lambda self, name: lakehouses[name])
    with pytest.raises(RuntimeError, match="different default lakehouses"):
        svc.kernel_session_share("B", "A")
    lakehouses["B"] = "lh-1"
    envs = {"A": "env-1", "B": None}
    monkeypatch.setattr(Service, "environments", property(lambda self: SimpleNamespace(of=envs.get)))
    with pytest.raises(RuntimeError, match="different environments"):
        svc.kernel_session_share("B", "A")


def test_a_notebook_not_saved_yet_has_no_default_lakehouse(svc):
    assert svc._saved_lakehouse("never saved") is None
