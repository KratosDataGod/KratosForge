"""Metadata projects: the refusals and write paths the run tests don't reach. Entity saves, renames and
deletes, environment and Keys workspace settings, which lakehouses a OneLake environment uses or creates
(with a stand-in for Fabric), where leases live, and reads that say when an environment can't be read."""

from __future__ import annotations

import polars as pl
import pytest

from fabric_desktop import meta_service as ms
from fabric_desktop.leases import LocalBackend, OneLakeBackend
from fabric_desktop.meta.store import LocalStore, OneLakeStore
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


class FakeFabric:
    """Lists and creates lakehouses per workspace, as Fabric does."""

    def __init__(self, lakehouses=None):
        self.lakehouses = {ws: dict(v) for ws, v in (lakehouses or {}).items()}
        self.created, self.listed = [], []

    def list_lakehouses(self, ws):
        self.listed.append(ws)
        return [{"displayName": n, "id": i} for n, i in self.lakehouses.get(ws, {}).items()]

    def create_lakehouse(self, ws, name, description):
        self.created.append((ws, name))
        self.lakehouses.setdefault(ws, {})[name] = f"id-{ws}-{name}"
        return {"id": f"id-{ws}-{name}"}

    def create_workspace(self, name, capacity_id, description):
        self.made = (name, capacity_id, description)
        return {"id": "ws-new"}


@pytest.fixture
def fabric(monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    monkeypatch.setattr(Service, "_platform_config", lambda self: {})
    return fake


# ---- small helpers

@pytest.mark.parametrize("value, days", [(None, ms.DEFAULT_RETENTION_DAYS), ("", ms.DEFAULT_RETENTION_DAYS),
                                         ("30", 30), (14.0, 14), ("2.5", "2.5"), ("soon", "soon"), ([1], [1])])
def test_retention_days_from_the_ui(value, days):
    assert ms._days(value) == days


def test_toml_values_are_written_inline():
    assert ms._toml({}) == "{}"
    assert ms._toml({"ingest": {"id": "a", "n": [1, True]}}) == '{ ingest = { id = "a", n = [1, true] } }'


def test_a_project_folder_must_be_a_full_path(svc):
    with pytest.raises(ValueError, match="full path"):
        svc.meta_open("relative/folder")


def test_opening_a_folder_that_isnt_a_project_says_so(svc, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="has no layers.toml"):
        svc.meta_open(str(tmp_path / "empty"))


def test_a_project_saved_by_a_relative_path_asks_to_be_opened_again(svc):
    svc._save_settings(meta_project="proj")
    with pytest.raises(ms.NoMetadataProject, match="not a full path"):
        svc._project()


def test_init_without_a_sign_in_leaves_the_owner_blank(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")

    def nobody():
        raise RuntimeError("not signed in")

    monkeypatch.setattr(type(s.auth), "signed_in_user", lambda self: nobody())
    assert s.meta_init(str(tmp_path / "p"), "3")


def test_settings_that_cant_be_sealed_are_still_read_and_the_failure_said_once(svc, monkeypatch, caplog):
    monkeypatch.setattr(ms.sealed_settings, "needs_sealing", lambda s: True)

    def full(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(ms, "write_json", full)
    monkeypatch.setattr(ms, "_seal_failed", [])
    assert svc._settings()["meta_project"]
    assert svc._settings()["meta_project"]
    assert caplog.text.count("Couldn't encrypt") == 1


# ---- entities

def entity_text(svc, name):
    return svc._project().entities[name].path.read_text(encoding="utf-8")


def test_an_entity_save_needs_valid_toml_and_a_name(svc):
    with pytest.raises(ValueError, match="isn't valid TOML"):
        svc.meta_save_entity("silver.orders", "name = ")
    with pytest.raises(ValueError, match="needs a name"):
        svc.meta_save_entity("silver.orders", 'description = "x"')
    with pytest.raises(ValueError, match="Not saved: Bad Name"):
        svc.meta_save_entity("silver.orders", 'name = "Bad Name"')


def test_a_rename_onto_another_entity_or_away_from_one_that_is_read_is_refused(svc):
    text = entity_text(svc, "silver.orders")
    with pytest.raises(ValueError, match="silver.customers already exists"):
        svc.meta_save_entity("silver.orders", text.replace('"silver.orders"', '"silver.customers"'))
    with pytest.raises(ms.GateError, match="can't rename silver.orders"):
        svc.meta_save_entity("silver.orders", text.replace('"silver.orders"', '"silver.orders_v2"'))
    assert "silver.orders" in svc._project().entities  # nothing moved


def test_a_rename_writes_the_new_file_before_removing_the_old(svc):
    text = entity_text(svc, "gold.fact_sales")
    out = svc.meta_save_entity("gold.fact_sales", text.replace('"gold.fact_sales"', '"gold.fact_orders"'))
    names = svc._project().entities
    assert out and "gold.fact_orders" in names and "gold.fact_sales" not in names


def test_deleting_an_entity_others_read_is_refused_and_a_leaf_is_deleted(svc):
    with pytest.raises(ms.GateError, match="can't delete bronze.orders"):
        svc.meta_delete_entity("bronze.orders")
    assert svc.meta_delete_entity("gold.fact_sales") == {"deleted": "gold.fact_sales"}
    assert "gold.fact_sales" not in svc._project().entities


@pytest.mark.parametrize("template, name, match", [("nope", "silver.x", "template must be one of"),
                                                    ("clean", "silver.orders", "already exists"),
                                                    ("clean", "silver.a/b", "none can hold"),
                                                    ("clean", "platinum.x", "platinum is not a layer")])
def test_a_new_entity_is_refused_before_anything_is_written(svc, template, name, match):
    before = sorted(svc._project().entities)
    with pytest.raises(ValueError, match=match):
        svc.meta_new_entity(template, name)
    assert sorted(svc._project().entities) == before


def test_a_files_entity_with_no_files_yet_starts_with_a_placeholder(svc, monkeypatch):
    monkeypatch.setattr(type(svc.auth), "signed_in_user", lambda self: (_ for _ in ()).throw(RuntimeError("no")))
    out = svc.meta_new_entity("files", "bronze.returns", source="Files/returns")
    assert out and "bronze.returns" in svc._project().entities


# ---- environments

def test_environment_settings_refuse_unknown_names_and_targets(svc):
    with pytest.raises(ValueError, match="environment must be one of"):
        svc.meta_set_environment("qa", "ws")
    with pytest.raises(ValueError, match="target is onelake"):
        svc.meta_set_environment("test", target="cloud")
    with pytest.raises(ms.NotSetUp, match="Assign a workspace to Test first"):
        svc.meta_set_environment("test", target="onelake")
    with pytest.raises(ValueError, match="environment must be one of"):
        svc.meta_set_keys_workspace("qa", "ws")
    with pytest.raises(ValueError, match="environment must be one of"):
        svc.meta_create_workspace("qa", "WS")


def test_a_local_environment_drops_fabric_compute(svc):
    svc.meta_set_environment("test", "ws-test", "Test")
    envs = {k: dict(v) for k, v in svc._project().environments.items()}
    envs["test"]["compute"] = "fabric"
    svc._write_environments(svc._project(), envs)
    svc.meta_set_environment("test", target="local")
    assert "compute" not in svc._project().environments["test"]


def test_create_workspace_needs_a_name_and_assigns_the_new_one(svc, fabric):
    with pytest.raises(ValueError, match="Give the workspace a name"):
        svc.meta_create_workspace("test", "   ")
    out = svc.meta_create_workspace("test", " Sales - Test ", "cap1")
    assert out["created"] == {"id": "ws-new", "displayName": "Sales - Test"}
    assert fabric.made[:2] == ("Sales - Test", "cap1")
    assert svc._project().environments["test"]["workspace_id"] == "ws-new"


# ---- which lakehouses an environment uses

def test_store_refuses_an_unknown_environment_and_one_without_a_workspace(svc, fabric):
    with pytest.raises(ValueError, match="environment must be one of"):
        svc._store(svc._project(), "qa")
    with pytest.raises(ms.NotSetUp, match="Assign a workspace to Test first"):
        svc._store(svc._project(), "test")
    assert not fabric.listed


def test_a_onelake_store_finds_lakehouses_per_workspace_and_creates_only_when_asked(svc, fabric):
    svc.meta_set_environment("test", "ws-test", "Test")
    svc.meta_set_designation("test", "product", "ws-prod-data", "Products")
    proj = svc._project()
    gold = proj.layer("gold")
    gold.workspace = "product"
    fabric.lakehouses = {"ws-test": {"LH_Bronze": "b1"}, "ws-prod-data": {"LH_Gold": "g1", "LH_Bronze": "other"}}

    store = svc._store(proj, "test")
    assert isinstance(store, OneLakeStore)
    assert store.lakehouse_ids == {"LH_Bronze": "b1", "LH_Gold": "g1"}  # each name from its own workspace
    assert store.workspace_of("LH_Gold") == "ws-prod-data" and store.workspace_of("LH_Bronze") == "ws-test"
    assert not fabric.created

    store = svc._store(proj, "test", create=True)
    assert sorted(fabric.created) == [("ws-test", ms.META_LAKEHOUSE), ("ws-test", "LH_Silver")]
    assert store.lakehouse_ids["LH_Silver"] == "id-ws-test-LH_Silver"


def test_the_platforms_landing_zone_is_used_never_created(svc, fabric, monkeypatch):
    svc.meta_set_environment("test", "ws-test", "Test")
    monkeypatch.setattr(Service, "_platform_config", lambda self: {"test": {"landing_id": "land", "workspace_id": "ws-land"}})
    store = svc._store(svc._project(), "test", create=True)
    assert store.lakehouse_ids[ms.LANDING_LAKEHOUSE] == "land"
    assert store.workspace_of(ms.LANDING_LAKEHOUSE) == "ws-land"
    assert ("ws-land", ms.LANDING_LAKEHOUSE) not in fabric.created


def test_keys_store_needs_a_keys_workspace_and_creates_lh_keys_only_when_asked(svc, fabric, monkeypatch):
    from fabric_desktop.meta import keys as keys_mod
    monkeypatch.setattr(Service, "_keys_in_fabric", lambda self, env: False)
    proj = svc._project()
    with pytest.raises(ValueError, match="environment must be one of"):
        svc._keys_store(proj, "qa")
    assert isinstance(svc._keys_store(proj, "dev"), LocalStore)
    svc.meta_set_environment("test", "ws-test", "Test")
    with pytest.raises(LookupError, match="Pick a Keys workspace for Test"):
        svc._keys_store(svc._project(), "test")
    envs = {k: dict(v) for k, v in svc._project().environments.items()}
    envs["test"]["keys_workspace_id"] = "ws-keys"
    svc._write_environments(svc._project(), envs)
    store = svc._keys_store(svc._project(), "test")
    assert store.lakehouse_ids == {} and not fabric.created
    store = svc._keys_store(svc._project(), "test", create=True)
    assert fabric.created == [("ws-keys", keys_mod.KEYS_LAKEHOUSE)]


def test_keys_follow_the_environments_own_choice_first(svc, monkeypatch):
    monkeypatch.setattr(Service, "_keys_in_fabric", lambda self, env: True)
    monkeypatch.setattr(Service, "_keys_config", lambda self: {"shared": True})
    proj = svc._project()
    assert svc._keys_in_platform(proj, "test")  # in Fabric, and no LH_Keys yet
    assert svc._keys_database(proj, "test") == "SQL_Keys"
    proj.environments["test"]["keys"] = "lakehouse"
    assert not svc._keys_in_platform(proj, "test") and svc._keys_database(proj, "test") == ""
    (proj.local_data / "keys-dev").mkdir(parents=True)
    assert not svc._keys_in_platform(proj, "dev")  # keys already issued on this PC move first


# ---- leases and releases

def test_leases_live_in_lh_meta_or_a_local_folder(svc, tmp_path):
    proj = svc._project()
    local = LocalStore(tmp_path / "d", "dev")
    assert isinstance(svc.lease_backend(proj, "dev", local), LocalBackend)
    no_meta = OneLakeStore("test", "ws", {}, lambda: "t", tmp_path / "c")
    with pytest.raises(ms.NotSetUp, match="promote to test first"):
        svc.lease_backend(proj, "test", no_meta)
    with_meta = OneLakeStore("test", "ws", {ms.META_LAKEHOUSE: "m"}, lambda: "t", tmp_path / "c")
    assert isinstance(svc.lease_backend(proj, "test", with_meta), OneLakeBackend)
    with pytest.raises(TypeError, match="no lease backend for object"):
        svc.lease_backend(proj, "dev", object())


class Deployments:
    def __init__(self, versions):
        self.versions = versions

    def meta_where(self, table, cond):
        return pl.DataFrame({"env": ["test"] * len(self.versions), "version": self.versions},
                            schema={"env": pl.String, "version": pl.String})


def test_test_runs_the_release_it_is_on_or_this_folder_when_that_release_has_no_files(svc, monkeypatch):
    proj = svc._project()
    assert svc._released(proj, "dev", None) is proj
    assert svc._released(proj, "test", Deployments([])) is proj
    assert svc._released(proj, "test", Deployments([proj.version])) is proj
    monkeypatch.setattr(ms.snapshot, "restore", lambda p, s, v: (_ for _ in ()).throw(LookupError(v)))
    assert svc._released(proj, "test", Deployments(["0.0.1"])) is proj
    monkeypatch.setattr(ms.snapshot, "restore", lambda p, s, v: ("restored", v))
    assert svc._released(proj, "test", Deployments(["0.0.1"])) == ("restored", "0.0.1")


def test_release_reads_say_when_an_environment_cant_be_read(svc):
    out = svc.meta_releases("test")
    assert out["releases"] == [] and "Assign a workspace to Test" in out["problem"]
    out = svc.meta_last_release("test")
    assert out["release"] is None and "Assign a workspace to Test" in out["problem"]
    assert svc.meta_last_release("dev") == {"env": "dev", "release": None, "problem": None}


def test_rollback_preview_of_an_environment_never_promoted_to_says_so(svc):
    with pytest.raises(LookupError, match="Nothing has been promoted to Dev yet"):
        svc.meta_rollback_preview("dev", "1.0.0")


def test_dev_is_rolled_back_with_git(svc):
    with pytest.raises(ValueError, match="roll back with Git"):
        svc.meta_rollback("dev", "1.0.0")


def test_lineage_of_an_unknown_entity_and_of_one_column(svc):
    with pytest.raises(LookupError, match="No entity silver.nope"):
        svc.meta_lineage("silver.nope")
    cols = svc.meta_lineage("silver.orders")["columns"]
    col = next(iter(cols))
    out = svc.meta_lineage("silver.orders", col)
    assert out["column"] == col and "upstream" in out and "downstream" in out


def test_run_detail_of_an_unknown_run_says_so(svc):
    with pytest.raises(KeyError, match="no run nope in dev"):
        svc.meta_run_detail("nope")


def test_signed_in_object_id_is_none_when_the_sign_in_cant_say(svc):
    class Broken:
        def signed_in_object_id(self):
            raise RuntimeError("token cache locked")

    svc._auth = Broken()
    assert svc._signed_in_object_id() is None
