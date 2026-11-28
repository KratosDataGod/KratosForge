"""Databricks workspace sync: the path the other tests leave out (coverage pass 20). A listed object with
no path, or one that isn't an object at all, is left out of the folder's notebooks and files."""

from __future__ import annotations

from fabric_desktop.engines import databricks_workspace_sync as sync
from test_databricks_discovery import Resp
from test_databricks_workspace_sync import REMOTE, FakeWorkspace, client


class OddListing(FakeWorkspace):
    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        if url.endswith("/api/2.0/workspace/list"):
            return Resp(body={"objects": [
                {"object_type": "NOTEBOOK", "path": "", "modified_at": 3},      # no name to keep it under
                {"object_type": "FILE", "modified_at": 4},                       # no path at all
                "not an object",
                {"object_type": "FILE", "path": f"{REMOTE}/notes.txt", "modified_at": 0},
            ]})
        return super().request(method, url, headers, timeout, json, params)


def test_listed_objects_without_a_name_are_left_out():
    there, exists = sync.remote_files(client(OddListing()), REMOTE)
    assert exists
    assert there == {"notes.txt": {"type": "FILE", "modified_at": 0}}
