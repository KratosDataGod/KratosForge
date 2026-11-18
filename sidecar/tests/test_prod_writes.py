"""A notebook cell's MERGE, UPDATE, DELETE, VACUUM or RESTORE on a workspace marked Prod waits for the person to
type the workspace's name (prod_writes_service.py), with or without a metadata project open, and the typed name
lets that notebook's next run through once."""

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, prod_writes_service
from fabric_desktop.audit import READ_ONLY, fails_closed
from fabric_desktop.meta_service import NoMetadataProject
from fabric_desktop.notebook import Kernel
from fabric_desktop.prod_writes_service import ProdWritesMixin
from tests.test_spark_writer import lake  # noqa: F401 - a fixture


class _Project:
    def __init__(self, prod):
        self.environments = {"prod": prod, "dev": {"workspace_id": "dev-ws"}}


class Svc(ProdWritesMixin):
    def __init__(self, root, project=None):
        self.root = root
        self.project = project

    def _project(self):
        if self.project is None:
            raise NoMetadataProject("No metadata project is open.")
        return self.project


def test_a_workspace_a_project_named_prod_stays_marked_with_no_project_open(tmp_path):
    svc = Svc(tmp_path, _Project({"workspace_id": "WS-Prod", "workspaces": {"gold": {"id": "ws-gold"}}}))
    assert svc._prod_write_refusal("merge", "ws-prod", "Sales Prod", "dbo.sales", "nb") is not None
    assert svc._prod_write_refusal("delete", "dev-ws", "Sales Dev", "dbo.sales", "nb") is None
    svc.project = None  # closed: what it named is remembered on this PC
    assert sorted(json.loads((tmp_path / "prod_workspaces.json").read_text())["prod"]) == ["ws-gold", "ws-prod"]
    why = svc._prod_write_refusal("vacuum", "WS-GOLD", "Gold Prod", "dbo.sales", "nb")
    assert why and "Gold Prod, a Prod workspace" in why and "Nothing was changed" in why
    assert Svc(tmp_path)._prod_write_refusal("merge", "other", "Other", "dbo.sales", "nb") is None


def test_the_typed_name_must_match_and_allows_one_run_of_that_notebook(tmp_path):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))
    svc = Svc(tmp_path)
    assert svc.notebook_prod_pending("nb") is None
    svc._prod_write_refusal("delete", "ws-prod", "Sales Prod", "dbo.sales", "nb")
    assert svc.notebook_prod_pending("nb") == {"workspace_id": "ws-prod", "workspace_name": "Sales Prod",
                                               "operation": "DELETE", "table": "dbo.sales"}
    assert svc.notebook_prod_pending("other") is None
    with pytest.raises(ValueError, match="Type Sales Prod exactly"):
        svc.notebook_prod_confirm("nb", "ws-prod", "sales prod")
    with pytest.raises(ValueError, match="Nothing in this notebook"):
        svc.notebook_prod_confirm("nb", "another-ws", "Sales Prod")
    with pytest.raises(ValueError, match="Nothing in this notebook"):
        svc.notebook_prod_confirm("other", "ws-prod", "Sales Prod")
    assert svc._take_prod_allowance("nb") == frozenset()  # nothing confirmed yet
    assert svc.notebook_prod_confirm("nb", "ws-prod", "Sales Prod")["allowed"] is True
    assert svc.notebook_prod_pending("nb") is None
    assert svc._take_prod_allowance("other") == frozenset()
    assert svc._take_prod_allowance("nb") == frozenset({"ws-prod"})
    assert svc._take_prod_allowance("nb") == frozenset()  # used up by that run


def test_an_allowance_or_a_refusal_that_waited_too_long_lapses(tmp_path, monkeypatch):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))
    svc = Svc(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(prod_writes_service.time, "monotonic", lambda: now[0])
    svc._prod_write_refusal("merge", "ws-prod", "", "dbo.sales", "nb")  # no name known: the id is typed
    svc.notebook_prod_confirm("nb", "ws-prod", "ws-prod")
    now[0] += prod_writes_service.ALLOW_SECONDS + 1
    assert svc._take_prod_allowance("nb") == frozenset()
    svc._prod_write_refusal("merge", "ws-prod", "", "dbo.sales", "nb")
    now[0] += prod_writes_service.PENDING_SECONDS + 1
    with pytest.raises(ValueError, match="Nothing in this notebook"):
        svc.notebook_prod_confirm("nb", "ws-prod", "ws-prod")


def test_the_confirm_is_audited_before_it_runs_and_the_pending_read_is_not():
    assert fails_closed("notebook_prod_confirm", {"notebook": "nb", "workspace_id": "w", "confirm_name": "Sales Prod"})
    assert "notebook_prod_pending" in READ_ONLY and "notebook_prod_confirm" not in READ_ONLY


@pytest.fixture
def prod(lake, monkeypatch, tmp_path):  # noqa: F811
    """The attached lakehouse's workspace (ws1) marked Prod, through a Service-like guard."""
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws1"]}))
    svc = Svc(tmp_path)
    monkeypatch.setattr(lakehouse, "prod_guard", svc._prod_write_refusal)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    write_deltalake(str(lake / "dbo" / "sales"), pa.table({"id": [4], "region": ["APAC"]}), mode="append")
    return svc


def _run(code, kernel=None):
    out = (kernel or Kernel()).run(code)
    enames = [o.get("ename") for o in out["outputs"] if o.get("type") == "error"]
    return out["status"], enames, "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def _version(lake):
    return DeltaTable(str(lake / "dbo" / "sales")).version()


@pytest.mark.parametrize("sql", [
    "DELETE FROM dbo.sales WHERE id = 1",
    "UPDATE dbo.sales SET region = 'X' WHERE id = 1",
    "MERGE INTO dbo.sales t USING (SELECT 1 AS id, 'Z' AS region) s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *",
    "VACUUM dbo.sales",
    "RESTORE TABLE dbo.sales TO VERSION AS OF 0",
])
def test_a_cell_changing_a_prod_marked_workspace_is_refused_until_the_name_is_typed(lake, prod, sql):  # noqa: F811
    before = _version(lake)
    status, enames, text = _run(f"spark.sql({sql!r})")
    assert status == "error" and enames == ["ProdConfirmNeeded"], text
    assert "a Prod workspace" in text and _version(lake) == before
    pending = prod.notebook_prod_pending("")
    prod.notebook_prod_confirm("", pending["workspace_id"], pending["workspace_name"])
    kernel = Kernel()
    kernel.prod_allowed = prod._take_prod_allowance("")
    status, _, text = _run(f"spark.sql({sql!r})", kernel)
    assert status == "ok", text
    kernel.prod_allowed = prod._take_prod_allowance("")  # the next run has none again
    status, enames, _ = _run(f"spark.sql({sql!r})", kernel)
    assert enames == ["ProdConfirmNeeded"]


def test_optimize_and_reads_are_not_asked_about(lake, prod):  # noqa: F811
    status, _, text = _run("spark.sql('OPTIMIZE dbo.sales')\nprint(spark.table('dbo.sales').count())")
    assert status == "ok" and "4" in text, text
    assert prod.notebook_prod_pending("") is None


def test_outside_the_app_no_workspace_is_marked(lake):  # noqa: F811
    status, _, text = _run("spark.sql('DELETE FROM dbo.sales WHERE id = 1')")
    assert status == "ok", text


def test_a_process_kernel_asks_the_engine_with_its_notebook_and_its_runs_allowance(monkeypatch):
    from fabric_desktop.kernel_proc import ProcessKernel
    seen = []
    monkeypatch.setattr(lakehouse, "prod_guard", lambda *a: seen.append(a) or "waits")
    k = ProcessKernel()
    k.notebook_name = "Weekly"
    assert k.calls["prod_guard"]("delete", "WS1", "Sales Prod", "dbo.sales") == "waits"
    assert seen == [("delete", "WS1", "Sales Prod", "dbo.sales", "Weekly")]
    k.prod_allowed = frozenset({"ws1"})
    assert k.calls["prod_guard"]("delete", "WS1", "Sales Prod", "dbo.sales") is None
    assert len(seen) == 1
    # A thread in the kernel's process that isn't the running cell's own gets no allowance and no notebook.
    assert k.calls["prod_guard"]("delete", "WS1", "Sales Prod", "dbo.sales", True) == "waits"
    assert seen[-1] == ("delete", "WS1", "Sales Prod", "dbo.sales", lakehouse.NO_CELL)


def test_an_unreadable_project_is_warned_about_and_the_remembered_ids_still_hold(tmp_path, caplog):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))

    class Broken(Svc):
        def _project(self):
            raise ValueError("layers.toml line 3: bad value")

    svc = Broken(tmp_path)
    with caplog.at_level("WARNING", logger="fabric_desktop.prod_writes_service"):
        assert svc._prod_write_refusal("delete", "other-ws", "Other", "dbo.sales", "nb") is None
    assert any(r.levelname == "WARNING" and "only the Prod workspaces remembered" in r.getMessage() for r in caplog.records)
    assert svc._prod_write_refusal("delete", "ws-prod", "Sales Prod", "dbo.sales", "nb") is not None


@pytest.mark.parametrize("code", [
    "df = spark.createDataFrame([(9, 'EU')], ['id', 'region'])\ndf.write.mode('overwrite').saveAsTable('dbo.sales')",
    "spark.sql(\"INSERT OVERWRITE dbo.sales SELECT 9 AS id, 'EU' AS region\")",
    "import pyarrow as pa\nfrom fabric_desktop import lakehouse\nlakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales')",
])
def test_an_overwrite_from_a_cell_asks_too(lake, prod, code):  # noqa: F811
    before = _version(lake)
    status, enames, text = _run(code)
    assert status == "error" and enames == ["ProdConfirmNeeded"], text
    assert "OVERWRITE on dbo.sales" in text and _version(lake) == before
    pending = prod.notebook_prod_pending("")
    prod.notebook_prod_confirm("", pending["workspace_id"], pending["workspace_name"])
    kernel = Kernel()
    kernel.prod_allowed = prod._take_prod_allowance("")
    status, _, text = _run(code, kernel)
    assert status == "ok", text
    assert _version(lake) > before


def test_an_append_from_a_cell_and_the_apps_own_overwrite_are_not_asked_about(lake, prod):  # noqa: F811
    status, _, text = _run("df = spark.createDataFrame([(9, 'EU')], ['id', 'region'])\n"
                           "df.write.mode('append').saveAsTable('dbo.sales')\nspark.sql(\"INSERT INTO dbo.sales SELECT 10, 'US'\")")
    assert status == "ok", text
    assert prod.notebook_prod_pending("") is None
    lakehouse.write_table(pa.table({"id": [1], "region": ["EU"]}), "dbo.sales")  # not a cell: the SQL page confirms its own way
    assert prod.notebook_prod_pending("") is None


def test_in_a_notebooks_own_process_every_overwrite_is_a_cells(lake, prod, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "in_kernel_process", True)
    with pytest.raises(lakehouse.ProdConfirmNeeded):
        lakehouse.write_table(pa.table({"id": [1], "region": ["EU"]}), "dbo.sales")


def test_an_overwrite_from_a_thread_the_running_cell_started_asks_too(lake, prod):  # noqa: F811
    code = ("import threading, pyarrow as pa\nfrom fabric_desktop import lakehouse\nerrors = []\n"
            "def write():\n"
            "    try:\n"
            "        lakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales')\n"
            "    except Exception as exc:\n"
            "        errors.append(type(exc).__name__)\n"
            "t = threading.Thread(target=write)\nt.start()\nt.join()\nprint(errors)")
    before = _version(lake)
    status, _, text = _run(code)
    assert status == "ok" and "ProdConfirmNeeded" in text, text
    assert _version(lake) == before


def test_another_thread_never_borrows_the_running_cells_typed_name(lake, prod):  # noqa: F811
    """A MERGE from a thread that isn't the cell's, on the workspace the cell's typed name allows, is still asked."""
    import threading
    started, release, raised = threading.Event(), threading.Event(), []
    from fabric_desktop import delta_dml

    def other_writer():
        started.wait(10)
        change = delta_dml.parse("MERGE INTO dbo.sales t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")
        try:
            lakehouse.change_table(change, pa.table({"id": [1], "region": ["Z"]}))
        except lakehouse.ProdConfirmNeeded:
            raised.append(True)
        finally:
            release.set()

    prod._prod_write_refusal("merge", "ws1", "", "dbo.sales", "")
    prod.notebook_prod_confirm("", "ws1", "ws1")
    kernel = Kernel()
    kernel.prod_allowed = prod._take_prod_allowance("")
    assert kernel.prod_allowed == frozenset({"ws1"})
    writer = threading.Thread(target=other_writer)
    writer.start()
    import sys
    import types
    sys.modules["_prod_test_hooks"] = types.SimpleNamespace(started=started, release=release)
    try:
        status, _, text = _run("import _prod_test_hooks as t\nt.started.set()\nt.release.wait(10)", kernel)
    finally:
        del sys.modules["_prod_test_hooks"]
    writer.join(10)
    assert status == "ok", text
    assert raised == [True]



def test_a_write_without_its_own_cell_is_never_charged_to_a_running_notebook(lake, prod):  # noqa: F811
    """Two notebooks: A holds a typed name and is running. An overwrite from a thread that is no cell's (notebook B's
    or the app's) is asked under no notebook's name, so it can't be confirmed into A's or B's next run."""
    import sys
    import threading
    import types
    started, release, raised = threading.Event(), threading.Event(), []

    def writer():
        started.wait(10)
        try:
            lakehouse.write_table(pa.table({"id": [1], "region": ["EU"]}), "dbo.sales")
        except lakehouse.ProdConfirmNeeded:
            raised.append(True)
        finally:
            release.set()

    prod._prod_write_refusal("overwrite", "ws1", "", "dbo.sales", "A")
    prod.notebook_prod_confirm("A", "ws1", "ws1")
    a = Kernel()
    a.notebook_name = "A"
    a.prod_allowed = prod._take_prod_allowance("A")
    t = threading.Thread(target=writer)
    t.start()
    sys.modules["_prod_test_hooks"] = types.SimpleNamespace(started=started, release=release)
    try:
        status, _, text = _run("import _prod_test_hooks as t\nt.started.set()\nt.release.wait(10)", a)
    finally:
        del sys.modules["_prod_test_hooks"]
    t.join(10)
    assert status == "ok", text
    assert raised == [True]
    assert prod.notebook_prod_pending("A") is None and prod.notebook_prod_pending("B") is None
    assert prod.notebook_prod_pending(lakehouse.NO_CELL) is None  # nothing waits: it can't be confirmed
    why = prod._prod_write_refusal("overwrite", "ws1", "Sales Prod", "dbo.sales", lakehouse.NO_CELL)
    assert "Only a cell's own code" in why and "If it came from outside the notebook, try again when no cell is running" in why
    assert "Run this cell on its own" not in why


def test_a_refusal_under_no_cell_can_never_be_confirmed(tmp_path):
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws-prod"]}))
    svc = Svc(tmp_path)
    svc._prod_write_refusal("overwrite", "ws-prod", "Sales Prod", "dbo.sales", lakehouse.NO_CELL)
    with pytest.raises(ValueError, match="wasn't made by a notebook cell"):
        svc.notebook_prod_confirm(lakehouse.NO_CELL, "ws-prod", "Sales Prod")
    assert svc._take_prod_allowance(lakehouse.NO_CELL) == frozenset()


def test_a_thread_that_outlives_its_cell_is_still_asked_and_never_allowed(lake, prod):  # noqa: F811
    """A cell with a typed name starts a thread and finishes; the thread's overwrite after that is asked under no
    notebook, so neither the cell's allowance nor a later typed name lets it through."""
    import sys
    import threading
    import types
    go, done, raised = threading.Event(), threading.Event(), []
    prod._prod_write_refusal("overwrite", "ws1", "", "dbo.sales", "A")
    prod.notebook_prod_confirm("A", "ws1", "ws1")
    kernel = Kernel()
    kernel.notebook_name = "A"
    kernel.prod_allowed = prod._take_prod_allowance("A")
    sys.modules["_prod_test_hooks"] = types.SimpleNamespace(go=go, done=done, raised=raised)
    code = ("import threading, pyarrow as pa, _prod_test_hooks as t\nfrom fabric_desktop import lakehouse\n"
            "def later():\n"
            "    t.go.wait(10)\n"
            "    try:\n"
            "        lakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales')\n"
            "    except lakehouse.ProdConfirmNeeded:\n"
            "        t.raised.append(True)\n"
            "    finally:\n"
            "        t.done.set()\n"
            "threading.Thread(target=later, daemon=True).start()")
    try:
        before = _version(lake)
        status, _, text = _run(code, kernel)
        assert status == "ok", text
        go.set()  # the cell has finished: no cell is running now
        assert done.wait(10)
    finally:
        del sys.modules["_prod_test_hooks"]
    assert raised == [True] and _version(lake) == before
    assert prod.notebook_prod_pending("A") is None and prod.notebook_prod_pending("") is None


def test_a_thread_running_before_the_cell_is_not_counted_as_the_cells(lake, prod):  # noqa: F811
    import threading
    release = threading.Event()
    other = threading.Thread(target=release.wait, args=(10,), daemon=True)
    other.start()
    try:
        status, _, text = _run("print('ok')")
        assert status == "ok", text
        assert other not in lakehouse.cell_started_threads
    finally:
        release.set()
        other.join(10)


def test_a_widget_handlers_write_is_asked_under_no_cell(lake, prod):  # noqa: F811
    """A control's handler is a cell's code but no running cell's own: its overwrite is asked and can't be allowed."""
    k = Kernel()
    status, _, text = _run(
        "import ipywidgets as w, pyarrow as pa\nfrom fabric_desktop import lakehouse\nb = w.Button(description='Go')\n"
        "b.on_click(lambda b: lakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales'))", k)
    assert status == "ok", text
    before = _version(lake)
    out = k.ipywidget_event(k.namespace["b"].model_id, {"click": True})
    assert out["status"] == "error" and any(o.get("ename") == "ProdConfirmNeeded" for o in out["outputs"]), out
    assert _version(lake) == before and prod.notebook_prod_pending("") is None


_LATER_WRITE = ("import threading, pyarrow as pa, _prod_test_hooks as t\nfrom fabric_desktop import lakehouse\n"
                "def write():\n"
                "    try:\n"
                "        lakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales')\n"
                "    except lakehouse.ProdConfirmNeeded:\n"
                "        t.raised.append(True)\n"
                "    finally:\n"
                "        t.done.set()\n")


def _later(lake, prod, code, after=None):  # noqa: F811
    """Run ``code`` in a cell holding a typed name for ws1, then let its leftover work write once the cell is done."""
    import sys
    import threading
    import types
    go, done, raised = threading.Event(), threading.Event(), []
    prod._prod_write_refusal("overwrite", "ws1", "", "dbo.sales", "A")
    prod.notebook_prod_confirm("A", "ws1", "ws1")
    kernel = Kernel()
    kernel.notebook_name = "A"
    kernel.prod_allowed = prod._take_prod_allowance("A")
    sys.modules["_prod_test_hooks"] = types.SimpleNamespace(go=go, done=done, raised=raised)
    try:
        before = _version(lake)
        status, _, text = _run(_LATER_WRITE + code, kernel)
        assert status == "ok", text
        if after:
            after(kernel)
        go.set()
        assert done.wait(10)
    finally:
        del sys.modules["_prod_test_hooks"]
    assert raised == [True] and _version(lake) == before
    assert prod.notebook_prod_pending("A") is None and prod.notebook_prod_pending("") is None


def test_a_thread_started_by_a_leftover_thread_after_the_cell_ends_is_asked_too(lake, prod):  # noqa: F811
    _later(lake, prod, "def parent():\n    t.go.wait(10)\n    threading.Thread(target=write).start()\n"
                       "threading.Thread(target=parent, daemon=True).start()")


def test_a_thread_pool_the_cell_made_and_used_later_is_asked_too(lake, prod):  # noqa: F811
    _later(lake, prod, "from concurrent.futures import ThreadPoolExecutor\npool = ThreadPoolExecutor(1)\n"
                       "pool.submit(lambda: None).result()",
           after=lambda k: k.namespace["pool"].submit(k.namespace["write"]))


def test_an_app_thread_started_while_a_cell_runs_is_not_the_cells(lake, prod):  # noqa: F811
    import sys
    import threading
    import types
    started, release, made = threading.Event(), threading.Event(), []

    def app():
        started.wait(10)
        t = threading.Thread(target=release.wait, args=(10,), daemon=True)
        t.start()
        made.append(t)
        release.set()

    a = threading.Thread(target=app)
    a.start()
    sys.modules["_prod_test_hooks"] = types.SimpleNamespace(started=started, release=release)
    try:
        status, _, text = _run("import _prod_test_hooks as t\nt.started.set()\nt.release.wait(10)")
    finally:
        del sys.modules["_prod_test_hooks"]
    a.join(10)
    assert status == "ok", text
    assert made and made[0] not in lakehouse.cell_started_threads
