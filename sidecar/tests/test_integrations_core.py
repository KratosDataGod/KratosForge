"""The contract for external callers (integrations/core.py and services.py): errors, schemas,
timeouts, cancellation, and adapters that call the existing services without the UI."""

import json
import threading
import time

import pytest

from fabric_desktop.integrations import services
from fabric_desktop.integrations.core import (CODES, CallContext, Caller, IntegrationError, error_from,
                                              redact_secrets, run_call, validate)
from fabric_desktop.service import Service

CALLER = Caller("cl_test", "test")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name"],
          "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 10},
                         "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                         "mode": {"enum": ["a", "b"]},
                         "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2}}}


def ctx(timeout=5.0):
    return CallContext(CALLER, timeout=timeout)


# ---- validation

def test_validate_fills_defaults_and_keeps_given_values():
    assert validate(SCHEMA, {"name": "x"}) == {"name": "x", "limit": 20}
    assert validate(SCHEMA, {"name": "x", "limit": 3}) == {"name": "x", "limit": 3}


@pytest.mark.parametrize("args, problem", [
    ({}, "name is required"),
    ({"name": ""}, "name is too short"),
    ({"name": "x" * 11}, "longer than 10"),
    ({"name": "x", "limit": 0}, "at least 1"),
    ({"name": "x", "limit": True}, "limit should be integer"),
    ({"name": "x", "mode": "c"}, "must be one of a, b"),
    ({"name": "x", "tags": ["a", 1]}, "tags[1] should be string"),
    ({"name": "x", "tags": ["a", "b", "c"]}, "more than 2"),
    ({"name": "x", "path": "/etc"}, "path isn't an argument this takes"),
])
def test_validate_rejects_with_a_plain_reason(args, problem):
    with pytest.raises(IntegrationError) as err:
        validate(SCHEMA, args)
    assert err.value.code == "invalid_input"
    assert problem in err.value.message
    assert err.value.details["problems"]


def test_validate_rejects_non_object_arguments():
    with pytest.raises(IntegrationError):
        validate(SCHEMA, ["name"])


# ---- errors

class FakeResponse:
    def __init__(self, status):
        self.status_code = status


class FakeHTTPError(Exception):
    def __init__(self, status, text="boom"):
        super().__init__(text)
        self.response = FakeResponse(status)


@pytest.mark.parametrize("exc, code, retryable", [
    (PermissionError("no"), "permission_denied", False),
    (FileNotFoundError("missing.ipynb"), "not_found", False),
    (KeyError("nb"), "not_found", False),
    (LookupError("No proof x"), "not_found", False),
    (ValueError("bad"), "invalid_input", False),
    (TimeoutError("slow"), "timeout", True),
    (RuntimeError("A proof is running this notebook's kernel. Wait for it, or cancel it."), "conflict", True),
    (RuntimeError("the job failed"), "failed", False),
    (FakeHTTPError(403), "permission_denied", False),
    (FakeHTTPError(404), "not_found", False),
    (FakeHTTPError(429), "upstream", True),
    (FakeHTTPError(503), "upstream", True),
    (ZeroDivisionError("x"), "internal", False),
])
def test_engine_exceptions_map_to_stable_codes(exc, code, retryable):
    err = error_from(exc)
    assert (err.code, err.retryable) == (code, retryable)
    assert err.code in CODES
    json.dumps(err.to_dict())


def test_gate_errors_keep_every_reason():
    class Gate(Exception):
        errors = ["row count fell 90%", "key not unique"]
    err = error_from(Gate("2 checks failed"))
    assert err.code == "failed" and err.details["reasons"] == Gate.errors


def test_messages_never_carry_tokens():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
    err = error_from(RuntimeError(f"401 for https://x/y?sig=abcDEF123&sv=1 with Bearer {jwt}"))
    assert jwt not in err.message and "abcDEF123" not in err.message
    assert "[removed]" in redact_secrets("password=hunter2")


def test_unknown_error_codes_are_refused():
    with pytest.raises(ValueError):
        IntegrationError("oops", "x")


# ---- calls

def test_run_call_returns_the_result():
    assert run_call(lambda: 42, ctx()) == 42


def test_run_call_maps_exceptions():
    def boom():
        raise LookupError("No notebook named Sales")
    with pytest.raises(IntegrationError) as err:
        run_call(boom, ctx())
    assert err.value.code == "not_found"


def test_run_call_times_out_and_tells_the_handler_to_stop():
    c = ctx(timeout=0.2)
    stopped = threading.Event()

    def slow():
        while not c.cancelled:
            time.sleep(0.01)
        stopped.set()
    started = time.monotonic()
    with pytest.raises(IntegrationError) as err:
        run_call(slow, c)
    assert err.value.code == "timeout" and err.value.retryable
    assert time.monotonic() - started < 2
    assert stopped.wait(1)


def test_run_call_cancels():
    c = ctx()
    threading.Timer(0.1, c.cancel).start()
    with pytest.raises(IntegrationError) as err:
        run_call(lambda: time.sleep(3), c)
    assert err.value.code == "cancelled"


def test_context_check_raises_when_cancelled_or_late():
    c = ctx(timeout=0.0)
    with pytest.raises(IntegrationError) as err:
        time.sleep(0.01)
        c.check()
    assert err.value.code == "timeout"
    c = ctx()
    c.cancel()
    with pytest.raises(IntegrationError) as err:
        c.check()
    assert err.value.code == "cancelled"


def test_progress_problems_never_fail_the_call():
    c = CallContext(CALLER, progress=lambda *a: 1 / 0)
    c.report(1, 2, "half")


# ---- adapters over the engine's services, with no UI

@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path)
    monkeypatch.setattr(s, "list_workspaces", lambda: [
        {"id": f"ws{i}", "displayName": f"Workspace {i}", "type": "Workspace"} for i in range(5)])
    monkeypatch.setattr(s, "list_items", lambda ws: [
        {"id": "lh1", "displayName": "LH_Bronze", "type": "Lakehouse", "description": ""},
        {"id": "nb1", "displayName": "nb_load", "type": "Notebook", "description": "loads"}])
    monkeypatch.setattr(s, "list_tables", lambda ws, lh: {"dbo": ["sales", "customers"], "stage": ["raw"]})
    return s


def test_paging_says_where_to_continue(svc):
    first = services.list_workspaces(svc, ctx(), limit=2)
    assert [w["id"] for w in first["items"]] == ["ws0", "ws1"]
    assert first["total"] == 5 and first["next_offset"] == 2
    last = services.list_workspaces(svc, ctx(), offset=4, limit=2)
    assert last["next_offset"] is None
    assert set(first["items"][0]) == {"id", "name", "type", "capacity_id"}


def test_artifacts_filter_by_type(svc):
    rows = services.list_artifacts(svc, ctx(), workspace_id="ws0", type="notebook")["items"]
    assert rows == [{"id": "nb1", "name": "nb_load", "type": "Notebook", "description": "loads"}]


def test_missing_artifact_is_not_found(svc):
    with pytest.raises(IntegrationError) as err:
        services.get_artifact_metadata(svc, ctx(), workspace_id="ws0", item_id="nope")
    assert err.value.code == "not_found"


def test_tables_are_listed_with_full_names(svc):
    out = services.list_tables(svc, ctx(), workspace_id="ws0", lakehouse_id="lh1")
    assert {"schema": "dbo", "name": "sales", "full_name": "dbo.sales"} in out["tables"]


def test_notebooks_return_code_without_outputs_or_secrets(svc):
    svc.notebooks.save("Load sales", [
        {"type": "code", "source": "key = 'password=hunter2'\nprint(key)"},
        {"type": "markdown", "source": "# Notes"}], {"name": "LH", "workspace_id": "ws0", "lakehouse_id": "lh1"})
    nb = services.get_notebook(svc, ctx(), name="Load sales")
    assert nb["cells"][0]["type"] == "code" and "hunter2" not in nb["cells"][0]["source"]
    assert "outputs" not in nb["cells"][0]
    assert nb["lakehouse"]["lakehouse_id"] == "lh1"
    assert "source" not in services.get_notebook(svc, ctx(), name="Load sales", include_source=False)["cells"][0]
    assert services.list_notebooks(svc, ctx())["items"][0]["name"] == "Load sales"


def test_validate_notebook_runs_the_screen_checks(svc):
    svc.notebooks.save("Uses spark", [{"type": "code", "source": "df = spark.read.table('sales')\ndisplay(df)"}])
    out = services.validate_notebook(svc, ctx(), name="Uses spark")
    assert set(out) >= {"this_pc", "fabric", "proof", "dependencies", "ok"}
    assert out["proof"]["ok"] is False  # no lakehouse attached
    # Validate's code checks too: reading a table with no lakehouse attached is an error.
    assert out["verdict"] == "not_ready" and [f["code"] for f in out["findings"] if f["severity"] == "error"] == ["no_lakehouse"]


def test_projects_list_the_notebook_folder_without_a_metadata_project(svc):
    out = services.list_projects(svc, ctx())
    assert [p["id"] for p in out["projects"]] == ["notebooks"]
    with pytest.raises(IntegrationError) as err:
        services.get_project(svc, ctx(), id="metadata")
    assert err.value.code == "not_found"


def test_status_never_starts_a_sign_in(svc):
    out = services.get_status(svc, ctx())
    assert out["product"] == "KratosForge" and out["caller"]["channel"] == "test"
    assert "token" not in json.dumps(out).lower()



def test_status_builds_no_sign_in_client_when_nobody_signed_in_here(svc, tmp_path, monkeypatch):
    """Building the sign-in client asks Microsoft for its endpoints (and on Windows starts the
    broker): with no sign-in cache on this PC there is nobody to report, so status doesn't."""
    import fabric_desktop.service as service_mod
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    built = []
    monkeypatch.setattr(service_mod, "Authenticator", lambda *a, **k: built.append(1))
    out = services.get_status(svc, ctx())
    assert (out["signed_in"], out["user"], built) == (False, None, [])


def test_calls_arriving_together_build_one_sign_in_client(tmp_path, monkeypatch):
    import fabric_desktop.service as service_mod
    built = []

    class Slow:
        client_id = ""

        def __init__(self, client_id, interactive=True):
            self.client_id = client_id
            built.append(self)
            time.sleep(0.2)  # the endpoint lookup
    monkeypatch.setattr(service_mod, "Authenticator", Slow)
    s = Service(root=tmp_path)
    got = []
    threads = [threading.Thread(target=lambda: got.append(s.auth)) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1 and all(a is built[0] for a in got) and len(got) == 12

def test_metadata_project_is_described_and_validated(svc, tmp_path):
    svc.meta_init(str(tmp_path / "project"))
    ids = [p["id"] for p in services.list_projects(svc, ctx())["projects"]]
    assert ids == ["notebooks", "metadata"]
    project = services.get_project(svc, ctx(), id="metadata")
    assert project["entities"] and set(project["entities"][0]) <= {
        "name", "layer", "schema", "table", "owner", "mode", "source", "keys", "columns", "reads", "description",
        "checks", "clean", "derive", "freshness", "criticality"}
    result = services.validate_project(svc, ctx())
    assert result["ok"] is True and result["errors"] == []


def test_every_check_follows_a_run_notebook_on_this_pc(svc):
    """#1273: Runs on (open and save), Validate, Prove's check, the advisor and the AI tool all read
    the helper a notebook runs, so they say the same thing about it."""
    svc.notebooks.save("Helpers", [{"type": "code", "source": "import notebookutils as nu\nnu.fs.mount('abfss://c@a', '/m')"}])
    main = [{"type": "code", "source": "%run Helpers"}]
    svc.notebooks.save("Main", main)
    text = "runs Helpers (%run), whose cell 1 uses Fabric notebook utilities that need Fabric"
    opened = svc.open_notebook("Main")["check"]["local"]
    saved = svc.save_notebook("Main", main)["check"]["local"]
    tool = services.validate_notebook(svc, ctx(), name="Main")
    assert opened["reasons"][0]["text"] == saved["reasons"][0]["text"] == tool["this_pc"]["reasons"][0]["text"] == text
    assert tool["ok"] is False and tool["this_pc"]["status"] == "blocked"
    assert any(f["code"] == "runs_on_pc" and text in f["text"] for f in tool["findings"])
    assert [b for b in tool["proof"]["blockers"] if text in b["text"]]


def test_placement_advice_follows_a_run_notebook_too(svc):
    svc.notebooks.save("Helpers", [{"type": "code", "source": "import notebookutils as nu\nnu.fs.mount('abfss://c@a', '/m')"}])
    svc.notebooks.save("Main", [{"type": "code", "source": "%run Helpers"}])
    texts = [r["text"] for r in svc.placement_advice("Main")["reasons"] if r["kind"] == "compatibility"]
    assert any("runs Helpers (%run), whose cell 1 uses Fabric notebook utilities" in t for t in texts), texts


def test_every_check_reads_a_run_b_file_and_a_notebook_runs_own_lakehouse(svc):
    """#1288: the service gives the checks this notebook's resources folder and the other notebooks' lakehouses."""
    from pathlib import Path
    svc.notebooks.save("Main", [{"type": "code", "source": "%run -b setup.py"},
                                {"type": "code", "source": "import notebookutils as nu\nnu.notebook.run('Child')"}],
                       {"workspace_id": "ws", "lakehouse_id": "lh", "name": "Sales"})
    svc.notebooks.save("Child", [{"type": "code", "source": "df = spark.read.table('dbo.t')"}])
    builtin = Path(svc.notebooks.resources("Main")) / "builtin"
    builtin.mkdir(parents=True, exist_ok=True)
    (builtin / "setup.py").write_text("import notebookutils as nu\nnu.fs.mount('abfss://c@a', '/m')", encoding="utf-8")
    check = svc.open_notebook("Main")["check"]
    assert check["local"]["reasons"][0]["text"].startswith("runs setup.py (%run -b), which uses Fabric notebook utilities")
    assert check["fabric"]["reasons"][0]["text"].startswith("runs Child (notebook.run) in a session of its own")
    tool = services.validate_notebook(svc, ctx(), name="Main")
    assert tool["this_pc"]["reasons"] == check["local"]["reasons"] and tool["fabric"]["reasons"] == check["fabric"]["reasons"]
