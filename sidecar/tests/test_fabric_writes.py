"""Every Fabric write the app sends (QA-018): when Fabric refuses it (4xx) or fails it (5xx), the call
raises FabricError, so nothing above it can report the write as done, and a write that may have
reached Fabric is never sent a second time. Request shapes and the success paths are in
test_fabric_api*.py; this file walks every write method with the same two failures."""

import pytest

from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.retrying import SAFE_METHODS


class Resp:
    def __init__(self, status):
        self.status_code = status
        self.headers = {"Retry-After": "0", "x-ms-request-id": "r1"}
        self._body = {"errorCode": "Refused" if status < 500 else "InternalError", "message": f"status {status}"}
        self.content = b"x"
        self.text = str(self._body)

    def json(self):
        return self._body


class Always:
    """A session that answers every request with one status."""

    def __init__(self, status):
        self.status, self.calls = status, []

    def request(self, method, url, **kwargs):
        self.calls.append((method.upper(), url))
        return Resp(self.status)


NB = b'{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}'
DEF = {"parts": [{"path": "x.json", "payload": "e30=", "payloadType": "InlineBase64"}]}

# Every FabricClient method that creates, changes, runs, cancels or deletes something, with arguments.
WRITES = {
    "add_role_assignment": ("ws", "p1", "User", "Viewer"),
    "assign_to_capacity": ("ws", "cap"),
    "cancel_job": ("ws", "it", "job"),
    "create_admin_domain": ("Sales",),
    "create_item": ("ws", "Notebook", "nb"),
    "create_kql_queryset": ("ws", "q", DEF),
    "create_lakehouse": ("ws", "Sales"),
    "create_schedule": ("ws", "it", "Pipeline", {"type": "Cron", "interval": 60}),
    "create_shortcut": ("ws", "it", "Tables", "s", {"oneLake": {"workspaceId": "w2", "itemId": "i2", "path": "Tables/t"}}),
    "create_workspace": ("Sales",),
    "delete_item": ("ws", "it"),
    "delete_lakehouse": ("ws", "lh"),
    "delete_role_assignment": ("ws", "a1"),
    "delete_schedule": ("ws", "it", "Pipeline", "s1"),
    "delete_shortcut": ("ws", "it", "Tables", "s"),
    "git_update_from_git": ("ws", "c1", "h1", "PreferRemote", True),
    "put_data_access_roles": ("ws", "it", [{"name": "r"}]),
    "refresh_sql_endpoint": ("ws", "sql"),
    "run_item_execute": ("ws", "it", "CopyJob"),
    "run_item_job": ("ws", "it", "Pipeline"),
    "run_table_maintenance": ("ws", "lh", "t"),
    "save_item": ("ws", "Report", "r", {"definition.pbir": "{}"}),
    "save_notebook": ("ws", "nb", NB),
    "save_semantic_model": ("ws", "m", {"definition.pbism": "{}"}),
    "save_variable_library": ("ws", "v", {"variables.json": "{}"}),
    "set_active_value_set": ("ws", "lib", "Prod"),
    "update_item_definition": ("ws", "it", DEF),
    "update_notebook": ("ws", {"id": "it", "displayName": "nb"}, "nb", NB),
    "update_role_assignment": ("ws", "a1", "Member"),
    "update_schedule": ("ws", "it", "Pipeline", "s1", True, {"type": "Cron", "interval": 60}),
}


def test_the_list_holds_every_write_method():
    """A new write method on FabricClient has to join WRITES, so its failures are walked too."""
    verbs = ("add_", "assign_", "cancel_", "create_", "delete_", "put_", "refresh_", "run_", "save_", "set_",
             "update_", "git_update")
    found = {n for n in dir(FabricClient) if n.startswith(verbs) and callable(getattr(FabricClient, n))}
    assert found == set(WRITES)


@pytest.mark.parametrize("status", [400, 403, 404, 409, 500, 503])
@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_refused_or_failed_write_raises_and_is_sent_once(name, status):
    session = Always(status)
    client = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(FabricError) as raised:
        getattr(client, name)(*WRITES[name])
    assert raised.value.status == status, name
    writes = [c for c in session.calls if c[0] not in SAFE_METHODS]
    # A write that reached Fabric and failed may have half happened, so it isn't sent again. (A
    # method that looks the item up first can fail on that read before sending any write.)
    assert len(writes) <= 1, (name, session.calls)
    assert all(url.startswith("https://api.fabric.microsoft.com/") for _, url in session.calls), session.calls


# ---- OneLake file writes (FileStore): upload, delete, mkdir

import requests  # noqa: E402

from fabric_desktop import onelake  # noqa: E402


class DfsFailing:
    """OneLake's DFS API that answers ``status`` to one upload step (create, write, commit or replace) and
    200 to everything else."""

    def __init__(self, step, status):
        self.step, self.status, self.calls = step, status, []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        params, headers = params or {}, headers or {}
        step = ("replace" if headers.get("x-ms-rename-source") else "create" if params.get("resource") == "file"
                else "write" if params.get("action") == "append" else "commit" if params.get("action") == "flush"
                else "delete" if method == "DELETE" else "mkdir" if params.get("resource") == "directory" else "?")
        self.calls.append((step, url))
        r = requests.Response()
        r.status_code, r._content, r._content_consumed = (self.status if step == self.step else 200), b"{}", True
        r.headers["x-ms-request-id"] = "00000000-0000-0000-0000-000000000000"
        return r


def _store(fake):
    return onelake.FileStore(lambda: "tok", session=fake, sleep=lambda s: None)


@pytest.mark.parametrize("status", [403, 409, 500, 503])
@pytest.mark.parametrize("step", ["create", "write", "commit", "replace"])
def test_a_failed_upload_step_raises_never_renames_and_removes_its_new_file(step, status):
    fake = DfsFailing(step, status)
    with pytest.raises(requests.HTTPError, match=f"{status}"):
        _store(fake).upload("ws", "lh", "Files/out/data.csv", b"a,b\n1,2\n")
    steps = [s for s, _ in fake.calls]
    assert steps.count(step) == 1, steps  # a write that may have happened is sent once
    if step != "replace":
        assert "replace" not in steps, steps  # the file it was replacing is left as it was
    if step == "create":
        assert "delete" not in steps  # nothing was made, so nothing is removed
    else:  # the new file was made: it is removed, never the file at the path
        deleted = [url for s, url in fake.calls if s == "delete"]
        assert len(deleted) == 1 and deleted[0].endswith(onelake.UPLOADING_SUFFIX), deleted


@pytest.mark.parametrize("status", [403, 409, 500, 503])
def test_a_refused_or_failed_delete_raises_and_is_sent_once(status):
    fake = DfsFailing("delete", status)
    with pytest.raises(requests.HTTPError, match=f"{status}"):
        _store(fake).delete("ws", "lh", "Files/out")
    assert [s for s, _ in fake.calls] == ["delete"]


def test_a_delete_of_a_missing_path_is_done():
    _store(DfsFailing("delete", 404)).delete("ws", "lh", "Files/gone")


@pytest.mark.parametrize("status", [403, 500])
def test_a_refused_or_failed_folder_create_raises(status):
    fake = DfsFailing("mkdir", status)
    with pytest.raises(requests.HTTPError, match=f"{status}"):
        _store(fake).mkdir("ws", "lh", "Files/out")
    # If-None-Match makes a folder create safe to repeat: a 5xx is tried again, a 403 isn't.
    assert len(fake.calls) == (1 if status < 500 else 5)


def test_a_folder_that_is_already_there_is_done():
    _store(DfsFailing("mkdir", 409)).mkdir("ws", "lh", "Files/out")
