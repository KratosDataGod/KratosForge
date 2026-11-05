""""Not set up yet" is a type, not a wording (#916).

Every place that says a step hasn't been taken yet (no workspace assigned, no metadata project open,
a lakehouse not promoted) raises ``NotSetUp``, and Monitoring and Data mesh Home decide by that type.
Nothing here talks to a tenant."""

import types

import pytest

from fabric_desktop import mailbox
from fabric_desktop.errors import NotSetUp
from fabric_desktop.meta import OneLakeStore
from fabric_desktop.meta_service import NoMetadataProject
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate, model


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda scopes: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    return s


@pytest.fixture
def project(svc, tmp_path):
    svc.meta_init(str(tmp_path / "proj"), "3")
    return svc._project()


def _bare_store(tmp_path, env="test"):
    """An environment's workspace with no lakehouses in it yet: nothing promoted."""
    return OneLakeStore(env, "ws-1", {}, lambda: "token", tmp_path / "cache")


def _not_set_up(call):
    with pytest.raises(NotSetUp) as info:
        call()
    assert model.classify_error(info.value)[0] == "not_set_up"
    return str(info.value)


# --------------------------------------------------------------------------- each raise site


def test_no_metadata_project_is_not_set_up(svc):
    assert issubclass(NoMetadataProject, NotSetUp)
    assert _not_set_up(svc._project) == "No metadata project is open. Create one or open its folder."


def test_an_environment_with_no_workspace_is_not_set_up(svc, project):
    svc._write_environments(project, project.environments | {"test": {"target": "onelake"}})
    assert _not_set_up(lambda: svc._store(svc._project(), "test")) == (
        "Assign a workspace to Test first (Data mesh > Deploy > Environments and promote).")
    assert _not_set_up(lambda: svc.meta_set_environment("prod", target="onelake")) == "Assign a workspace to Prod first."
    assert _not_set_up(lambda: svc._publish_varlib(project, "test")) == (
        "Assign a workspace to Test first (Data mesh > Deploy > Environments and promote).")


def test_lh_meta_not_promoted_is_not_set_up(svc, project, tmp_path, monkeypatch):
    store = _bare_store(tmp_path)
    assert _not_set_up(lambda: mailbox.control_backend_for(store, None, lambda: "token")) == (
        "LH_Meta doesn't exist in this environment's workspace yet; promote to it first")
    meta = "LH_Meta doesn't exist in the test workspace yet; promote to test first"
    assert _not_set_up(lambda: svc.lease_backend(project, "test", store)) == meta
    assert _not_set_up(lambda: svc._fabric_bundle(project, "test", store)) == meta
    monkeypatch.setattr(svc, "_store", lambda project, env: store)
    monkeypatch.setattr(svc, "_env_cfg", lambda project, env: {"workspace_id": "ws-1"})
    assert _not_set_up(lambda: svc._bpa_target("test", None, None)) == meta


def test_a_layer_lakehouse_not_promoted_is_not_set_up(svc, project, tmp_path, monkeypatch):
    store = _bare_store(tmp_path)
    monkeypatch.setattr(svc, "_store", lambda project, env: store)
    assert _not_set_up(lambda: svc.meta_build_model("test")).endswith(
        " doesn't exist in the test workspace yet; promote and run first")
    assert _not_set_up(lambda: store._lakehouse_id("LH_Gold")) == (
        "LH_Gold doesn't exist in the test workspace yet; promote to create it")
    assert _not_set_up(lambda: _bare_store(tmp_path, "dev")._lakehouse_id("LH_Gold")) == (
        "LH_Gold doesn't exist in the dev workspace yet; run Dev to create it")


def test_not_set_up_is_still_a_lookup_error():
    # Existing ``except LookupError`` handlers keep catching it.
    with pytest.raises(LookupError):
        raise NotSetUp("Assign a workspace to Test first.")


# --------------------------------------------------------------------------- decided by type, not by words


@pytest.mark.parametrize("text", [
    "Assign a workspace to Test first (Data mesh > Deploy > Environments and promote).",
    "No metadata project is open. Create one or open its folder.",
    "LH_Meta doesn't exist in this environment's workspace yet; promote to it first",
    "LH_Silver doesn't exist in the test workspace yet; promote and run first",
])
def test_a_plain_lookup_error_with_the_same_words_is_an_error(text):
    assert model.classify_error(LookupError(text)) == ("error", f"LookupError: {text}")
    signals, not_set_up = evaluate.unreadable("test", LookupError(text))
    assert not_set_up == "" and [s["kind"] for s in signals] == ["cant_evaluate"]


@pytest.mark.parametrize("text", [
    "Test has no workspace yet. Pick one under Data mesh > Environments.",
    "Nothing has been deployed to this environment so far.",
])
def test_a_reworded_not_set_up_is_still_not_set_up(text):
    assert model.classify_error(NotSetUp(text)) == ("not_set_up", text)
    assert model.classify_error(NoMetadataProject(text)) == ("not_set_up", text)
    assert evaluate.unreadable("test", NotSetUp(text)) == ([], text)
