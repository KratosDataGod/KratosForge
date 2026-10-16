import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile
from fabric_desktop.jobs import Job, Target, run_local
from fabric_desktop.onelake import TableRef, check_name
from fabric_desktop.publish import stage_locally
from fabric_desktop.server import handle

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_duckdb_job_runs_locally():
    data = run_local(Job.load(EXAMPLES / "sales_by_region.toml"))
    assert data.column("region").to_pylist() == ["East", "North", "West"]
    assert data.column("total_amount").to_pylist() == [960, 400, 1350]


def test_duckdb_job_uses_utc(tmp_path):
    job = Job(name="tz", engine="duckdb", base_dir=tmp_path, target=Target("ws", "lh", "tz"),
              sql="select current_setting('TimeZone') as tz")
    assert run_local(job).column("tz").to_pylist() == ["UTC"]


def test_polars_job_runs_locally():
    data = run_local(Job.load(EXAMPLES / "top_products.toml"))
    assert data.column("product").to_pylist()[0] == "Bikes"
    assert data.num_rows == 3


def test_staged_table_matches_fabric_profile(tmp_path):
    data = run_local(Job.load(EXAMPLES / "sales_by_region.toml"))
    table = stage_locally(data, tmp_path / "stage")
    assert table.protocol().min_reader_version == 1
    assert table.to_pyarrow_table().num_rows == 3


def test_profile_rejects_v2_checkpoints(tmp_path):
    write_deltalake(str(tmp_path), pa.table({"a": [1]}), configuration={"delta.checkpointPolicy": "v2"})
    with pytest.raises(delta_profile.ProfileViolation):
        delta_profile.check_table(DeltaTable(str(tmp_path)))


def test_table_uri_uses_schema_folder():
    ref = TableRef("ws-guid", "lh-guid", "sales", "orders")
    assert ref.uri == "abfss://ws-guid@onelake.dfs.fabric.microsoft.com/lh-guid/Tables/sales/orders"


@pytest.mark.parametrize("name", ["bad#name", "a:b", "", " padded", "q?"])
def test_bad_names_rejected(name):
    with pytest.raises(ValueError):
        check_name("table", name)


def test_job_validation(tmp_path):
    job = tmp_path / "job.toml"
    job.write_text('name="x"\nengine="spark"\nsql="select 1"\n[target]\nworkspace_id="w"\nlakehouse="l"\ntable="t"\n')
    with pytest.raises(ValueError, match="engine"):
        Job.load(job)


def test_server_reports_errors_without_crashing():
    class Fake:
        def history(self, limit=50):
            return [{"job": "x"}]

        def list_workspaces(self):
            raise RuntimeError("boom")

    assert handle(Fake(), json.dumps({"id": 1, "method": "history"})) == {"id": 1, "result": [{"job": "x"}]}
    assert "boom" in handle(Fake(), json.dumps({"id": 2, "method": "list_workspaces"}))["error"]
    assert "unknown" in handle(Fake(), json.dumps({"id": 3, "method": "rm_rf"}))["error"]



@pytest.mark.skipif(os.name == "nt", reason="closes fd 2 in the child, which needs POSIX")
def test_server_starts_without_stderr(tmp_path):
    """The windowed exe can start Python with no stderr; the server must still answer."""
    out = subprocess.run([sys.executable, "-m", "fabric_desktop.server"], input='{"id": 1, "method": "environment"}\n',
                         stdout=subprocess.PIPE, text=True, timeout=60, env=os.environ | {"LOCALAPPDATA": str(tmp_path)},
                         preexec_fn=lambda: os.close(2))
    assert out.returncode == 0
    assert json.loads(out.stdout)["id"] == 1


def test_signed_in_tenant_comes_from_the_msal_account():
    from fabric_desktop.auth import Authenticator

    class App:
        def __init__(self, accounts):
            self.accounts = accounts

        def get_accounts(self):
            return self.accounts

    auth = Authenticator.__new__(Authenticator)
    auth.app = App([{"username": "chris@contoso.com", "home_account_id": "oid-1.72f988bf-86f1-41af-91ab-2d7cd011db47"}])
    assert auth.signed_in_tenant() == "72f988bf-86f1-41af-91ab-2d7cd011db47"
    auth.app = App([])
    assert auth.signed_in_tenant() is None


def test_microsofts_sign_in_asks_for_default_scopes_and_an_own_app_for_narrow_ones(tmp_path):
    from fabric_desktop.auth import EXECUTE_SCOPES, MICROSOFT_CLIENT_ID, STORAGE_SCOPES, Authenticator

    class App:
        def __init__(self):
            self.asked = []

        def get_accounts(self):
            return [{"username": "chris@contoso.com"}]

        def acquire_token_silent(self, scopes, account):
            self.asked.append(scopes)
            return {"access_token": "tok"}

    auth = Authenticator(cache_path=tmp_path / "cache.bin")  # nothing set: Microsoft's app
    assert auth.client_id == MICROSOFT_CLIENT_ID
    auth.app = App()
    auth._token(EXECUTE_SCOPES)
    auth.storage_token()
    assert auth.silent_token(STORAGE_SCOPES) == "tok"
    assert auth.app.asked == [["https://api.fabric.microsoft.com/.default"], ["https://storage.azure.com/.default"],
                              ["https://storage.azure.com/.default"]]

    own = Authenticator("11111111-2222-3333-4444-555555555555", cache_path=tmp_path / "cache.bin")
    own.app = App()
    own._token(EXECUTE_SCOPES)
    assert own.app.asked == [EXECUTE_SCOPES] and not own.broker


def test_token_cache_survives_damage_and_is_swapped_in_whole(tmp_path):
    from fabric_desktop.auth import Authenticator
    path = tmp_path / "cache.bin"
    path.write_text('{"AccessToken": {"half-writ')  # e.g. two runs wrote it at once before
    auth = Authenticator("11111111-2222-3333-4444-555555555555", cache_path=path)
    auth.cache.add({"client_id": auth.client_id, "scope": ["s"], "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    "response": {"access_token": "a", "expires_in": 3600, "token_type": "Bearer"}})
    auth._save()
    assert "AccessToken" in path.read_text() and list(tmp_path.iterdir()) == [path]
    Authenticator("11111111-2222-3333-4444-555555555555", cache_path=path)  # reads back cleanly


def test_catalog_lists_lakehouses_across_workspaces_and_keeps_favorites(tmp_path, monkeypatch):
    from fabric_desktop import service as service_mod
    from fabric_desktop.service import Service

    class Auth:
        def fabric_token(self):
            return "tok"

        def silent_token(self, scopes):
            return None

    class Client:
        def __init__(self, token):
            pass

        def list_workspaces(self):
            return [{"id": "w1", "displayName": "Sales"}, {"id": "w2", "displayName": "Paused"}]

        def list_lakehouses(self, ws):
            if ws == "w2":
                raise RuntimeError("capacity is paused")
            return [{"id": "lh1", "displayName": "LH_Gold"}]

        def list_items(self, ws):
            if ws == "w2":
                raise RuntimeError("capacity is paused")
            return [{"id": "lh1", "displayName": "LH_Gold", "type": "Lakehouse"},
                    {"id": "nb1", "displayName": "Load orders", "type": "Notebook"}]

    monkeypatch.setattr(service_mod, "FabricClient", Client)
    svc = Service(client_id="x", root=tmp_path)
    svc._auth = Auth()
    assert svc.set_favorite("lh1", True) == ["lh1"]
    catalog = svc.catalog()
    gold = {"workspace_id": "w1", "workspace_name": "Sales", "item_id": "lh1", "lakehouse_id": "lh1",
            "name": "LH_Gold", "type": "Lakehouse", "description": ""}
    assert catalog["items"] == [gold]
    everything = svc.catalog(all_types=True)["items"]
    assert everything[0] == gold
    assert everything[1] == {"workspace_id": "w1", "workspace_name": "Sales", "item_id": "nb1", "name": "Load orders",
                             "type": "Notebook", "description": ""}
    assert catalog["skipped"] == ["Paused"]
    assert catalog["favorites"] == ["lh1"]
    assert svc.set_favorite("lh1", False) == []
    end = svc.endorsements(["w1"])
    assert end["available"] is False and end["pending"] is False and "admin" in end["reason"]


def test_workspace_marks_keep_favorites_and_pin_order(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(client_id="x", root=tmp_path)
    assert svc.workspace_marks() == {"favorites": [], "pins": []}
    svc.set_workspace_mark("w2", "pins", True)
    svc.set_workspace_mark("w1", "pins", True)
    svc.set_workspace_mark("w1", "favorites", True)
    assert Service(client_id="x", root=tmp_path).workspace_marks() == {"favorites": ["w1"], "pins": ["w2", "w1"]}
    assert svc.set_workspace_mark("w2", "pins", False)["pins"] == ["w1"]
    import pytest
    with pytest.raises(ValueError):
        svc.set_workspace_mark("w1", "bogus", True)
