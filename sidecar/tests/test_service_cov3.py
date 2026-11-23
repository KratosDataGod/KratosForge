"""The desktop Service's thin paths: Fabric listings, cells refused before they start, the SQL page's
helpers, files that can't find a free name, and records that can't be cleaned. Fakes only."""

import json
from concurrent.futures import Future
from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import app_settings, service as service_mod, sql_exec
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user-settings.json")
    monkeypatch.setattr(service_mod.known_folders, "downloads", lambda: tmp_path / "Downloads")
    return Service(root=tmp_path / "app")


def _fabric(monkeypatch, fake):
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))


def _auth(monkeypatch, fake):
    monkeypatch.setattr(Service, "auth", property(lambda self: fake))


def _no_free_name(monkeypatch):
    def taken(*args, **kwargs):
        raise FileExistsError(args[0])
    monkeypatch.setattr(service_mod, "open", taken, raising=False)


# ---------------------------------------------------------------- small helpers


def test_a_lakehouse_reference_is_its_id_else_its_name_else_none():
    assert service_mod._lakehouse_key({"lakehouse_id": "L", "name": "n"}) == "L"
    assert service_mod._lakehouse_key({"name": "n"}) == "n"
    assert service_mod._lakehouse_key({}) is None
    assert service_mod._lakehouse_key("LH") == "LH"
    assert service_mod._lakehouse_key("") is None


def test_a_finished_sign_in_setup_for_an_older_app_id_leaves_the_newer_one(svc):
    newer = ("new-id", Future())
    svc._auth_building = newer
    svc._auth_built(Future())
    assert svc._auth_building is newer
    svc._auth_built(newer[1])
    assert svc._auth_building is None


def test_a_new_install_id_is_made_each_time(svc):
    first = svc.telemetry_new_install_id()["install_id"]
    second = svc.telemetry_new_install_id()["install_id"]
    assert first and second and first != second


def test_an_unreadable_sign_in_cache_is_not_signed_in(svc, monkeypatch):
    def broken(path):
        raise OSError("cache locked")
    monkeypatch.setattr(service_mod, "cached_account", broken)
    assert svc._saved_account() == {}


def test_record_cleanups_that_fail_never_stop_the_app(svc, monkeypatch, caplog):
    from fabric_desktop import retention

    def fails(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(retention, "prune_audit", fails)
    monkeypatch.setattr(retention, "prune_receipts", fails)
    svc._prune_records()
    failed = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert "Audit trail cleanup failed (app)" in failed
    assert "Audit trail cleanup failed (integrations)" in failed
    assert "Receipt cleanup failed" in failed


def test_a_saved_notebooks_default_lakehouse_is_its_id(svc, monkeypatch):
    monkeypatch.setattr(svc.notebooks, "load", lambda name: {"lakehouse": {"lakehouse_id": "L1", "name": "LH"}})
    assert svc._saved_lakehouse("nb") == "L1"


def test_skins_are_listed(svc):
    from fabric_desktop import skins
    assert svc.skins_list() == skins.listing()


def test_saving_settings_leaves_kernels_in_the_engine_alone(svc, monkeypatch):
    in_engine = SimpleNamespace()
    monkeypatch.setattr(svc, "sessions", SimpleNamespace(kernels=lambda: [in_engine]))
    limited = []
    monkeypatch.setattr(service_mod, "_apply_limits", lambda kernel: limited.append(kernel))
    out = svc.save_app_settings({"telemetry": False})
    assert out["telemetry"]["value"] is False
    assert limited == []


def test_readiness_without_a_client_id_shows_not_signed_in(svc, monkeypatch):
    monkeypatch.setattr(svc, "_saved_account", lambda: {"username": "chris@example.com"})

    def no_client_id(self):
        raise ValueError("no client ID set")
    monkeypatch.setattr(Service, "auth", property(no_client_id))
    monkeypatch.setattr(service_mod.net_trust, "explain", lambda exc: None)
    seen = {}
    from fabric_desktop import readiness

    def checks(user, workspaces, capacities, settings, clock, folder, local_folders=None):
        seen.update(user=user, workspaces=workspaces)
        return [{"id": "sign_in"}]
    monkeypatch.setattr(readiness, "checks", checks)
    assert svc.readiness_check() == [{"id": "sign_in"}]
    assert seen["user"] is None and seen["workspaces"] is list


# ---------------------------------------------------------------- Fabric listings


def test_fabric_listings_pass_through(svc, monkeypatch):
    calls = []

    class Fabric:
        def __getattr__(self, name):
            return lambda *a: calls.append((name, a)) or [name]
    _fabric(monkeypatch, Fabric())
    assert svc.list_workspaces() == ["list_workspaces"]
    assert svc.list_lakehouses("w") == ["list_lakehouses"]
    assert svc.list_items("w") == ["list_items"]
    assert svc.create_lakehouse("w", "LH") == ["create_lakehouse"]
    assert calls == [("list_workspaces", ()), ("list_lakehouses", ("w",)), ("list_items", ("w",)),
                     ("create_lakehouse", ("w", "LH"))]


def test_a_reloaded_catalog_reads_table_names_again(svc, monkeypatch):
    _auth(monkeypatch, SimpleNamespace(fabric_token=lambda: "t"))
    monkeypatch.setattr(service_mod, "FabricClient", lambda token: SimpleNamespace(list_workspaces=lambda: []))
    svc._tables = {"pending": False, "items": {"L": ["dbo.a"]}}
    out = svc.catalog()
    assert out["items"] == [] and out["skipped"] == []
    assert svc._tables is None
    svc._tables = {"pending": True, "items": {}}  # still being read: kept
    svc.catalog()
    assert svc._tables == {"pending": True, "items": {}}


def test_jobs_preview_and_run(svc, monkeypatch):
    monkeypatch.setattr(service_mod.Job, "load", staticmethod(lambda path: f"job:{path}"))
    monkeypatch.setattr(service_mod, "run_local", lambda job: pa.table({"a": [1, 2, 3]}))
    assert svc.preview_job("j.toml", limit=2) == {"rows": 3, "columns": ["a"], "sample": [{"a": 1}, {"a": 2}]}
    _fabric(monkeypatch, "fabric")
    _auth(monkeypatch, SimpleNamespace(storage_token="token"))
    seen = {}

    def run(job, fabric, token, **kw):
        seen.update(job=job, fabric=fabric, token=token, **kw)
        return SimpleNamespace(status="ok", rows=3)
    monkeypatch.setattr(service_mod, "run_job", run)
    assert svc.run_job("j.toml") == {"status": "ok", "rows": 3}
    assert seen["job"] == "job:j.toml" and seen["fabric"] == "fabric" and seen["work_dir"] == svc.root


def test_files_in_a_lakehouse_are_listed(svc, monkeypatch):
    _auth(monkeypatch, SimpleNamespace(storage_token="token"))
    monkeypatch.setattr(service_mod, "FileStore",
                        lambda token: SimpleNamespace(list=lambda ws, lh, d: [{"path": f"{ws}/{lh}/{d}"}]))
    assert svc.list_files("w", "l") == [{"path": "w/l/Files"}]


# ---------------------------------------------------------------- cells


def _cell_ready(svc, monkeypatch, kernel):
    monkeypatch.setattr(svc, "proof_busy", lambda: False)
    monkeypatch.setattr(svc, "_replay_guard", lambda notebook: None)
    monkeypatch.setattr(svc, "sessions", SimpleNamespace(
        host_of=lambda nb: None, reap_idle=lambda: None, in_process=False,
        session=lambda nb: SimpleNamespace(kernel=kernel)))


def test_a_cell_waits_for_an_external_run_and_then_gives_up(svc, monkeypatch):
    _cell_ready(svc, monkeypatch, None)
    svc._attach_lock = SimpleNamespace(acquire=lambda timeout: False)
    with pytest.raises(RuntimeError, match="external tool is running"):
        svc.start_cell("1", notebook="a")


def test_a_cell_in_another_busy_notebook_says_which_one(svc, monkeypatch):
    kernel = SimpleNamespace(poll=lambda: {"status": "running"}, notebook_name="b")
    _cell_ready(svc, monkeypatch, kernel)
    with pytest.raises(RuntimeError, match="already running in b"):
        svc.start_cell("1", notebook="a")
    assert svc.attach_lock.acquire(timeout=0)  # the lock was let go
    svc.attach_lock.release()
    with pytest.raises(RuntimeError, match="A cell is already running. Wait for it"):
        svc.start_cell("1", notebook="b")  # the busy notebook itself


def test_completion_and_editor_helpers(svc, monkeypatch):
    monkeypatch.setattr(svc, "sessions", SimpleNamespace(
        kernel=lambda nb: SimpleNamespace(complete=lambda code, cursor: {"matches": [code], "cursor": cursor})))
    assert svc.kernel_complete("pri", 3, "nb") == {"matches": ["pri"], "cursor": 3}
    assert svc.notebook_syntax("x = (")  # an unclosed bracket is underlined
    assert svc.notebook_syntax("x = 1") == []
    from fabric_desktop import chart_output, nbsymbols
    monkeypatch.setattr(nbsymbols, "find_symbol", lambda cells, cell, offset, name: (cells, cell, offset, name))
    assert svc.notebook_symbol([{"source": "x"}], "1", "2", "") == ([{"source": "x"}], 1, 2, None)
    assert svc.notebook_symbol([], name="x")[3] == "x"
    monkeypatch.setattr(chart_output, "plotly_js", lambda: {"js": "plotly"})
    assert svc.notebook_plotly_js() == {"js": "plotly"}
    rules = svc.notebook_practice_rules()
    assert rules and all("id" in r for r in rules)
    assert set(svc.notebook_practices(["x = 1"])) == {"counts", "findings"}


# ---------------------------------------------------------------- the SQL page


def test_sql_help_and_practices(svc, monkeypatch):
    monkeypatch.setattr(service_mod.sql_function_help, "function_help", lambda dialect, name: {"d": dialect, "n": name})
    assert svc.sql_function_help("upper") == {"d": "spark", "n": "upper"}
    rules = svc.sql_practice_rules()
    assert rules and all("id" in r for r in rules)
    assert {"counts", "findings"} <= set(svc.sql_practices("SELECT * FROM t"))


def test_a_compatibility_report_with_no_free_name_isnt_written(svc, monkeypatch):
    _no_free_name(monkeypatch)
    with pytest.raises(ValueError, match="free file name in Downloads for the report"):
        svc.sql_compat_export("SELECT 1")


_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect x="1" y="1" fill="{fill}"/></svg>'


def test_a_diagram_with_a_link_in_a_value_is_refused(svc):
    with pytest.raises(ValueError, match="a link in fill"):
        svc.sql_diagram_export(_SVG.format(fill="url(#x)"))


def test_a_diagram_with_no_free_name_isnt_written(svc, monkeypatch):
    _no_free_name(monkeypatch)
    with pytest.raises(ValueError, match="free file name in Downloads for the diagram"):
        svc.sql_diagram_export(_SVG.format(fill="#fff"))


class _Runs:
    def __getattr__(self, name):
        return lambda *a, **kw: {"op": name, "args": list(a), **kw}


def test_sql_run_calls_pass_through(svc):
    svc._sql_runs = _Runs()
    assert svc.sql_executions() == {"op": "recent", "args": []}
    assert svc.sql_pin("e1", 1) == {"op": "pin", "args": ["e1", True]}
    assert svc.sql_cancel("e1") == {"op": "cancel", "args": ["e1"]}
    assert svc.sql_export("e1", "parquet", "n", 2, raw=1) == {"op": "export", "args": ["e1", "parquet", "n", 2], "raw": True}
    assert svc.sql_export_status("x1") == {"op": "export_status", "args": ["x1"]}
    assert svc.sql_export_cancel("x1") == {"op": "export_cancel", "args": ["x1"]}


def test_column_stats_of_a_gone_snapshot_ask_for_a_rerun(svc):
    def gone(*a):
        raise sql_exec.RerunNeeded("run it again")
    svc._sql_runs = SimpleNamespace(column_stats=gone)
    assert svc.sql_column_stats("e1", 0) == {"id": "e1", "rerun_needed": True, "message": "run it again"}


def test_sql_files_are_read_and_described(svc, tmp_path):
    path = tmp_path / "q.sql"
    path.write_text("SELECT 1\n", encoding="utf-8")
    read = svc.sql_file_read(str(path))
    assert read["text"] == "SELECT 1\n"
    info = svc.sql_file_info(str(path))
    assert info["version"] == read["version"]


def test_a_query_export_with_no_free_name_isnt_written(svc, monkeypatch):
    monkeypatch.setattr(svc, "_attach", lambda ref: None)

    class Stream:
        def __init__(self, query, dialect):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(service_mod.sql, "Stream", Stream)
    _no_free_name(monkeypatch)
    with pytest.raises(ValueError, match="free file name in Downloads for the export"):
        svc.export_sql_csv("SELECT 1", {"name": "LH"})


def test_a_notebook_is_compared_with_its_last_commit(svc, monkeypatch, tmp_path):
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps({"cells": [{"id": "c1", "cell_type": "code", "source": "x = 2"}]}), encoding="utf-8")
    monkeypatch.setattr(svc, "notebooks", SimpleNamespace(path=lambda name: path))
    old = {"cells": [{"id": "c1", "cell_type": "code", "source": "x = 1"}]}
    monkeypatch.setattr(service_mod.release, "committed_file", lambda p: json.dumps(old))
    out = svc.notebook_diff("nb")
    assert out["against"] == "head" and out["problem"] is None
    assert [c["kind"] for c in out["cells"]] == ["changed"]
