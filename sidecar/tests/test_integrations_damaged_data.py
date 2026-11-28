"""The read-only tools an external AI tool calls, on data that is damaged, odd or changing while it reads.

Each test builds real data with the real engine (a local data project with a Delta table, a metadata
project that has run, saved notebooks, proofs, side-by-side runs and receipts), damages it on disk the
way a power cut, a sync client, another program or a hand edit would, and calls the tool through
``Registry.invoke`` (and, for a sample, over MCP in memory). Every call must end cleanly: a normal
result that matches the tool's output schema, or an IntegrationError with a code a caller can act on.
Never ``internal``, never a traceback, never the full path of this PC's data folder, never a hang.

The MCP server is a process of its own: it reads each local data project's saved catalog
(project.json) and Delta logs and never opens a project's DuckDB file, which the app keeps open.
``no_database_opened`` holds every test here to that."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import anyio
import duckdb
import pytest

from fabric_desktop import receipts
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, validate
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.service import Service

SECONDS = 20  # a bound on one call: generous for a slow CI machine, far below a hang
CLEAN = {"invalid_input", "not_found", "failed", "permission_denied", "timeout"}  # what damaged data may give


# ---- the contract every call here is held to

@pytest.fixture
def reg():
    r = Registry()
    register_core(r)
    return r


@pytest.fixture(autouse=True)
def no_database_opened(monkeypatch):
    """No tool call opens a .duckdb file. A call runs on run_call's "integration-call" thread; the app's
    own writes (the writer threads below, the fixtures) don't count."""
    opened: list[str] = []
    real = duckdb.connect

    def spy(database: str = ":memory:", *args, **kwargs):
        if threading.current_thread().name == "integration-call" and str(database).lower().endswith(".duckdb"):
            opened.append(str(database))
        return real(database, *args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", spy)
    yield
    assert opened == [], f"a tool opened a project's database: {opened}"


def _places(tmp: Path) -> list[str]:
    return [str(tmp), str(tmp.resolve()), str(tmp).replace("\\", "/"), str(tmp.resolve()).replace("\\", "/")]


def call(reg, svc, cap_id, tmp: Path, codes=CLEAN, **args):
    """The tool's result, or its IntegrationError, after checking the call ended cleanly."""
    started = time.monotonic()
    try:
        out = reg.invoke(svc, cap_id, args, CallContext(Caller("t", "test"), timeout=SECONDS))
    except IntegrationError as err:
        took = time.monotonic() - started
        text = json.dumps(err.to_dict(), ensure_ascii=False)
        assert err.code in codes, f"{cap_id}: {err.code}: {err.message}"
        assert err.code != "internal", f"{cap_id}: {err.message}"
        assert "Traceback" not in text and "<unknown>" not in text and "\x1b[" not in text, text
        assert not any(p in text or p.replace("\\", "\\\\") in text for p in _places(tmp)), text
        assert took < SECONDS + 2, f"{cap_id} took {took:.1f}s"
        return err
    took = time.monotonic() - started
    assert took < SECONDS + 2, f"{cap_id} took {took:.1f}s"
    assert validate(reg.get(cap_id).output_schema, out) is not None  # never a garbled result shown as good
    json.dumps(out)
    return out


def ok(result):
    assert not isinstance(result, IntegrationError), f"{result.code}: {result.message}"
    return result


def failed(result, *codes):
    assert isinstance(result, IntegrationError), f"expected an error, got {str(result)[:200]}"
    if codes:
        assert result.code in codes, f"{result.code}: {result.message}"
    return result


# ---- local data projects: project.json and Delta logs

@pytest.fixture
def app(tmp_path):
    """The app's engine: a project reading a CSV folder and holding a Delta table with two versions,
    its database left open."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "orders.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    s = Service(root=tmp_path / "app")
    s.pid = s.local_create_project("Sales")["id"]
    s.local_add_source(s.pid, str(data))
    s.local_write_delta(s.pid, "main.big_orders", "SELECT * FROM orders WHERE amount > 5")
    s.local_write_delta(s.pid, "main.big_orders", "SELECT 3 AS id, 30 AS amount", mode="append")
    proj = s.local_data.store.load(s.pid)
    s.project_json = Path(proj.folder) / "project.json"
    s.delta_log = Path(proj.catalog["main.big_orders"]["folder"]) / "_delta_log"
    assert s.local_data.engine(s.pid).is_open
    yield s
    s.local_data.close_all()


@pytest.fixture
def tool(app, tmp_path):
    """The MCP server's own engine on the same data folder."""
    s = Service(root=tmp_path / "app")
    yield s
    s.local_data.close_all()


def opened(svc) -> list:
    return [pid for pid, e in svc.local_data._engines.items() if e.is_open]


def _with(raw: bytes, **changes) -> bytes:
    return json.dumps(json.loads(raw) | changes).encode("utf-8")


PROJECT_DAMAGE = {
    "empty": lambda good: b"",
    "truncated": lambda good: good[: len(good) // 2],
    "invalid JSON": lambda good: b"{not json",
    "a list": lambda good: b"[1, 2]",
    "a string": lambda good: b'"Sales"',
    "catalog is a list": lambda good: _with(good, catalog=[1]),
    "settings of the wrong type": lambda good: _with(good, settings={"threads": "many"}),
    "unknown setting": lambda good: _with(good, settings={"zzz": 1}),
    "source of the wrong shape": lambda good: _with(good, sources=[{"nope": 1}]),
    "name of the wrong type": lambda good: _with(good, name=5),
    "not UTF-8": lambda good: b"\xff\xfe\x00garbage",
    "nested too deeply": lambda good: b"[" * 100_000,
}


@pytest.mark.parametrize("damage", PROJECT_DAMAGE, ids=list(PROJECT_DAMAGE))
def test_a_damaged_project_json_is_left_out_of_the_list_and_named_when_asked_for(reg, app, tool, tmp_path, damage):
    app.project_json.write_bytes(PROJECT_DAMAGE[damage](app.project_json.read_bytes()))
    assert ok(call(reg, tool, "fabric_desktop_list_local_projects", tmp_path))["projects"] == []
    for cap_id, args in (("fabric_desktop_get_local_tables", {}), ("fabric_desktop_get_local_table_history", {"table": "main.big_orders"})):
        err = failed(call(reg, tool, cap_id, tmp_path, project=app.pid, **args), "invalid_input", "failed")
        assert "project.json" in err.message, err.message
    assert opened(tool) == []


def test_a_project_json_that_is_a_folder_is_a_clean_failure(reg, app, tool, tmp_path):
    app.project_json.unlink()
    app.project_json.mkdir()
    assert ok(call(reg, tool, "fabric_desktop_list_local_projects", tmp_path))["projects"] == []
    failed(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid), "failed")


def test_a_catalog_entry_that_isnt_an_object_is_named_not_a_crash(reg, app, tool, tmp_path):
    # Was: internal, "AttributeError: 'str' object has no attribute 'get'".
    raw = json.loads(app.project_json.read_bytes())
    raw["catalog"]["main.junk"] = "zzz"
    raw["catalog"]["main.big_orders"]["folder"] = 5
    app.project_json.write_text(json.dumps(raw), encoding="utf-8")
    out = ok(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid))
    assert out["unreadable"] == ["main.junk"] and {t["table"] for t in out["items"]} == {"orders", "big_orders"}
    # Was: internal, "TableNotFoundError: Local path "5" does not exist".
    failed(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders"),
           "invalid_input")


@pytest.mark.parametrize("damage", ["empty", "a list", "a bad entry", "truncated"])
def test_a_damaged_list_of_projects_is_rebuilt_or_left_out_cleanly(reg, app, tool, tmp_path, damage):
    index = tmp_path / "app" / "local-data" / "projects.json"
    good = index.read_bytes()
    index.write_bytes({"empty": b"", "a list": b"[1]", "a bad entry": b'{"sales": 5}', "truncated": good[:10]}[damage])
    out = ok(call(reg, tool, "fabric_desktop_list_local_projects", tmp_path))
    assert [p["id"] for p in out["projects"]] in ([app.pid], [])
    call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid)


def _commits(log: Path) -> list[Path]:
    return sorted(p for p in log.iterdir() if p.suffix == ".json" and p.is_file())


DELTA_DAMAGE = {
    "first version missing": lambda log: _commits(log)[0].unlink(),
    "last commit cut off": lambda log: _commits(log)[-1].write_bytes(_commits(log)[-1].read_bytes()[:30]),
    "first commit cut off": lambda log: _commits(log)[0].write_bytes(_commits(log)[0].read_bytes()[:30]),
    "commit not UTF-8": lambda log: _commits(log)[-1].write_bytes(b"\xff\xfe\xfa"),
    "a gap in the versions": lambda log: (log / f"{5:020d}.json").write_bytes(b'{"commitInfo":{}}\n'),
    "checkpoint missing": lambda log: (log / "_last_checkpoint").write_text('{"version":1,"size":3}', encoding="utf-8"),
    "add without a path": lambda log: _commits(log)[-1].write_text("\n".join(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "path"} if k == "add" else v for k, v in json.loads(line).items()})
        for line in _commits(log)[-1].read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8"),
    "no log at all": lambda log: shutil.rmtree(log),
    "no table folder": lambda log: shutil.rmtree(log.parent),
}


@pytest.mark.parametrize("damage", DELTA_DAMAGE, ids=list(DELTA_DAMAGE))
def test_a_damaged_delta_log_is_a_clean_failure(reg, app, tool, tmp_path, damage):
    # Was: internal, with deltalake's native backtrace ("0: <unknown>") and the table's full path.
    DELTA_DAMAGE[damage](app.delta_log)
    err = failed(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders"),
                 "failed", "not_found")
    assert "\n" not in err.message.strip() or "Delta" in err.message
    ok(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid))  # the catalog still reads
    assert opened(tool) == []


@pytest.mark.parametrize("damage", ["unknown action", "huge commit", "empty last commit", "checkpoint hint garbled"])
def test_odd_but_readable_delta_logs_give_the_history(reg, app, tool, tmp_path, damage):
    last = _commits(app.delta_log)[-1]
    if damage == "unknown action":
        last.write_bytes(last.read_bytes() + b'{"weirdAction":{"x":1}}\n')
    elif damage == "huge commit":  # ~24 MB of commitInfo in one commit file
        last.write_bytes(last.read_bytes() + (b'{"commitInfo":{"note":"' + b"a" * (1 << 20) + b'"}}\n') * 24)
    elif damage == "empty last commit":
        last.write_bytes(b"")
    else:
        (app.delta_log / "_last_checkpoint").write_text("garbage", encoding="utf-8")
    out = ok(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders"))
    assert out["versions"] and out["versions"][0]["version"] == out["version"]


def test_a_commit_that_is_a_folder_reads_as_the_versions_before_it(reg, app, tool, tmp_path):
    last = _commits(app.delta_log)[-1]
    last.unlink()
    last.mkdir()
    out = call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders")
    if not isinstance(out, IntegrationError):
        assert out["version"] == 0 and [v["version"] for v in out["versions"]] == [0]


def test_the_history_of_a_table_the_app_made_after_the_tools_first_call_is_found(reg, app, tool, tmp_path):
    # Was: invalid_input "isn't a Delta table": the tool kept the catalog its first call loaded, for good.
    ok(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders"))
    app.local_write_delta(app.pid, "main.later", "SELECT 1 AS id")
    out = ok(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.later"))
    assert out["table"] == "main.later" and out["version"] == 0
    assert opened(tool) == []


def test_a_project_json_read_just_as_the_app_swaps_in_a_new_one_is_waited_out(reg, app, tool, tmp_path, monkeypatch):
    # On Windows a file being replaced (jsonfile.write_text) refuses a reader for a moment. Was: permission_denied.
    real, refused = Path.read_text, []

    def busy(self, *args, **kwargs):
        if self.name == "project.json" and not refused:
            refused.append(self)
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", busy)
    ok(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid))
    assert refused


def test_unicode_long_and_spaced_project_names_are_listed(reg, tmp_path):
    s = Service(root=tmp_path / "app")
    names = ["Ventes été 2026 ✓", "Sales  with  spaces", "x" * 100, "日本語のプロジェクト"]
    for name in names:
        s.local_create_project(name)
    tool = Service(root=tmp_path / "app")
    out = ok(call(reg, tool, "fabric_desktop_list_local_projects", tmp_path))
    assert sorted(p["name"] for p in out["projects"]) == sorted(names)
    for p in out["projects"]:
        assert ok(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=p["id"]))["total"] == 0
    s.local_data.close_all()


# ---- a project changing while it is read

def test_a_project_being_written_while_it_is_read_reads_as_the_old_or_the_new(reg, app, tool, tmp_path):
    """One thread appends Delta commits (each saves project.json), another keeps saving project.json
    as the app does, while the tools read the project again and again. Each read is the project as
    one save left it: every table with its columns, a history whose versions count down from the
    table's version without a gap. A retryable error is the only error allowed."""
    stop = threading.Event()
    problems: list[BaseException] = []
    engine = app.local_data.engine(app.pid)

    def appender():
        try:
            for i in range(15):
                app.local_write_delta(app.pid, "main.big_orders", f"SELECT {10 + i} AS id, {i} AS amount", mode="append")
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            problems.append(exc)
        finally:
            stop.set()

    def saver():
        try:
            while not stop.is_set():
                app.local_data.store.save(engine.project)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            problems.append(exc)

    threads = [threading.Thread(target=appender), threading.Thread(target=saver)]
    for t in threads:
        t.start()
    seen, reads = set(), 0
    try:
        while not stop.is_set() or reads < 6:
            for cap_id, args in (("fabric_desktop_list_local_projects", {}),
                                 ("fabric_desktop_get_local_tables", {"project": app.pid}),
                                 ("fabric_desktop_get_local_table_history", {"project": app.pid, "table": "main.big_orders"})):
                out = call(reg, tool, cap_id, tmp_path, **args)
                if isinstance(out, IntegrationError):
                    assert out.retryable, f"{cap_id}: {out.code}: {out.message}"
                    continue
                if cap_id == "fabric_desktop_get_local_tables":
                    assert {t["table"] for t in out["items"]} == {"orders", "big_orders"}
                    assert all(isinstance(t.get("columns", []), list) for t in out["items"])
                elif cap_id == "fabric_desktop_get_local_table_history":
                    versions = [v["version"] for v in out["versions"]]
                    assert versions == list(range(out["version"], out["version"] - len(versions), -1)), versions
                    seen.add(out["version"])
                else:
                    assert [p["id"] for p in out["projects"]] == [app.pid]
            reads += 1
    finally:
        stop.set()
        for t in threads:
            t.join(60)
    assert not problems, problems
    final = ok(call(reg, tool, "fabric_desktop_get_local_table_history", tmp_path, project=app.pid, table="main.big_orders"))
    assert final["version"] == 16 and len(seen) >= 1
    assert opened(tool) == []


# ---- permission denied

def _chmod_restricts(tmp: Path) -> bool:
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return False
    probe = tmp / "probe"
    probe.write_text("x", encoding="utf-8")
    probe.chmod(0)
    try:
        probe.read_text(encoding="utf-8")
        return False
    except PermissionError:
        return True
    finally:
        probe.chmod(0o600)


def test_files_that_cant_be_read_are_permission_denied_without_their_path(reg, app, tool, tmp_path):
    if not _chmod_restricts(tmp_path):
        pytest.skip("chmod doesn't stop this user reading a file here")
    tool.notebooks.save("locked", [{"type": "code", "source": "x = 1"}])
    nb = tool.notebooks.path("locked")
    for path in (app.project_json, nb):
        path.chmod(0)
    try:
        failed(call(reg, tool, "fabric_desktop_get_local_tables", tmp_path, project=app.pid), "permission_denied")
        failed(call(reg, tool, "fabric_desktop_get_notebook", tmp_path, name="locked"), "permission_denied")
        ok(call(reg, tool, "fabric_desktop_list_local_projects", tmp_path))
        ok(call(reg, tool, "fabric_desktop_list_notebooks", tmp_path))
    finally:
        for path in (app.project_json, nb):
            path.chmod(0o600)


def test_a_notebook_that_cant_be_opened_is_permission_denied_without_its_path(reg, tmp_path, monkeypatch):
    # The same refusal, made without chmod so it runs everywhere. Was: the message named the full path.
    svc = Service(root=tmp_path / "app")
    svc.notebooks.save("locked", [{"type": "code", "source": "x = 1"}])
    real = Path.read_text

    def refuse(self, *args, **kwargs):
        if self.name == "locked.ipynb":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    err = failed(call(reg, svc, "fabric_desktop_get_notebook", tmp_path, name="locked"), "permission_denied")
    assert "locked.ipynb" in err.message


# ---- notebooks

NB_TOOLS = ("fabric_desktop_get_notebook", "fabric_desktop_validate_notebook", "fabric_desktop_get_notebook_dependencies",
            "fabric_desktop_get_notebook_environment", "fabric_desktop_check_proof", "fabric_desktop_placement_advice")

NOTEBOOK_DAMAGE = {
    "empty": b"",
    "invalid JSON": b"{nope",
    "a list": b"[]",
    "a string": b'"x"',
    "cells are an object": b'{"cells": {"a": 1}}',
    "cells that aren't objects": b'{"cells": ["abc", 5]}',
    "a source that is a number": b'{"cells": [{"cell_type": "code", "source": 5}]}',
    "a source list with a number": b'{"cells": [{"cell_type": "code", "source": ["a", 5]}]}',
    "metadata is a list": b'{"cells": [], "metadata": []}',
    "our metadata is text": b'{"cells": [], "metadata": {"fabric_desktop": "x"}}',
    "the lakehouse is text": b'{"cells": [], "metadata": {"fabric_desktop": {"lakehouse": "x"}}}',
    "cell metadata is a list": b'{"cells": [{"cell_type": "code", "source": "x", "metadata": [1]}]}',
    "nbformat-invalid": b'{"nbformat": "four", "cells": [{"cell_type": 5, "source": "x = 1", "outputs": "none"}]}',
    "not UTF-8": b"\xff\xfe\x00",
    "nested too deeply": b"[" * 100_000,
}


@pytest.fixture
def nbsvc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.notebooks.save("good", [{"type": "code", "source": "x = 1"}])
    return s


# A file that still has readable cells: nbformat-invalid parts the app doesn't use, and metadata of
# the wrong type, whose settings the notebook reader drops (#3176).
CELL_NAMED = {"cells that aren't objects", "a source that is a number", "a source list with a number"}
READ_ANYWAY = {"nbformat-invalid", "metadata is a list", "our metadata is text", "cell metadata is a list"}


@pytest.mark.parametrize("damage", NOTEBOOK_DAMAGE, ids=list(NOTEBOOK_DAMAGE))
def test_a_damaged_notebook_is_named_by_every_notebook_tool(reg, nbsvc, tmp_path, damage):
    # Was: internal (AttributeError, 'list' object has no attribute 'get') for JSON that isn't a notebook.
    (nbsvc.notebooks.folder / "bad.ipynb").write_bytes(NOTEBOOK_DAMAGE[damage])
    for cap_id in NB_TOOLS:
        out = call(reg, nbsvc, cap_id, tmp_path, name="bad")
        if damage in READ_ANYWAY:  # the reader drops the settings it can't read and keeps the cells
            assert not isinstance(out, IntegrationError) or out.code not in ("internal", "invalid_input"), (cap_id, out)
        else:
            # The file is named, and so is the cell when one is the problem (the reader's cell check, #3232).
            start = "bad.ipynb can't be opened, because" if damage in CELL_NAMED else "bad.ipynb is damaged"
            assert failed(out, "invalid_input").message.startswith(start), cap_id
    names = [n["name"] for n in ok(call(reg, nbsvc, "fabric_desktop_list_notebooks", tmp_path))["items"]]
    assert set(names) == {"good", "bad"}
    ok(call(reg, nbsvc, "fabric_desktop_get_project", tmp_path, id="notebooks"))
    ok(call(reg, nbsvc, "fabric_desktop_list_projects", tmp_path))
    ok(call(reg, nbsvc, "fabric_desktop_get_notebook", tmp_path, name="good"))


def test_binary_outputs_stay_out_and_a_huge_notebook_reads_in_time(reg, nbsvc, tmp_path):
    blob = "iVBORw0KGgo" * 800_000  # ~9 MB of base64 image
    big = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "code", "source": "x = 1\n" * 2000, "metadata": {}, "execution_count": 1, "outputs": [
            {"output_type": "display_data", "metadata": {}, "data": {"image/png": blob, "application/octet-stream": "\u0000\u0001�"}},
            {"output_type": "stream", "name": "stdout", "text": "\u0000\u0007 bytes \udcff".encode("utf-8", "surrogatepass").decode("utf-8", "replace")}]}]}
    (nbsvc.notebooks.folder / "big.ipynb").write_text(json.dumps(big), encoding="utf-8")
    out = ok(call(reg, nbsvc, "fabric_desktop_get_notebook", tmp_path, name="big"))
    assert out["cells"][0]["lines"] == 2000 and "iVBOR" not in json.dumps(out)
    for cap_id in ("fabric_desktop_get_notebook_dependencies", "fabric_desktop_validate_notebook"):
        call(reg, nbsvc, cap_id, tmp_path, name="big")


def test_a_notebook_file_that_is_a_folder_is_left_out_and_a_clean_failure(reg, nbsvc, tmp_path):
    (nbsvc.notebooks.folder / "folder.ipynb").mkdir()
    names = [n["name"] for n in ok(call(reg, nbsvc, "fabric_desktop_list_notebooks", tmp_path))["items"]]
    assert names == ["good"]
    # Was: internal, "IsADirectoryError: [Errno 21] Is a directory: '<full path>'".
    failed(call(reg, nbsvc, "fabric_desktop_get_notebook", tmp_path, name="folder"), "failed")


def test_odd_notebook_names_list_and_open_or_are_refused_cleanly(reg, nbsvc, tmp_path):
    good = nbsvc.notebooks.path("good").read_text(encoding="utf-8")
    names = ["ünï cødé", "a b", "x" * 180, "日本語"]
    if sys.platform != "win32":  # names Windows itself refuses: made here to see they never break a list
        names += ["CON", "NUL", "trailing.", "trailing space ", "com1"]
    for name in names:
        (nbsvc.notebooks.folder / f"{name}.ipynb").write_text(good, encoding="utf-8")
    listed = [n["name"] for n in ok(call(reg, nbsvc, "fabric_desktop_list_notebooks", tmp_path))["items"]]
    assert set(listed) == {"good", *names}
    for name in listed:
        if len(name) <= 200:
            call(reg, nbsvc, "fabric_desktop_get_notebook", tmp_path, name=name)
    for name in ("ünï cødé", "a b", "x" * 180, "日本語"):
        ok(call(reg, nbsvc, "fabric_desktop_get_notebook", tmp_path, name=name))


# ---- proofs, side-by-side runs and receipts

RECORD = {"id": "p1", "name": "good", "state": "done", "started_at": "2026-01-01T00:00:00Z",
          "pc": {"cells": {"1": {"outputs": ["rows"]}}}, "local": {"cells": {"1": {"outputs": ["rows"]}}}, "tables": []}

RUNS_DAMAGE = {
    "a line cut off": json.dumps(RECORD) + "\n" + json.dumps(RECORD | {"id": "p2"})[:25],
    "a line that isn't an object": json.dumps(RECORD) + "\n5\n[1]\n",
    "a line without an id": json.dumps(RECORD) + '\n{"name": "x"}\n',
    "a side that is text": json.dumps(RECORD | {"pc": "x", "local": "x"}) + "\n",
    "started_at of two types": json.dumps(RECORD) + "\n" + json.dumps(RECORD | {"id": "p2", "started_at": 5}) + "\n",
    "blank lines": "\n\n" + json.dumps(RECORD) + "\n\n",
}


@pytest.mark.parametrize("damage", RUNS_DAMAGE, ids=list(RUNS_DAMAGE))
@pytest.mark.parametrize("kind", ["proofs", "side_by_side"])
def test_damaged_proof_and_side_by_side_logs_keep_the_rows_that_read(reg, nbsvc, tmp_path, kind, damage):
    # Was: one cut-off line made every proof invalid_input ("Expecting value"); a line that isn't an
    # object, or a side that is text, was internal (AttributeError).
    path = nbsvc.root / f"{kind}.jsonl"
    path.write_text(RUNS_DAMAGE[damage], encoding="utf-8")
    listed = ok(call(reg, nbsvc, f"fabric_desktop_list_{kind}", tmp_path))
    assert "p1" in [r["id"] for r in listed["items"]]
    assert "rows" not in json.dumps(listed)
    got = ok(call(reg, nbsvc, f"fabric_desktop_get_{'proof' if kind == 'proofs' else kind}", tmp_path, id="p1"))
    assert got["id"] == "p1" and "rows" not in json.dumps(got)
    failed(call(reg, nbsvc, f"fabric_desktop_get_{'proof' if kind == 'proofs' else kind}", tmp_path, id="nope"), "not_found")


@pytest.mark.parametrize("damage", ["not UTF-8", "a folder", "empty"])
@pytest.mark.parametrize("kind", ["proofs", "side_by_side"])
def test_a_proof_or_side_by_side_log_that_cant_be_read_at_all_is_clean(reg, nbsvc, tmp_path, kind, damage):
    path = nbsvc.root / f"{kind}.jsonl"
    if damage == "a folder":
        path.mkdir()
    else:
        path.write_bytes(b"\xff\xfe\n" if damage == "not UTF-8" else b"")
    listed = call(reg, nbsvc, f"fabric_desktop_list_{kind}", tmp_path)
    if not isinstance(listed, IntegrationError):
        assert listed["items"] == []
    call(reg, nbsvc, f"fabric_desktop_get_{'proof' if kind == 'proofs' else kind}", tmp_path, id="p1")


def _raw_receipt(folder: Path, data: bytes) -> str:
    import hashlib
    rid = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{rid}.json").write_bytes(data)
    return rid


RECEIPT_DAMAGE = {"a list": b"[1, 2]", "a string": b'"x"', "invalid JSON": b"{x", "not UTF-8": b"\xff\xfe",
                  "empty": b"", "nested too deeply": b"[" * 100_000}


@pytest.mark.parametrize("damage", RECEIPT_DAMAGE, ids=list(RECEIPT_DAMAGE))
def test_a_receipt_that_isnt_one_is_listed_as_invalid_and_named(reg, nbsvc, tmp_path, damage):
    # Was: internal (AttributeError) for JSON that isn't an object, and a list that failed outright.
    good = nbsvc.receipts.put(receipts.new_receipt("proof", notebook={"name": "good"}, tables=[]))
    rid = _raw_receipt(nbsvc.root / "receipts", RECEIPT_DAMAGE[damage])
    rows = {r["id"]: r for r in ok(call(reg, nbsvc, "fabric_desktop_list_receipts", tmp_path))["items"]}
    assert rows[good]["valid"] is True and rows[rid]["valid"] is False
    failed(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id=rid), "failed")
    ok(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id=good))


def test_receipts_changed_misnamed_or_folders_are_clean(reg, nbsvc, tmp_path):
    folder = nbsvc.root / "receipts"
    good = nbsvc.receipts.put(receipts.new_receipt("proof", notebook={"name": "good"}, tables=[]))
    odd = _raw_receipt(folder, b'{"notebook": "x", "verification": 5, "created_at": 7}')
    (folder / ("a" * 64 + ".json")).write_text('{"changed": true}', encoding="utf-8")
    (folder / ("b" * 64 + ".json")).mkdir()
    (folder / "ünï name with spaces.json").write_text("{}", encoding="utf-8")
    rows = {r["id"]: r for r in ok(call(reg, nbsvc, "fabric_desktop_list_receipts", tmp_path))["items"]}
    assert rows[good]["valid"] and rows[odd]["valid"] and rows[odd]["notebook"] is None
    assert not rows["a" * 64]["valid"] and not rows["b" * 64]["valid"]
    # Was: internal "ReceiptError: ..." and internal "IsADirectoryError: ... '<full path>'".
    failed(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id="a" * 64), "failed")
    failed(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id="b" * 64), "failed")
    failed(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id="c" * 64), "not_found")
    shutil.rmtree(folder)
    folder.write_text("not a folder", encoding="utf-8")
    assert ok(call(reg, nbsvc, "fabric_desktop_list_receipts", tmp_path))["items"] == []
    failed(call(reg, nbsvc, "fabric_desktop_get_receipt", tmp_path, id=good), "failed", "not_found")


# ---- the metadata project: its files and its run log (a Delta table)

@pytest.fixture(scope="module")
def meta_template(tmp_path_factory):
    base = tmp_path_factory.mktemp("meta")
    s = Service(root=base / "app")
    s.meta_init(str(base / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return base / "crm"


@pytest.fixture
def meta(meta_template, tmp_path):
    crm = tmp_path / "crm"
    shutil.copytree(meta_template, crm)
    s = Service(root=tmp_path / "app")
    s.meta_open(str(crm))
    s.crm = crm
    s.run_log = crm / ".local" / "dev" / "LH_Meta" / "Tables" / "meta" / "run_log"
    s.run_id = ok(call(_registry(), s, "fabric_desktop_list_project_runs", tmp_path))["items"][0]["run_id"]
    return s


def _registry():
    r = Registry()
    register_core(r)
    return r


RUN_LOG_DAMAGE = {
    "last commit cut off": lambda log: _commits(log)[-1].write_bytes(_commits(log)[-1].read_bytes()[:40]),
    "a version missing": lambda log: _commits(log)[2].unlink(),
    "commit not UTF-8": lambda log: _commits(log)[-1].write_bytes(b"\xff\xfe"),
    "checkpoint missing": lambda log: (log / "_last_checkpoint").write_text('{"version":3,"size":3}', encoding="utf-8"),
    "add without a path": DELTA_DAMAGE["add without a path"],
    "a data file missing": lambda log: sorted(log.parent.glob("*.parquet"))[-1].unlink(),
    "a data file cut off": lambda log: (lambda p: p.write_bytes(p.read_bytes()[:50]))(sorted(log.parent.glob("*.parquet"))[-1]),
}


@pytest.mark.parametrize("damage", RUN_LOG_DAMAGE, ids=list(RUN_LOG_DAMAGE))
def test_a_damaged_run_log_is_a_clean_failure_and_lineage_still_reads(reg, meta, tmp_path, damage):
    # Was: internal, with deltalake's native backtrace and the run log's full path.
    RUN_LOG_DAMAGE[damage](meta.run_log / "_delta_log")
    runs = call(reg, meta, "fabric_desktop_list_project_runs", tmp_path)
    run = call(reg, meta, "fabric_desktop_get_project_run", tmp_path, run_id=meta.run_id)
    assert isinstance(runs, IntegrationError) or isinstance(run, IntegrationError) or runs["total"] >= 1
    ok(call(reg, meta, "fabric_desktop_get_lineage", tmp_path, entity="silver.orders"))


PROJECT_FILE_DAMAGE = {
    "layers.toml not TOML": ("layers.toml", b"[[[ nope"),
    "layers.toml not UTF-8": ("layers.toml", b"\xff\xfe"),
    "environments.toml not TOML": ("environments.toml", b"[[[ nope"),
    "an environment that is text": ("environments.toml", b"dev = 'x'\n"),
}


@pytest.mark.parametrize("damage", PROJECT_FILE_DAMAGE, ids=list(PROJECT_FILE_DAMAGE))
def test_a_metadata_project_whose_files_dont_parse_is_named_and_the_notebooks_still_list(reg, meta, tmp_path, damage):
    # Was: list_projects failed outright (the notebooks went with it), and "dev = 'x'" was internal.
    name, data = PROJECT_FILE_DAMAGE[damage]
    (meta.crm / name).write_bytes(data)
    projects = ok(call(reg, meta, "fabric_desktop_list_projects", tmp_path))["projects"]
    assert projects[0]["id"] == "notebooks" and projects[1]["id"] == "metadata" and projects[1]["error"]
    for cap_id, args in (("fabric_desktop_get_project", {"id": "metadata"}), ("fabric_desktop_validate_project", {}),
                         ("fabric_desktop_get_lineage", {"entity": "silver.orders"}), ("fabric_desktop_list_project_runs", {}),
                         ("fabric_desktop_get_project_run", {"run_id": meta.run_id})):
        assert failed(call(reg, meta, cap_id, tmp_path, **args), "failed").message.startswith("The metadata project's files")


@pytest.mark.parametrize("damage", ["not TOML", "not UTF-8", "a folder", "wrong types"])
def test_one_damaged_entity_file_is_a_project_error_not_a_failure(reg, meta, tmp_path, damage):
    # Was: an entity file that isn't UTF-8, or is a folder, failed every metadata tool (a folder: internal).
    entity = meta.crm / "metadata" / "entities" / "silver.orders.toml"
    if damage == "a folder":
        entity.unlink()
        entity.mkdir()
    else:
        entity.write_bytes({"not TOML": b"[[[ nope", "not UTF-8": b"\xff\xfe", "wrong types": b"columns = 5\nkeys = 'x'\n"}[damage])
    ok(call(reg, meta, "fabric_desktop_get_project", tmp_path, id="metadata"))
    ok(call(reg, meta, "fabric_desktop_list_project_runs", tmp_path))
    ok(call(reg, meta, "fabric_desktop_get_lineage", tmp_path, entity="gold.dim_customer"))
    call(reg, meta, "fabric_desktop_get_lineage", tmp_path, entity="silver.orders")
    if damage != "wrong types":
        assert not ok(call(reg, meta, "fabric_desktop_validate_project", tmp_path))["ok"]


# ---- over MCP, in memory

def test_damaged_data_over_mcp_is_a_structured_error_without_paths(app, tmp_path):
    from mcp import Client

    from fabric_desktop.integrations.mcp_server import build_server

    tool = Service(root=tmp_path / "app")
    tool.notebooks.save("good", [{"type": "code", "source": "x = 1"}])
    (tool.notebooks.folder / "bad.ipynb").write_bytes(b"[]")
    _commits(app.delta_log)[-1].write_bytes(b"\xff\xfe")
    (tool.root / "proofs.jsonl").write_text(json.dumps(RECORD) + "\n" + json.dumps(RECORD)[:20], encoding="utf-8")
    reg = _registry()

    async def go():
        async with Client(build_server(reg, lambda: tool, "cl_1"), mode="auto") as c:
            results = {}
            for name, args in (("fabric_desktop_get_notebook", {"name": "bad"}),
                               ("fabric_desktop_get_local_table_history", {"project": app.pid, "table": "main.big_orders"}),
                               ("fabric_desktop_list_proofs", {}),
                               ("fabric_desktop_list_notebooks", {}),
                               ("fabric_desktop_get_local_tables", {"project": app.pid})):
                results[name] = await c.call_tool(name, args)
            return results

    results = anyio.run(go)
    for name in ("fabric_desktop_get_notebook", "fabric_desktop_get_local_table_history"):
        out = results[name]
        assert out.is_error, name
        err = out.structured_content["error"]
        assert err["code"] in ("invalid_input", "failed"), err
        text = out.content[0].text
        assert "Traceback" not in text and "<unknown>" not in text, text
        assert not any(p in text for p in _places(tmp_path)), text
    for name in ("fabric_desktop_list_proofs", "fabric_desktop_list_notebooks", "fabric_desktop_get_local_tables"):
        assert not results[name].is_error, (name, results[name].content[0].text)
    assert [r["id"] for r in results["fabric_desktop_list_proofs"].structured_content["items"]] == ["p1"]
    assert opened(tool) == []
    tool.local_data.close_all()
