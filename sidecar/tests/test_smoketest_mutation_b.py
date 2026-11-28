"""The tenant smoke test (smoketest.py), mutation batch 403: what each step records, which flow each step
belongs to, when the warehouse check runs and how its findings are counted, empty tokens and a name check
that lets a bad name through, and the summary's notes. Sign-in, Fabric and the live test are faked."""

from fabric_desktop import fabric_api, smoketest
from tests.test_smoketest import WS, Credential, Fabric, Live, Platform, clean_bundle, smoke


class Warehouse:
    def __init__(self, counts=None, cleanup="Dropped fdlt_wh1."):
        self.result = {"run_id": "wh1", "outcome": "passed", "refused": [], "counts": counts or {},
                       "cleanup": cleanup}
        self.out = None

    def __call__(self, out):
        self.out = out
        return self

    def run(self):
        return self.result


def steps(result):
    return {s["id"]: s for s in result["steps"]}


def test_each_step_records_what_it_found(tmp_path):
    got = steps(smoke(tmp_path).run())
    assert got["engine"]["detail"].startswith("KratosForge engine ")
    assert got["auth.client"]["detail"] == "No App ID set: signs in with Microsoft's client."
    for id in ("auth.fabric", "auth.onelake"):
        assert got[id]["detail"] == "Token received (not recorded).", id
    assert got["catalog.workspaces"]["detail"] == "2 workspaces listed, the test workspace among them."
    assert got["catalog.items"]["detail"] == "3 items (Lakehouse, Notebook)."
    assert got["support.bundle"]["detail"].startswith("Checked ") and "no token" in got["support.bundle"]["detail"]
    assert got["live.outcome"]["detail"] == "Live test live1: passed"


def test_a_workspace_with_no_items_says_none(tmp_path):
    test = smoke(tmp_path)
    test.fabric.list_items = lambda ws: []
    assert test._items() == "0 items (none)."


def test_each_step_belongs_to_its_flow(tmp_path):
    got = steps(smoke(tmp_path).run())
    assert {id: got[id]["flow"] for id in ("auth.sql", "auth.kusto", "catalog.items", "lakehouse.bad_name")} == {
        "auth.sql": 2, "auth.kusto": 2, "catalog.items": 3, "lakehouse.bad_name": 4}


def test_a_run_doesnt_run_in_fabric_unless_asked_and_gets_a_twelve_character_id(tmp_path):
    test = smoketest.SmokeTest(WS, Credential(), Fabric(), tmp_path / "out", live_test=Live(),
                               support_bundle=clean_bundle)
    assert test.run_in_fabric is False and len(test.run_id) == 12
    assert test.run()["run_in_fabric"] is False


def test_an_empty_token_fails_its_step(tmp_path):
    class Empty(Credential):
        def storage_token(self):
            return ""

    got = steps(smoke(tmp_path, credential=Empty()).run())
    assert got["auth.onelake"]["status"] == "failed"
    assert got["auth.onelake"]["detail"] == "RuntimeError: Sign-in returned an empty token"


def test_a_name_check_that_accepts_the_bad_name_fails_flow_4(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_api, "lakehouse_name_problem", lambda name: "")
    got = steps(smoke(tmp_path).run())
    assert got["lakehouse.bad_name"]["status"] == "failed"


def test_the_warehouse_check_runs_only_when_asked_for_and_fabric_signed_in(tmp_path):
    assert "warehouse.check" not in steps(smoke(tmp_path / "none").run())

    class NoFabric(Credential):
        def fabric_token(self):
            return ""

    test = smoke(tmp_path / "nofabric", credential=NoFabric())
    test.warehouse_check = Warehouse()
    assert "warehouse.check" not in steps(test.run())
    assert test.warehouse_check.out is None


def test_the_warehouse_step_records_the_cleanup_and_its_findings(tmp_path):
    test = smoke(tmp_path / "clean")
    test.warehouse_check = Warehouse(counts={"as documented": 3})
    got = steps(test.run())
    assert got["warehouse.check"]["detail"] == "Dropped fdlt_wh1."
    assert got["warehouse.findings"]["status"] == "passed"
    for counts in ({"docs are wrong": 1, "local differs": 1}, {"docs are wrong": 1}, {"local differs": 2}):
        test = smoke(tmp_path / str(len(counts)) / str(sum(counts.values())))
        test.warehouse_check = Warehouse(counts=counts)
        assert steps(test.run())["warehouse.findings"]["status"] == "failed", counts


def test_the_platform_outcome_belongs_to_flow_11(tmp_path):
    test = smoke(tmp_path)
    test.platform_test = Platform()
    assert steps(test.run())["platform.outcome"]["flow"] == 11


def test_the_plan_names_the_platform_flows_only_when_asked_for(tmp_path):
    test = smoke(tmp_path)
    assert not any(line.startswith("Flows 11-16") for line in test.plan())
    test.platform_test = Platform()
    assert any(line.startswith("Flows 11-16") for line in test.plan())


def _summary(tmp_path, **result):
    base = smoke(tmp_path).run()
    base.update(result)
    return smoketest.summary(base)


def test_the_summary_notes_only_steps_that_dont_fail_their_flow(tmp_path):
    text = _summary(tmp_path)
    note = " (recorded, doesn't fail the flow)"
    lines = [line for line in text.splitlines() if note in line]
    assert lines and all("Token for Azure SQL" in line or "Token for Kusto" in line for line in lines)


def test_the_summary_mentions_what_was_left_behind_only_when_something_was(tmp_path):
    assert "Left behind" not in _summary(tmp_path / "a", live_test=None)
    assert "Left behind" not in _summary(tmp_path / "b", live_test={"left_behind": []})
    assert "**Left behind** by the live test" in _summary(tmp_path / "c", live_test={"left_behind": [{"id": "x"}]})
    left = [{"id": "sql"}]
    assert "**Left behind** by the platform flows" in _summary(
        tmp_path / "d", platform_test={"left_behind": left, "kept": False})
    assert "**Kept** by the platform flows" in _summary(tmp_path / "e", platform_test={"left_behind": left, "kept": True})


def test_the_summary_names_the_workspace_and_says_whether_it_ran_in_fabric(tmp_path):
    named = _summary(tmp_path / "a", workspace={"id": WS, "name": "Live tests"}, run_in_fabric=False)
    assert "workspace Live tests, started" in named and ", without Run in Fabric." in named
    bare = _summary(tmp_path / "b", workspace={"id": WS, "name": ""}, run_in_fabric=True)
    assert f"workspace {WS}, started" in bare and ", with Run in Fabric." in bare
