"""The central platform flows of the smoke test (platformtest.py), mutation batch 404: the flow each check
belongs to, what the product step records, a clash with an item of another type, when setup is waiting for a
connection, a platform with no schema version, an exception that was never active, the workspace name handed
to setup, the results folder, and a long delete error. Fabric is faked; the platform runs on SQLite."""

import datetime as dt
from fabric_desktop import platformtest
from tests.test_platformtest import WS, Fabric, by_id, harness


def test_each_check_belongs_to_its_flow(tmp_path):
    test, *_ = harness(tmp_path)
    flows = {c["id"]: c["flow"] for c in test.run()["checks"]}
    assert flows == {"platform.finish_empty": 11, "platform.repair": 12, "platform.product": 13,
                     "platform.keys": 14, "platform.exception": 15, "platform.restrict": 16}


def test_the_product_step_says_what_it_registered(tmp_path):
    test, *_ = harness(tmp_path)
    detail = by_id(test.run())["platform.product"]["detail"]
    assert detail.startswith("Registered fdlt_run1/fdlt_run1_product (version ") and detail.endswith("; it's listed.")


def test_an_upgrade_skips_flow_11_and_says_why(tmp_path):
    test, fabric, platform, setup = harness(tmp_path, upgrade=True)
    old = []
    for item, key in platformtest.ITEMS.items():
        old.append({"id": f"old-{key}", "displayName": item, "type": platformtest.ITEM_TYPES[item]})
    fabric.items = [dict(i) for i in old]
    check = by_id(test.run())["platform.finish_empty"]
    assert check["flow"] == 11 and check["status"] == "skipped"
    assert check["title"] == platformtest.FLOW_TITLES[11]


def test_flows_13_to_15_are_skipped_in_their_own_flows_when_the_platform_isnt_ready(tmp_path):
    test, *_ = harness(tmp_path, connected=False)
    checks = by_id(test.run())
    for flow, id in ((13, "product"), (14, "keys"), (15, "exception")):
        check = checks[f"platform.{id}"]
        assert (check["flow"], check["status"], check["title"]) == (flow, "skipped", platformtest.FLOW_TITLES[flow])


def test_a_lakehouses_own_endpoint_isnt_a_clash_but_an_endpoint_named_like_the_functions_item_is():
    meta = next(n for n, t in platformtest.ITEM_TYPES.items() if t == "Lakehouse")
    functions = next(n for n, t in platformtest.ITEM_TYPES.items() if t == "UserDataFunction")
    found, clashes = platformtest._platform_items([{"id": "e", "displayName": meta, "type": "SQLEndpoint"}])
    assert found == {} and clashes == []
    found, clashes = platformtest._platform_items([{"id": "e", "displayName": functions, "type": "SQLEndpoint"}])
    assert clashes == [f"a SQLEndpoint named {functions}, where the platform's is a UserDataFunction"]
    found, clashes = platformtest._platform_items([{"id": "n", "displayName": meta, "type": "Notebook"}])
    assert clashes == [f"a Notebook named {meta}, where the platform's is a Lakehouse"]


def test_only_a_failed_tables_step_waits_for_the_connection():
    assert platformtest._needs_connection({"steps": [{"step": "Platform tables", "status": "failed"}]})
    assert not platformtest._needs_connection({"steps": [{"step": "LH_Meta", "status": "failed"}]})
    assert not platformtest._needs_connection({"steps": [{"step": "Platform tables", "status": "done"}]})


def test_a_platform_that_gives_no_schema_version_isnt_ready(tmp_path):
    test, _, platform, _ = harness(tmp_path)

    class NoVersion:
        def call(self, name, **kw):
            return {} if name == "ensure_schema" else platform.call(name, **kw)

    test.client_for = lambda ws, fid: NoVersion()
    check = by_id(test.run())["platform.finish_empty"]
    assert check["status"] == "failed" and "The platform's schema is version None" in check["detail"]


def test_an_exception_that_is_listed_only_among_ended_ones_fails_flow_15(tmp_path):
    test, _, platform, _ = harness(tmp_path)

    class NeverActive:
        def call(self, name, **kw):
            if name == "list_policy_exceptions":
                return [] if kw.get("active") else [{"id": self.req}]
            out = platform.call(name, **kw)
            if name == "request":
                self.req = out["id"]
            return out

    client = NeverActive()
    test.client_for = lambda ws, fid: client
    check = by_id(test.run())["platform.exception"]
    assert check["status"] == "failed" and check["detail"].endswith("The approved exception isn't listed as active")


def test_the_exception_lasts_a_week(tmp_path):
    test, *_ = harness(tmp_path)
    until = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    assert f"until {until}:" in by_id(test.run())["platform.exception"]["detail"]


def test_setup_is_given_the_workspaces_name(tmp_path):
    fabric = Fabric(name="KF tenant test")
    seen = []
    test, *_ = harness(tmp_path, fabric)
    inner = test.setup

    def setup(fabric, token, env, name, capacity, existing):
        seen.append(dict(existing))
        return inner(fabric, token, env, name, capacity, existing)

    test.setup = setup
    test.run()
    assert seen[0]["workspace_name"] == "KF tenant test" and seen[0]["workspace_id"] == WS


def test_a_run_makes_its_results_folder_with_its_parents_or_reuses_it(tmp_path):
    class Unreadable(Fabric):
        def get_workspace(self, ws):
            raise RuntimeError("403")

    nested = tmp_path / "a" / "b"
    test, *_ = harness(tmp_path, Unreadable())
    test.dir = nested
    assert test.run()["outcome"] == "refused" and nested.is_dir()
    assert test.run()["outcome"] == "refused"


def test_a_run_without_its_own_id_gets_twelve_hex_characters(tmp_path):
    test = platformtest.PlatformTest(WS, Fabric(), lambda: "tok", tmp_path, client=None, setup=None, user=None)
    assert len(test.run_id) == 12 and int(test.run_id, 16) >= 0


def test_the_plan_says_the_platform_is_kept_only_with_keep(tmp_path):
    test, *_ = harness(tmp_path)
    assert "not deleted" not in test.plan()[-1]
    test.keep = True
    assert ": not deleted, --platform-keep" in test.plan()[-1]


def test_a_long_delete_error_is_cut_to_200_characters(tmp_path):
    class Wordy(Fabric):
        def _delete(self, item_id):
            raise RuntimeError("x" * 300)

    test, *_ = harness(tmp_path, Wordy())
    left = test.run()["left_behind"]
    assert left and all(e["why"] == "x" * 200 for e in left)
