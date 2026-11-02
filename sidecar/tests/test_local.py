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
    (tmp_path / "stage" / "part-0.parquet").parent.mkdir()
    (tmp_path / "stage" / "part-0.parquet").write_bytes(b"x" * 1000)  # an old build's full copy
    table = stage_locally(data, tmp_path, "stage")
    assert table.protocol().min_reader_version == 1
    assert table.schema().to_arrow().names == data.schema.names
    assert list(tmp_path.iterdir()) == []  # checked, then deleted: nothing stays on disk, old copies neither


def test_staging_still_refuses_a_schema_outside_the_profile(tmp_path):
    import datetime as dt
    data = pa.table({"at": pa.array([dt.datetime(2026, 1, 1)], pa.timestamp("us"))})  # timestamp_ntz: reader 3
    with pytest.raises(delta_profile.ProfileViolation, match="reader version 3"):
        stage_locally(data, tmp_path, "stage")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["..", "../../victim", "_writes", "a/b", "", "."])
def test_staging_never_deletes_outside_its_own_folder(tmp_path, name):
    """A job's name becomes a folder whose old contents are deleted: a name pointing anywhere else is refused."""
    root = tmp_path / "app" / "staging"
    keep = [tmp_path / "app" / "notebooks" / "a.ipynb", tmp_path / "victim" / "b.txt", root / "_writes" / "w.arrow"]
    for f in keep:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    with pytest.raises(ValueError):
        stage_locally(pa.table({"a": [1]}), root, name)
    with pytest.raises(ValueError):
        stage_locally(pa.table({"a": [1]}), root, str(tmp_path / "victim"))  # an absolute path
    assert all(f.exists() for f in keep)


@pytest.mark.parametrize("name", ["..", "../x", "C:\\Users\\x", "/etc", "_writes", " "])
def test_job_names_must_be_plain_folder_names(tmp_path, name):
    job = Job(name=name, engine="duckdb", base_dir=tmp_path, target=Target("ws", "lh", "t"), sql="select 1")
    with pytest.raises(ValueError, match="job name"):
        job.validate()


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


def test_storage_token_has_half_an_hour_left_for_long_onelake_writes(tmp_path):
    """HD-034: a OneLake write keeps its token until it commits, so a storage token with less than
    STORAGE_MIN_SECONDS left is refreshed first; Fabric tokens aren't, and a tenant whose tokens
    live shorter than that isn't asked again on every call, unless the refresh returned the same token."""
    from fabric_desktop.auth import STORAGE_MIN_SECONDS, Authenticator

    class App:
        def __init__(self, cached, fresh, same=False):
            self.cached, self.fresh, self.same, self.forced = cached, fresh, same, 0

        def get_accounts(self):
            return [{"username": "chris@contoso.com"}]

        def acquire_token_silent(self, scopes, account, force_refresh=False):
            if force_refresh:
                self.forced += 1
                return {"access_token": "old" if self.same else "new", "expires_in": self.fresh}
            return {"access_token": "old", "expires_in": self.cached}

    auth = Authenticator(cache_path=tmp_path / "cache.bin")
    auth.app = App(cached=STORAGE_MIN_SECONDS - 60, fresh=3600)
    assert auth.fabric_token() == "old" and auth.app.forced == 0
    assert auth.storage_token() == "new" and auth.app.forced == 1
    auth.app = App(cached=STORAGE_MIN_SECONDS + 60, fresh=3600)
    assert auth.storage_token() == "old" and auth.app.forced == 0

    auth.app = App(cached=600, fresh=600)  # tokens that only ever live 10 minutes
    assert auth.storage_token() == "new" and auth.app.forced == 1
    assert auth.storage_token() == "old" and auth.app.forced == 1

    # Windows sign-in's broker can answer a forced refresh from its own cache: the same token back
    # doesn't stop the next call asking again.
    auth = Authenticator(cache_path=tmp_path / "cache.bin")
    auth.app = App(cached=600, fresh=600, same=True)
    assert auth.storage_token() == "old" and auth.app.forced == 1
    assert auth.storage_token() == "old" and auth.app.forced == 2


def test_token_cache_survives_damage_and_is_swapped_in_whole(tmp_path):
    from fabric_desktop import auth as auth_mod
    from fabric_desktop.auth import Authenticator
    path = tmp_path / "cache.bin"
    path.write_text('{"AccessToken": {"half-writ')  # e.g. two runs wrote it at once before
    auth = Authenticator("11111111-2222-3333-4444-555555555555", cache_path=path)
    auth.cache.add({"client_id": auth.client_id, "scope": ["s"], "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    "response": {"access_token": "a", "expires_in": 3600, "token_type": "Bearer"}})
    auth._save()
    assert "AccessToken" in auth_mod.read_cache(path)[0] and sorted(tmp_path.iterdir()) == [path, path.with_name(path.name + ".lock")]  # encrypted on Windows
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


def test_a_browser_sign_in_left_open_gives_up_instead_of_holding_the_engine(tmp_path):
    import msal

    from fabric_desktop.auth import FABRIC_SCOPES, SIGN_IN_SECONDS, Authenticator, SignInError

    class App:
        asked = {}

        def get_accounts(self):
            return []

        def acquire_token_interactive(self, scopes, **kw):
            App.asked = kw
            raise msal.BrowserInteractionTimeoutError("User did not complete the flow in time")

    auth = Authenticator("11111111-2222-3333-4444-555555555555", cache_path=tmp_path / "cache.bin")
    assert not auth.broker  # an organization's own app signs in in the browser
    auth.app = App()
    with pytest.raises(SignInError, match="wasn't finished within 10 minutes. Sign in again."):
        auth._token(FABRIC_SCOPES)
    assert App.asked["timeout"] == SIGN_IN_SECONDS


def test_a_refreshed_token_skips_msals_cache_and_falls_back_to_a_normal_sign_in(tmp_path):
    from fabric_desktop.auth import FABRIC_SCOPES, Authenticator

    class App:
        def __init__(self, fresh):
            self.fresh, self.asked = fresh, []

        def get_accounts(self):
            return [{"username": "chris@contoso.com"}]

        def acquire_token_silent(self, scopes, account, force_refresh=False):
            self.asked.append(force_refresh)
            return self.fresh if force_refresh else {"access_token": "cached"}

    auth = Authenticator(cache_path=tmp_path / "cache.bin")
    auth.app = App({"access_token": "new"})
    assert auth.refreshed_token(FABRIC_SCOPES) == "new" and auth.app.asked == [True]
    auth.app = App(None)  # MSAL couldn't refresh silently: the usual path decides
    assert auth.refreshed_token(FABRIC_SCOPES) == "cached" and auth.app.asked == [True, False]
