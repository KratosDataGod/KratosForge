"""Fabric Environment items (PB-016 part 1): their published libraries and runtime, read with the
signed-in user's token, and how they differ from a notebook's packages on this PC."""

import json
import sys

import pytest
from test_fabric_api import Resp, client

from fabric_desktop import cli, fabric_environment
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service

WS = "11111111-1111-1111-1111-111111111111"
ENV = "22222222-2222-2222-2222-222222222222"
HERE = f"{sys.version_info.major}.{sys.version_info.minor}"


class Fabric:
    def __init__(self, libraries=(), runtime="1.3", fail=None, runtime_fail=None, staged=None, staged_runtime=None,
                 staged_fail=None):
        self.libraries, self.runtime, self.fail, self.runtime_fail = list(libraries), runtime, fail, runtime_fail
        # Unpublished changes: none unless a test stages some (staged = the item's saved library list)
        self.staged = list(staged) if staged is not None else None
        self.staged_runtime, self.staged_fail = staged_runtime, staged_fail
        self.calls = []

    def environment_libraries(self, ws, env, staged=False):
        self.calls.append(("staging/libraries" if staged else "libraries", ws, env))
        if self.fail:
            raise self.fail
        if staged:
            if self.staged_fail:
                raise self.staged_fail
            return self.libraries if self.staged is None else self.staged
        return self.libraries

    def environment_spark_compute(self, ws, env, staged=False):
        self.calls.append(("staging/sparkcompute" if staged else "sparkcompute", ws, env))
        if self.runtime_fail:
            raise self.runtime_fail
        return {"runtimeVersion": (self.staged_runtime or self.runtime) if staged else self.runtime, "driverCores": 4}


def test_the_client_reads_published_libraries_and_runtime_with_the_users_token():
    c, s = client([Resp(200, {"libraries": [{"name": "fuzzywuzzy", "libraryType": "External", "version": "0.0.1"}],
                              "continuationToken": "null", "continuationUri": "null"}),
                   Resp(200, {"runtimeVersion": "1.3"})])
    assert c.environment_libraries(WS, ENV) == [{"name": "fuzzywuzzy", "libraryType": "External", "version": "0.0.1"}]
    assert c.environment_spark_compute(WS, ENV)["runtimeVersion"] == "1.3"
    assert [(m, u) for m, u, _ in s.calls] == [
        ("GET", f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/environments/{ENV}/libraries?beta=False"),
        ("GET", f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/environments/{ENV}/sparkcompute?beta=False")]


def test_the_client_follows_the_next_page_of_libraries():
    nxt = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/environments/{ENV}/libraries?beta=False&continuationToken=2"
    c, s = client([Resp(200, {"libraries": [{"name": "a", "libraryType": "External"}], "continuationUri": nxt}),
                   Resp(200, {"libraries": [{"name": "b", "libraryType": "External"}]})])
    assert [lib["name"] for lib in c.environment_libraries(WS, ENV)] == ["a", "b"]
    assert s.calls[1][1] == nxt


def test_drift_reports_missing_different_and_local_only_packages():
    polars = next(p for p in fabric_environment.on_this_pc().values() if p["name"].lower() == "polars")
    fabric = Fabric([{"name": "polars", "libraryType": "External", "version": "0.0.1"},
                     {"name": "fuzzywuzzy", "libraryType": "External", "version": "0.18.0"},
                     {"name": "Scikit_Learn", "libraryType": "External", "version": None},
                     {"name": "demo_pkg-1.0-py3-none-any.whl", "libraryType": "Custom"},
                     {"name": "helpers.jar", "libraryType": "Custom"}])
    here = fabric_environment.on_this_pc([{"name": "demo-pkg", "version": "1.0.0"}, {"name": "rich", "version": "13.7.1"},
                                          {"name": "scikit-learn", "version": "1.5.2"}])
    out = fabric_environment.drift(fabric_environment.read(fabric, WS, ENV), here, python="3.12")
    assert out["drifted"] and out["runtime"] == "1.3"
    assert out["python"] == {"fabric": "3.11", "here": "3.12", "differs": True, "unknown": False}
    assert out["missing_here"] == [{"name": "fuzzywuzzy", "fabric": "0.18.0"}]
    assert out["different"] == [{"name": "polars", "fabric": "0.0.1", "here": polars["version"], "from": "engine"}]
    assert out["local_only"] == [{"name": "rich", "here": "13.7.1"}]  # 1.0 and 1.0.0 match; no version means any
    assert out["not_compared"] == ["helpers.jar"] and out["same"] == 2
    text = fabric_environment.summary(out)
    assert "Missing here: fuzzywuzzy==0.18.0" in text and "Python 3.11 in Fabric (runtime 1.3), 3.12 here." in text


def test_no_drift_when_every_published_library_matches():
    item = fabric_environment.read(Fabric([{"name": "rich", "libraryType": "External", "version": "13.7.1"}]), WS, ENV)
    out = fabric_environment.drift(item, fabric_environment.on_this_pc([{"name": "rich", "version": "13.7.1"}]), python="3.11")
    assert out["drifted"] is False
    assert fabric_environment.summary(out) == "No drift: 1 published library match this PC."


@pytest.mark.parametrize("error, says", [
    (FabricError("GET failed with 403", "InsufficientPrivileges", 403), "can't read this Environment item"),
    (FabricError("GET failed with 404", "EntityNotFound", 404), "no Environment item with that id"),
    (ConnectionError("network down"), "Couldn't read the Environment item"),
])
def test_an_item_that_cant_be_read_says_so_and_never_raises(error, says):
    out = fabric_environment.drift(fabric_environment.read(Fabric(fail=error), WS, ENV), fabric_environment.on_this_pc())
    assert out["status"] == "couldnt_read" and says in out["reason"] and out["drifted"] is None
    assert fabric_environment.summary(out).startswith("Couldn't compare with the Environment item:")


def test_ids_that_arent_guids_are_refused_before_any_call():
    fabric = Fabric()
    out = fabric_environment.read(fabric, WS, "../items")
    assert out["status"] == "couldnt_read" and fabric.calls == []


def test_a_runtime_that_cant_be_read_still_compares_the_libraries():
    item = fabric_environment.read(Fabric([{"name": "rich", "libraryType": "External", "version": "1"}],
                                          runtime_fail=FabricError("x", None, 403)), WS, ENV)
    out = fabric_environment.drift(item, fabric_environment.on_this_pc(), python="3.11")
    assert out["missing_here"] == [{"name": "rich", "fabric": "1"}] and "can't read" in out["runtime_problem"]
    assert out["python"]["differs"] is False


def test_the_service_counts_the_notebooks_environment(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc.environments.create("ml")
    svc.environments.attach("nb1", "ml")
    monkeypatch.setattr(type(svc.environments), "installed", lambda self, name: [{"name": "rich", "version": "13.7.1"}])
    monkeypatch.setattr(Service, "fabric", property(lambda self: Fabric([])))
    out = svc.environment_fabric_drift(WS, ENV, "nb1")
    assert out["environment"] == "ml" and out["local_only"] == [{"name": "rich", "here": "13.7.1"}]
    assert svc.environment_fabric_drift(WS, ENV)["local_only"] == []


def test_the_cli_exits_by_outcome(tmp_path, monkeypatch, capsys):
    results = iter([{"status": "read", "drifted": False, "same": 3, "python": {"differs": False}, "missing_here": [],
                     "different": [], "local_only": []},
                    {"status": "couldnt_read", "reason": "nope", "drifted": None}])
    monkeypatch.setattr(Service, "environment_fabric_drift", lambda self, *a: next(results))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: tmp_path)
    for code in (0, 2):
        with pytest.raises(SystemExit) as done:
            cli.main(["env-drift", "--workspace", WS, "--environment", ENV])
        assert done.value.code == code
    out = capsys.readouterr().out
    assert "No drift: 3 published libraries match this PC." in out and "nope" in out


def test_the_check_is_read_only_and_listed():
    from fabric_desktop import audit, server
    # The CLI calls it in-process; no screen does, so the RPC doesn't take it (HD-050 slice 4).
    assert "environment_fabric_drift" not in server.METHODS and "environment_fabric_drift" not in audit.READ_ONLY
    json.dumps(fabric_environment.drift(fabric_environment.read(Fabric(), WS, ENV), fabric_environment.on_this_pc()))


# ---- unpublished (staged) changes (#941)

def test_the_client_reads_staged_libraries_and_runtime_from_the_staging_paths():
    c, s = client([Resp(200, {"libraries": [], "continuationUri": "null"}), Resp(200, {"runtimeVersion": "1.3"})])
    c.environment_libraries(WS, ENV, staged=True)
    c.environment_spark_compute(WS, ENV, staged=True)
    assert [u for _, u, _ in s.calls] == [
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/environments/{ENV}/staging/libraries?beta=False",
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/environments/{ENV}/staging/sparkcompute?beta=False"]


PUBLISHED = [{"name": "rich", "libraryType": "External", "version": "13.7.1"},
             {"name": "fuzzywuzzy", "libraryType": "External", "version": "0.18.0"},
             {"name": "old_tool-1.0-py3-none-any.whl", "libraryType": "Custom"}]


def test_staged_changes_list_what_publishing_would_add_remove_and_change():
    staged = [{"name": "Rich", "libraryType": "External", "version": "14.0.0"},
              {"name": "fuzzywuzzy", "libraryType": "External", "version": "0.18"},
              {"name": "httpx", "libraryType": "External", "version": "0.27.0"},
              {"name": "new_tool-2.0-py3-none-any.whl", "libraryType": "Custom"}]
    item = fabric_environment.read(Fabric(PUBLISHED, staged=staged, staged_runtime="1.4"), WS, ENV)
    got = item["staged"]
    assert got["status"] == "read" and got["pending"] is True
    assert got["added"] == [{"name": "new_tool-2.0-py3-none-any.whl", "version": None}, {"name": "httpx", "version": "0.27.0"}]
    assert got["removed"] == [{"name": "old_tool-1.0-py3-none-any.whl", "version": None}]
    assert got["changed"] == [{"name": "Rich", "published": "13.7.1", "staged": "14.0.0"}], "0.18 and 0.18.0 are the same"
    assert got["runtime"] == {"published": "1.3", "staged": "1.4"}
    text = fabric_environment.summary(fabric_environment.drift(item, fabric_environment.on_this_pc(), python="3.11"))
    assert ("The Environment item has unpublished changes; publishing it adds new_tool-2.0-py3-none-any.whl, "
            "adds httpx==0.27.0, removes old_tool-1.0-py3-none-any.whl, changes Rich 13.7.1 to 14.0.0, "
            "changes the runtime 1.3 to 1.4.") in text


def test_nothing_staged_says_nothing_and_staged_changes_are_not_drift():
    item = fabric_environment.read(Fabric([{"name": "rich", "libraryType": "External", "version": "13.7.1"}]), WS, ENV)
    assert item["staged"] == {"status": "read", "added": [], "removed": [], "changed": [], "runtime": None, "pending": False}
    out = fabric_environment.drift(item, fabric_environment.on_this_pc([{"name": "rich", "version": "13.7.1"}]), python="3.11")
    assert fabric_environment.summary(out) == "No drift: 1 published library match this PC."
    pending = fabric_environment.read(Fabric([{"name": "rich", "libraryType": "External", "version": "13.7.1"}],
                                             staged=[]), WS, ENV)
    out = fabric_environment.drift(pending, fabric_environment.on_this_pc([{"name": "rich", "version": "13.7.1"}]), python="3.11")
    assert out["drifted"] is False and out["staged"]["removed"] == [{"name": "rich", "version": "13.7.1"}]


def test_a_staging_read_that_fails_is_reported_and_the_published_comparison_stands():
    item = fabric_environment.read(Fabric(PUBLISHED, staged_fail=FabricError("refused", "Forbidden", 403)), WS, ENV)
    assert item["status"] == "read" and len(item["libraries"]) == 2
    assert item["staged"] == {"status": "couldnt_read",
                              "reason": "You can't read this Environment item: ask its workspace admin for at least Viewer access."}
    text = fabric_environment.summary(fabric_environment.drift(item, fabric_environment.on_this_pc(), python="3.11"))
    assert "Couldn't read the item's unpublished changes: You can't read" in text


def test_a_staged_runtime_that_cant_be_read_still_reports_the_staged_libraries():
    item = fabric_environment.read(Fabric(PUBLISHED, staged=PUBLISHED[:2], runtime_fail=FabricError("boom", status=500)),
                                   WS, ENV)
    assert item["staged"]["removed"] == [{"name": "old_tool-1.0-py3-none-any.whl", "version": None}]
    assert "boom" in item["staged"]["runtime_problem"] and item["staged"]["runtime"] is None


def test_an_item_with_no_libraries_is_an_empty_list_not_an_error():
    c, _ = client([Resp(404, {"errorCode": "EnvironmentLibrariesNotFound", "message": "no libraries"})])
    assert c.environment_libraries(WS, ENV) == []
    c, _ = client([Resp(404, {"errorCode": "ItemNotFound", "message": "gone"})])
    with pytest.raises(FabricError):
        c.environment_libraries(WS, ENV)


def test_ids_must_be_whole_guids():
    for bad in ("------------------------------------", WS + "\n", WS.replace("-", "x", 1)):
        fabric = Fabric()
        assert fabric_environment.read(fabric, bad, ENV)["status"] == "couldnt_read" and fabric.calls == []


def test_extras_specifiers_and_odd_entries():
    here = {"requests": {"name": "requests", "version": "2.32.3", "from": "engine"},
            "rich": {"name": "rich", "version": "13.7.1", "from": "engine"}}
    item = fabric_environment.read(Fabric(["not a dict", None,
                                           {"name": "requests[socks]", "libraryType": "External", "version": "2.32.3"},
                                           {"name": "rich", "libraryType": "External", "version": ">=13"}]), WS, ENV)
    out = fabric_environment.drift(item, here, python="3.11")
    assert out["missing_here"] == [] and out["different"] == [] and out["same"] == 2
    item = fabric_environment.read(Fabric([{"name": "rich", "libraryType": "External", "version": ">=14"}]), WS, ENV)
    assert fabric_environment.drift(item, here, python="3.11")["different"][0]["fabric"] == ">=14"


def test_a_runtime_whose_python_isnt_known_says_so():
    out = fabric_environment.drift(fabric_environment.read(Fabric(runtime="9.9"), WS, ENV), {}, python="3.11")
    assert out["python"]["unknown"] is True and out["drifted"] is False
    assert "doesn't know which Python runtime 9.9 runs" in fabric_environment.summary(out)


def test_the_summary_drops_control_characters_from_fabric_names():
    item = fabric_environment.read(Fabric([{"name": "evil\x1b[2J\r\x9bpkg", "libraryType": "External", "version": "1\x07"}]),
                                   WS, ENV)
    text = fabric_environment.summary(fabric_environment.drift(item, {}, python="3.11"))
    assert "Missing here: evil[2Jpkg==1" in text and not any(ord(ch) < 32 and ch != "\n" for ch in text)


def test_an_empty_staging_list_shows_every_published_library_as_removed():
    c, _ = client([Resp(404, {"errorCode": "EnvironmentLibrariesNotFound", "message": "none staged"})])
    assert c.environment_libraries(WS, ENV, staged=True) == []
    published = [{"name": "rich", "libraryType": "External", "version": "13.7.1"},
                 {"name": "tool.jar", "libraryType": "Custom"}]
    staged = fabric_environment.read(Fabric(published, staged=[]), WS, ENV)["staged"]
    assert staged["pending"] and staged["added"] == [] and staged["changed"] == []
    assert staged["removed"] == [{"name": "tool.jar", "version": None}, {"name": "rich", "version": "13.7.1"}]


def test_the_staged_runtime_is_reported_when_the_published_one_wasnt_read():
    class PublishedRuntimeFails(Fabric):
        def environment_spark_compute(self, ws, env, staged=False):
            if not staged:
                raise FabricError("x", None, 500)
            return {"runtimeVersion": "1.3"}

    item = fabric_environment.read(PublishedRuntimeFails([]), WS, ENV)
    assert item["runtime"] is None and item["staged"]["runtime"] == {"published": None, "staged": "1.3"}
    assert item["staged"]["pending"] is False
    text = fabric_environment.summary(fabric_environment.drift(item, {}, python="3.11"))
    assert "Unpublished runtime: 1.3 (the published runtime wasn't read)." in text
