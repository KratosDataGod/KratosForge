"""A platform repository: definitions exported from the platform, and compared with each environment (item 37, stage 3)."""

from __future__ import annotations

import sqlite3
import tomllib

import pytest

from fabric_desktop.git import platform_files
from fabric_desktop.git.cli import GitError
from fabric_desktop.service import Service

from test_git import git_home  # noqa: F401  (an autouse fixture)
from test_governance import NAMING


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.platform_save_domain({"name": "Sales", "owners": "omar@contoso.com; ana@contoso.com", "description": "Selling"})
    s.platform_save_domain({"name": "EMEA", "parent_id": "sales"})
    s.platform_save_source({"code": "SAP", "title": "SAP ECC", "system_type": "sap", "expected_hours": "24",
                            "connections": {"prod": {"server_host": "sap.contoso.com", "secret_name": "sap-pw"}}})
    s.platform_create_key_table("customer", business_key="customer_code", description="Customers", domain_id="sales")
    s.platform_save_term({"term": "Active customer", "definition": "Bought in the last year", "domain_id": "sales"})
    s.platform_add_admin("ana@contoso.com")
    s.governance_create(NAMING)
    s.governance_publish("table-names")
    s.governance_assign("table-names", "domain", "sales", {"enforcement": "block"})
    s.governance_create(NAMING | {"id": "draft-only", "name": "Never published"})
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


@pytest.fixture
def repo(svc, tmp_path):
    svc.git_create(str(tmp_path / "platform"), "platform")
    return str((tmp_path / "platform").resolve())


def commit(svc, repo, message="Platform definitions"):
    svc.git_stage_all(repo)
    svc.git_commit(repo, message)


def read(tmp_path, rel):
    return tomllib.loads((tmp_path / "platform" / rel).read_text(encoding="utf-8"))


def test_export_writes_one_file_per_object_with_content_only(svc, repo, tmp_path):
    out = svc.git_platform_export(repo)
    assert "domains/sales.toml" in out["written"] and "policies/table-names.toml" in out["written"]
    assert out["skipped"] == [{"kind": "policies", "id": "draft-only", "why": "never published"}]
    assert read(tmp_path, "domains/sales.toml") == {"id": "sales", "name": "Sales", "parent": "", "description": "Selling",
                                                    "owners": ["ana@contoso.com", "omar@contoso.com"]}
    assert read(tmp_path, "domains/emea.toml")["parent"] == "sales"
    sap = read(tmp_path, "sources/sap.toml")
    assert sap == {"source_id": 1, "code": "SAP", "title": "SAP ECC", "description": "", "owner": "", "system_type": "sap",
                   "active": True, "expected_hours": "24"}
    assert read(tmp_path, "key_tables/customer.toml")["business_key"] == "customer_code"
    assert read(tmp_path, "glossary.toml")["term"][0]["term"] == "Active customer"
    assert read(tmp_path, "admins.toml") == {"admins": ["ana@contoso.com"]}
    assert read(tmp_path, "shortcut_rules.toml")["cross_env"] == "block"
    pol = read(tmp_path, "policies/table-names.toml")
    assert pol["definition"]["params"]["max_length"]["value"] == 60
    assert pol["assign"] == [{"scope": "domain", "target": "sales", "note": "", "settings": {"enforcement": "block"}}]
    everything = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "platform").rglob("*.toml"))
    for leak in ("sap.contoso.com", "sap-pw", "updated_at", "created_by", "hash", "key_count"):
        assert leak not in everything
    # Nothing committed: the files are changes to review.
    assert any(e["path"] == "domains/sales.toml" for e in out["status"]["untracked"])


def test_a_second_export_writes_nothing_and_the_diff_is_clean(svc, repo):
    svc.git_platform_export(repo)
    commit(svc, repo)
    again = svc.git_platform_export(repo)
    assert again["written"] == [] and again["unchanged"] > 0
    diff = svc.git_platform_diff(repo, "prod")
    assert diff["counts"] == {"same": len(diff["items"]), "changed": 0, "only_git": 0, "only_deployed": 0}
    assert diff["problems"] == []


def test_the_diff_shows_each_change_and_never_writes(svc, repo, tmp_path):
    svc.git_platform_export(repo)
    commit(svc, repo)
    root = tmp_path / "platform"
    text = (root / "domains/sales.toml").read_text(encoding="utf-8").replace('"Selling"', '"Selling things"')
    (root / "domains/sales.toml").write_text(text, encoding="utf-8")
    (root / "domains/marketing.toml").write_text('id = "marketing"\nname = "Marketing"\n', encoding="utf-8")
    (root / "sources/sap.toml").unlink()
    pol = (root / "policies/table-names.toml").read_text(encoding="utf-8").replace('"block"', '"warn"')
    (root / "policies/table-names.toml").write_text(pol, encoding="utf-8")
    before = svc.platform_domains()
    diff = svc.git_platform_diff(repo, "prod")
    by = {(i["kind"], i["id"]): i for i in diff["items"]}
    assert by[("domains", "sales")]["changes"] == [{"field": "description", "git": "Selling things", "deployed": "Selling"}]
    assert by[("domains", "marketing")]["state"] == "only_git"
    assert by[("sources", "sap")]["state"] == "only_deployed"
    assert by[("policies", "table-names")]["changes"][0]["field"] == "assign"
    assert svc.platform_domains() == before
    assert svc.git_status(repo)["dirty"]  # only the hand edits


def test_export_never_replaces_uncommitted_work_without_confirm(svc, repo, tmp_path):
    svc.git_platform_export(repo)
    commit(svc, repo)
    f = tmp_path / "platform" / "domains" / "sales.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('"Selling"', '"My edit"'), encoding="utf-8")
    (tmp_path / "platform" / "domains" / "marketing.toml").write_text('id = "marketing"\nname = "Marketing"\n',
                                                                      encoding="utf-8")
    out = svc.git_platform_export(repo)
    assert out["needs_confirm"] and out["changes"] == ["domains/sales.toml"]
    assert "My edit" in f.read_text(encoding="utf-8")
    done = svc.git_platform_export(repo, confirm=True)
    assert done["replaced_uncommitted"] == ["domains/sales.toml"] and "My edit" not in f.read_text(encoding="utf-8")
    # A file for something not deployed is left alone, never deleted.
    assert done["left"] == [{"kind": "domains", "id": "marketing"}]
    assert (tmp_path / "platform" / "domains" / "marketing.toml").exists()


def test_only_a_platform_repository_is_exported_into(svc, tmp_path):
    svc.git_create(str(tmp_path / "nbs"), "notebooks")
    with pytest.raises(GitError):
        svc.git_platform_export(str(tmp_path / "nbs"))
    with pytest.raises(GitError):
        svc.git_platform_diff(str(tmp_path / "nbs"))


def test_a_folder_that_is_a_link_is_never_written_through(svc, repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "platform" / "domains" / ".gitkeep").unlink()
    (tmp_path / "platform" / "domains").rmdir()
    try:
        (tmp_path / "platform" / "domains").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this system can't make symbolic links")
    with pytest.raises(GitError, match="isn't a plain folder"):
        svc.git_platform_export(repo)
    assert list(outside.iterdir()) == []


def test_export_skips_policies_on_a_platform_without_policy_tables(tmp_path):
    s = Service(root=tmp_path / "app")
    s.platform_save_domain({"name": "Sales"})
    db = tmp_path / "app" / "platform" / "platform-prod.sqlite"
    con = sqlite3.connect(db)
    for t in ("policy", "policy_version", "policy_assignment", "policy_event"):
        con.execute(f"DROP TABLE IF EXISTS catalog__{t}")
    con.commit()
    con.close()
    s.git_create(str(tmp_path / "platform"), "platform")
    out = s.git_platform_export(str(tmp_path / "platform"))
    assert out["policies_set_up"] is False
    assert not any(p.startswith("policies/") and p.endswith(".toml") for p in out["written"])
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    assert "catalog__policy" not in names  # export looked, it didn't create them


def test_key_tables_that_differ_between_environments_are_listed(tmp_path):
    defs = platform_files.from_platform({"key_tables": {
        "prod": [{"name": "customer", "business_key": "customer_code"}],
        "dev": [{"name": "customer", "business_key": "cust_no"}, {"name": "product", "business_key": "sku"}]}})
    assert defs["key_tables"]["customer"]["business_key"] == "customer_code"
    assert set(defs["key_tables"]) == {"customer", "product"}
    assert defs["disagree"] == [{"kind": "key_tables", "id": "customer", "env": "dev", "kept_from": "prod",
                                 "changes": [{"field": "business_key", "git": "cust_no", "deployed": "customer_code"}]}]


def test_before_keys_move_export_takes_key_tables_from_every_environment(svc, repo, tmp_path):
    svc._platform("dev").call("ensure_key_table", name="devonly", business_key="x")
    svc.git_platform_export(repo)
    assert read(tmp_path, "key_tables/devonly.toml")["business_key"] == "x"


def test_key_tables_export_from_the_shared_keys_database(svc, repo, tmp_path):
    assert svc.platform_share_keys(approve=True)["ok"]
    svc.platform_create_key_table("product", business_key="sku")  # only in the shared database
    out = svc.git_platform_export(repo)
    assert out["disagree"] == []
    assert read(tmp_path, "key_tables/product.toml")["business_key"] == "sku"
    for env in ("dev", "test", "prod"):
        assert svc.git_platform_diff(repo, env)["counts"]["changed"] == 0


def test_files_round_trip_and_odd_text_is_quoted():
    rec = {"id": "x", "text": 'quote " back \\ tab\t del\x7f é', "n": 3, "f": 0.5, "ok": False, "list": ["a", "b"],
           "table": {"inner key": {"deep": [1, 2]}, "empty": {}}, "rows": [{"a": 1}, {"a": 2, "sub": {"b": "c"}}]}
    assert tomllib.loads(platform_files.dumps(rec, "header\nline")) == rec


def test_files_that_dont_match_their_name_are_reported_not_used(tmp_path):
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "sales.toml").write_text('id = "finance"\nname = "Finance"\n', encoding="utf-8")
    (tmp_path / "domains" / "bad.toml").write_text("id = [\n", encoding="utf-8")
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "p.toml").write_text('id = "p"\n[definition]\nname = "P"\ncategory = "nope"\n', encoding="utf-8")
    got = platform_files.read_repo(tmp_path)
    assert got["domains"] == {} and got["policies"] == {}
    assert len(got["problems"]) == 3


# --------------------------------------------------------------------------- deploy


@pytest.fixture
def exported(svc, repo):
    svc.git_platform_export(repo)
    commit(svc, repo)
    return repo


@pytest.fixture
def other(tmp_path, exported, svc):
    """A second PC's app with an empty platform, that opened the same repository."""
    s = Service(root=tmp_path / "other")
    s.git_open(exported)
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


def deploy(s, repo, remove=None):
    preview = s.git_platform_deploy(repo, remove=remove)
    assert preview["done"] == [] and not preview.get("deployed")
    return preview, s.git_platform_deploy(repo, remove=remove, confirm=preview["plan_id"])


def test_deploy_shows_the_plan_and_runs_only_that_plan(other, exported):
    preview = other.git_platform_deploy(exported)
    assert preview["needs_confirm"] and preview["stops"] == []
    ops = [s["op"] for s in preview["steps"]]
    assert ops.index("save_domain") < ops.index("adopt_sources") < ops.index("ensure_key_table") < ops.index("create_policy")
    assert other.platform_domains() == []  # the preview changed nothing
    stale = other.git_platform_deploy(exported, confirm="not-the-plan")
    assert stale["done"] == [] and other.platform_domains() == []
    done = other.git_platform_deploy(exported, confirm=preview["plan_id"])
    assert done["deployed"] and not done.get("failed")
    for env in ("dev", "test", "prod"):
        assert other.git_platform_diff(exported, env)["counts"]["changed"] == 0
    diff = other.git_platform_diff(exported, "prod")
    assert diff["counts"]["only_git"] == diff["counts"]["only_deployed"] == 0
    assert [t["name"] for t in other.platform_key_tables("test")["key_tables"]] == ["customer"]
    pol = other.governance_policy("table-names")
    assert pol["current_version"] == 1 and pol["history"][-1]["by_user"] == ""
    assert "Deployed from the platform repository at commit" in pol["current"]["note"]
    # A second deploy has nothing to do.
    assert other.git_platform_deploy(exported)["steps"] == []


def test_deploy_makes_key_tables_once_in_the_shared_keys_database(other, exported):
    assert other.platform_share_keys(approve=True)["ok"]
    preview, done = deploy(other, exported)
    keys = [s for s in preview["steps"] if s["op"] == "ensure_key_table"]
    assert [(s["env"], s["id"], s["what"]) for s in keys] == [("keys", "customer", "create in the shared keys database")]
    assert any(s["op"] == "add_admin" and s["env"] == "keys" for s in preview["steps"])
    assert done["deployed"] and not done.get("failed")
    assert [t["name"] for t in other._shared_keys().call("list_key_tables")] == ["customer"]
    assert other._platform("dev").call("list_key_tables") == []  # not in each environment's own
    assert "ana@contoso.com" in {a["user_name"] for a in other._shared_keys().call("list_admins")}
    assert other.git_platform_deploy(exported)["steps"] == []


def test_a_changed_policy_publishes_a_new_version_and_never_edits_one(svc, exported, tmp_path):
    f = tmp_path / "platform" / "policies" / "table-names.toml"
    f.write_text(f.read_text(encoding="utf-8").replace("value = 60", "value = 50"), encoding="utf-8")
    commit(svc, exported, "Shorter table names")
    _, done = deploy(svc, exported)
    assert [s["op"] for s in done["done"]] == ["save_policy_draft", "publish_policy"]
    pol = svc.governance_policy("table-names")
    assert pol["current_version"] == 2 and all(v["intact"] for v in pol["versions"] if v["status"] == "published")
    assert pol["versions"][0]["definition"]["params"]["max_length"]["value"] == 60
    # A draft someone is working on is never overwritten.
    svc.governance_save_draft("table-names", pol["current"]["definition"] | {"description": "Mine"})
    f.write_text(f.read_text(encoding="utf-8").replace("value = 50", "value = 40"), encoding="utf-8")
    commit(svc, exported, "Even shorter")
    preview = svc.git_platform_deploy(exported)
    assert preview["blocked"][0]["id"] == "table-names" and "draft in progress" in preview["blocked"][0]["why"]
    assert not any(s["op"] == "save_policy_draft" for s in preview["steps"])


def test_a_source_id_is_never_renumbered(other, exported, svc, tmp_path):
    other.platform_save_source({"code": "CRM", "title": "CRM"})  # takes id 1 in this platform
    preview = other.git_platform_deploy(exported)
    assert preview["stops"] and "id 1 is already crm" in preview["stops"][0]
    assert other.git_platform_deploy(exported, confirm=preview["plan_id"])["done"] == []
    f = tmp_path / "platform" / "sources" / "sap.toml"
    f.write_text(f.read_text(encoding="utf-8").replace("source_id = 1", "source_id = 7"), encoding="utf-8")
    commit(svc, exported, "SAP is 7")
    _, done = deploy(other, exported)
    assert done["deployed"]
    assert {s["code"]: s["source_id"] for s in other.platform_sources()} == {"CRM": 1, "SAP": 7}
    # The original platform has SAP as 1: the deploy stops, nothing is written.
    preview = svc.git_platform_deploy(exported)
    assert any("never renumbered" in x for x in preview["stops"])


def test_a_deploy_keeps_a_sources_connections(svc, exported, tmp_path):
    f = tmp_path / "platform" / "sources" / "sap.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('"SAP ECC"', '"SAP S/4"'), encoding="utf-8")
    commit(svc, exported, "Rename SAP")
    deploy(svc, exported)
    sap = svc.platform_sources()[0]
    assert sap["title"] == "SAP S/4" and sap["connections"]["prod"]["server_host"] == "sap.contoso.com"


def test_removing_needs_a_tick_and_goes_children_first(svc, exported, tmp_path):
    for name in ("emea", "sales"):
        (tmp_path / "platform" / "domains" / f"{name}.toml").unlink()
    (tmp_path / "platform" / "policies" / "table-names.toml").unlink()
    commit(svc, exported, "Remove sales")
    preview = svc.git_platform_deploy(exported)
    assert {(x["kind"], x["id"]) for x in preview["removable"]} >= {("domains", "sales"), ("domains", "emea"),
                                                                    ("policies", "table-names")}
    assert not any(s["action"] == "remove" for s in preview["steps"])
    ticked = [{"kind": "domains", "id": "sales"}, {"kind": "domains", "id": "emea"}, {"kind": "policies", "id": "table-names"}]
    _, done = deploy(svc, exported, remove=ticked)
    removed = [(s["op"], s["id"]) for s in done["done"] if s["action"] == "remove"]
    assert removed == [("remove_assignment", "table-names"), ("set_policy_status", "table-names"),
                       ("delete_domain", "emea"), ("delete_domain", "sales")]
    assert svc.platform_domains() == [] and svc.governance_policy("table-names")["status"] == "retired"


def test_deploy_needs_committed_files(svc, exported, tmp_path):
    (tmp_path / "platform" / "domains" / "finance.toml").write_text('id = "finance"\nname = "Finance"\n', encoding="utf-8")
    with pytest.raises(GitError, match="Commit the changes"):
        svc.git_platform_deploy(exported)


def test_a_refused_step_stops_the_deploy_and_says_where(svc, exported, tmp_path):
    (tmp_path / "platform" / "domains" / "west.toml").write_text('id = "west"\nname = "West"\nparent = "nowhere"\n',
                                                                encoding="utf-8")
    (tmp_path / "platform" / "domains" / "finance.toml").write_text('id = "finance"\nname = "Finance"\n', encoding="utf-8")
    commit(svc, exported, "Two domains")
    _, out = deploy(svc, exported)
    assert out["failed"]["id"] == "west" and "nowhere" in out["message"]
    assert [s["id"] for s in out["done"]] == ["finance"]


def test_platform_functions_older_than_the_app_are_explained(svc, repo, monkeypatch):
    cat = svc._catalog()
    real = cat.call

    def old_functions(op, **kw):
        if op == "policies_ready":
            raise LookupError("not found")
        return real(op, **kw)

    monkeypatch.setattr(cat, "call", old_functions)
    with pytest.raises(GitError, match="Finish or repair"):
        svc.git_platform_diff(repo)


def test_a_source_id_taken_meanwhile_stops_before_the_source_is_saved(other, exported, monkeypatch):
    preview = other.git_platform_deploy(exported)
    other.platform_save_source({"code": "CRM", "title": "CRM"})  # takes id 1 after the plan was worked out
    monkeypatch.setattr(platform_files, "plan", lambda *a, **k: preview)  # as if nothing had changed
    out = other.git_platform_deploy(exported, confirm=preview["plan_id"])
    assert out["failed"]["op"] == "adopt_sources" and "already" in out["message"]
    assert {s["code"] for s in other.platform_sources()} == {"CRM"}


def test_domains_are_created_parents_first_and_removed_deepest_first_at_any_depth():
    deployed = platform_files.from_platform({"domains": [
        {"id": "a", "name": "A"}, {"id": "b", "name": "B", "parent_id": "a"}, {"id": "c", "name": "C", "parent_id": "b"}],
        "key_tables": {"prod": []}})
    git = {"domains": {"z": platform_files.domain_record({"id": "z", "name": "Z", "parent": "y"}),
                       "y": platform_files.domain_record({"id": "y", "name": "Y", "parent": "x"}),
                       "x": platform_files.domain_record({"id": "x", "name": "X"})}, "problems": []}
    out = platform_files.plan(git, {"prod": deployed}, "prod", {("domains", d) for d in "abc"}, "note")
    order = [(s["op"], s["id"]) for s in out["steps"]]
    assert order == [("save_domain", "x"), ("save_domain", "y"),
                     ("delete_domain", "c"), ("delete_domain", "b"), ("delete_domain", "a")]
    # The platform keeps subdomains one level deep: a third level is caught in the plan, not partway through.
    assert [b["id"] for b in out["blocked"]] == ["z"] and "one level deep" in out["blocked"][0]["why"]


def test_in_use_removals_are_caught_while_planning(svc, exported, tmp_path):
    svc._catalog().call("register_project", project={"name": "crm", "domain": "sales"})
    for name in ("emea", "sales"):
        (tmp_path / "platform" / "domains" / f"{name}.toml").unlink()
    (tmp_path / "platform" / "admins.toml").write_text("admins = []\n", encoding="utf-8")
    commit(svc, exported, "Remove sales and every admin")
    preview = svc.git_platform_deploy(exported, remove=[{"kind": "domains", "id": "emea"}])
    assert [s["id"] for s in preview["steps"] if s["action"] == "remove"] == ["emea"]
    preview = svc.git_platform_deploy(exported, remove=[{"kind": "domains", "id": "sales"}])
    assert not any(s["action"] == "remove" for s in preview["steps"])
    assert "subdomain emea, project crm" in next(b for b in preview["blocked"] if b["id"] == "sales")["why"]
    preview = svc.git_platform_deploy(exported, remove=[{"kind": "admins", "id": "ana@contoso.com"}])
    assert any("no platform admin" in x for x in preview["stops"])


def test_a_source_still_read_is_kept_in_the_plan():
    deployed = platform_files.from_platform({"sources": [{"name": "sap", "code": "SAP", "source_id": 1,
                                                          "readers": [{"project": "crm", "entities": []}]}],
                                             "key_tables": {"prod": []}})
    out = platform_files.plan({"sources": {}, "problems": []}, {"prod": deployed}, "prod", {("sources", "sap")}, "note")
    assert out["steps"] == [] and "still read by crm" in out["blocked"][0]["why"]


def test_a_file_git_ignores_is_never_deployed(svc, exported, tmp_path):
    root = tmp_path / "platform"
    (root / ".gitignore").write_text((root / ".gitignore").read_text(encoding="utf-8") + "domains/secret.toml\n",
                                     encoding="utf-8")
    commit(svc, exported, "Ignore one file")
    (root / "domains" / "secret.toml").write_text('id = "secret"\nname = "Secret"\n', encoding="utf-8")
    with pytest.raises(GitError, match="domains/secret.toml"):
        svc.git_platform_deploy(exported)
    assert "secret" not in {d["id"] for d in svc.platform_domains()}


def test_deploy_reads_the_commit_not_a_hidden_local_edit(svc, exported, tmp_path):
    from test_git import raw
    root = tmp_path / "platform"
    raw(root, "update-index", "--assume-unchanged", "domains/sales.toml")
    f = root / "domains" / "sales.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('"Selling"', '"Hidden edit"'), encoding="utf-8")
    assert not svc.git_status(exported)["dirty"]  # Git doesn't show it
    preview = svc.git_platform_deploy(exported)
    assert preview["steps"] == []  # the commit still says "Selling", as the platform does


def test_a_domain_under_a_skipped_parent_is_skipped_too():
    git = {"domains": {"west": platform_files.domain_record({"id": "west", "name": "West", "parent": "fab"}),
                       "fab": platform_files.domain_record({"id": "fab", "fabric_domain_id": "f-1"})}, "problems": []}
    deployed = platform_files.from_platform({"key_tables": {"prod": []}})
    out = platform_files.plan(git, {"prod": deployed}, "prod", set(), "note")
    assert out["steps"] == []
    assert [(b["id"], b["why"].split(".")[0]) for b in out["blocked"]] == [
        ("fab", "comes from Fabric"), ("west", "is under fab, which is skipped, so it is too")]


def test_a_parent_whose_child_stays_is_not_removed():
    deployed = platform_files.from_platform({"domains": [{"id": "a", "name": "A"}, {"id": "b", "name": "B", "parent_id": "a"}],
                                             "projects": [{"name": "crm", "domain_id": "b"}], "key_tables": {"prod": []}})
    out = platform_files.plan({"domains": {}, "problems": []}, {"prod": deployed}, "prod",
                              {("domains", "a"), ("domains", "b")}, "note")
    assert out["steps"] == []
    assert {b["id"]: b["why"] for b in out["blocked"]} == {"b": "is still used by project crm, so it stays.",
                                                          "a": "is still used by subdomain b, so it stays."}


# --------------------------------------------------------------------------- landing zones and policy templates


@pytest.fixture
def fabric(monkeypatch):
    from test_landing_zones import FakeFabric
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    return fake


TEMPLATE = NAMING | {"id": "short-names", "name": "Short table names", "description": "Up to 30 characters."}


@pytest.fixture
def zoned(svc, fabric):
    """The platform with the landing workspace, a secure zone with a group and a person as readers, a
    source in it, reads switched, and one template of the organization's own."""
    assert svc.platform_landing_setup()["ready"]
    svc.platform_save_domain({"name": "HR"})
    svc.platform_create_landing_zone({"name": "HR-Secure", "domain_id": "hr", "secure": True,
                                      "readers": ["HR Secure", "pat@contoso.com"], "label": "Highly Confidential"})
    svc.platform_save_source({"code": "WORKDAY", "title": "Workday", "zone": "hr_secure"})
    svc.platform_switch_landing_reads()
    svc.governance_template_save(TEMPLATE)
    return svc


def test_export_writes_zones_and_templates_with_content_only(zoned, repo, tmp_path):
    out = zoned.git_platform_export(repo)
    assert {"landing_zones/shared.toml", "landing_zones/hr_secure.toml",
            "policy_templates/short-names.toml"} <= set(out["written"])
    assert read(tmp_path, "landing_zones/hr_secure.toml") == {
        "id": "hr_secure", "name": "HR-Secure", "domain": "hr", "secure": True, "readers": ["HR Secure"],
        "label": "Highly Confidential", "devtest": "none"}
    assert read(tmp_path, "landing_zones/shared.toml") == {"id": "shared", "name": "Shared", "domain": "", "secure": False,
                                                           "readers": [], "label": "", "devtest": "read"}
    tpl = read(tmp_path, "policy_templates/short-names.toml")
    assert (tpl["id"], tpl["name"], tpl["category"], tpl["description"]) == (
        "short-names", "Short table names", "naming", "Up to 30 characters.")
    assert tpl["definition"]["params"]["max_length"]["value"] == 60 and "name" not in tpl["definition"]
    # Only the organization's own templates: the built-in ones ship with the app.
    assert sorted(p.name for p in (tmp_path / "platform" / "policy_templates").glob("*.toml")) == ["short-names.toml"]
    # A source's zone is in its file; a source in Shared has none.
    assert read(tmp_path, "sources/workday.toml")["zone"] == "hr_secure"
    assert "zone" not in read(tmp_path, "sources/sap.toml")
    written = "".join(p.read_text(encoding="utf-8") for d in ("landing_zones", "policy_templates")
                      for p in (tmp_path / "platform" / d).glob("*.toml"))
    for leak in ("pat@contoso.com", "lh-", "ws-landing", "lakehouse", "workspace", "switched", "updated", "created",
                 "builtin", "can_edit", "table-names", "product-owner"):
        assert leak not in written, leak


def test_zones_and_templates_round_trip_and_diff(zoned, repo, tmp_path):
    zoned.git_platform_export(repo)
    commit(zoned, repo)
    assert zoned.git_platform_export(repo)["written"] == []
    diff = zoned.git_platform_diff(repo, "prod")
    assert diff["counts"]["changed"] == diff["counts"]["only_git"] == diff["counts"]["only_deployed"] == 0
    assert {("landing_zones", "hr_secure"), ("landing_zones", "shared"), ("policy_templates", "short-names")} <= {
        (i["kind"], i["id"]) for i in diff["items"]}
    assert diff["labels"]["landing_zones"] == "Landing zone" and diff["labels"]["policy_templates"] == "Policy template"
    root = tmp_path / "platform"
    f = root / "landing_zones" / "hr_secure.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('"Highly Confidential"', '"Confidential"'), encoding="utf-8")
    t = root / "policy_templates" / "short-names.toml"
    t.write_text(t.read_text(encoding="utf-8").replace('"Up to 30 characters."', '"Up to 20."'), encoding="utf-8")
    w = root / "sources" / "workday.toml"
    w.write_text(w.read_text(encoding="utf-8").replace('zone = "hr_secure"\n', ""), encoding="utf-8")
    by = {(i["kind"], i["id"]): i for i in zoned.git_platform_diff(repo, "prod")["items"]}
    assert by[("landing_zones", "hr_secure")]["changes"] == [{"field": "label", "git": "Confidential",
                                                              "deployed": "Highly Confidential"}]
    assert by[("policy_templates", "short-names")]["changes"] == [{"field": "description", "git": "Up to 20.",
                                                                   "deployed": "Up to 30 characters."}]
    assert by[("sources", "workday")]["changes"] == [{"field": "zone", "git": None, "deployed": "hr_secure"}]


def test_deploy_creates_a_missing_zone_template_and_a_sources_zone(zoned, repo, tmp_path, fabric):
    zoned.git_platform_export(repo)
    commit(zoned, repo)
    other = Service(root=tmp_path / "other")
    other.git_open(repo)
    assert other.platform_landing_setup()["ready"]  # the other platform has its landing workspace, nothing else
    preview, done = deploy(other, repo)
    ops = [(s["op"], s["id"]) for s in preview["steps"]]
    assert ops.index(("save_domain", "hr")) < ops.index(("platform_create_landing_zone", "hr_secure")) \
        < ops.index(("save_source", "workday"))
    assert ops.index(("save_policy_template", "short-names")) < ops.index(("create_policy", "table-names"))
    assert ("platform_create_landing_zone", "shared") not in ops  # it was there already
    assert done["deployed"] and not done.get("failed"), done.get("message")
    zone = next(z for z in other._catalog().call("list_landing_zones") if z["id"] == "hr_secure")
    assert (zone["lakehouse_name"], zone["lakehouse_id"], zone["secure"], zone["readers"], zone["label"]) == (
        "LH_Landing_HR_Secure", "lh-LH_Landing_HR_Secure", True, ["HR Secure"], "Highly Confidential")
    assert ("lakehouse", "LH_Landing_HR_Secure", "ws-landing") in fabric.created
    assert {s["code"]: s["zone"] for s in other.platform_sources()} == {"SAP": "", "WORKDAY": "hr_secure"}
    mine = [t for t in other.governance_templates()["templates"] if not t["builtin"]]
    assert [(t["id"], t["name"]) for t in mine] == [("short-names", "Short table names")]
    audit = {(a["action"], a["target"]) for a in other._catalog().call("audit", limit=200)}
    assert {("save landing zone", "hr_secure"), ("policy template created", "short-names")} <= audit
    diff = other.git_platform_diff(repo, "prod")
    assert diff["counts"]["changed"] == diff["counts"]["only_git"] == 0
    assert other.git_platform_deploy(repo)["steps"] == []
    # Reads stay as they were: switching them is a manual step on the Platform page.
    shared = next(z for z in other._catalog().call("list_landing_zones") if z["id"] == "shared")
    assert shared["switched_at"] == "" and not shared["read"]
    other.kernel.shutdown() if hasattr(other.kernel, "shutdown") else None


def test_deploy_never_deletes_a_zone_or_template_and_never_switches_reads(zoned, repo, tmp_path):
    zoned.git_platform_export(repo)
    root = tmp_path / "platform"
    (root / "landing_zones" / "hr_secure.toml").unlink()
    (root / "policy_templates" / "short-names.toml").unlink()
    f = root / "landing_zones" / "shared.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('label = ""', 'label = "General"'), encoding="utf-8")
    commit(zoned, repo, "No HR zone, no template")
    by = {(i["kind"], i["id"]): i["state"] for i in zoned.git_platform_diff(repo, "prod")["items"]}
    assert by[("landing_zones", "hr_secure")] == by[("policy_templates", "short-names")] == "only_deployed"
    ticked = [{"kind": "landing_zones", "id": "hr_secure"}, {"kind": "policy_templates", "id": "short-names"}]
    preview, done = deploy(zoned, repo, remove=ticked)
    assert not any(r["kind"] in ("landing_zones", "policy_templates") for r in preview["removable"])
    assert any("Landing zone hr_secure" in n and "never removes" in n for n in preview["notes"])
    assert any("Policy template short-names" in n and "never removes" in n for n in preview["notes"])
    ops = {s["op"] for s in preview["steps"]}
    assert ops == {"save_landing_zone"}  # only the Shared zone's label
    assert done["deployed"]
    zones = {z["id"]: z for z in zoned._catalog().call("list_landing_zones")}
    assert set(zones) == {"shared", "hr_secure"} and zones["shared"]["label"] == "General"
    assert zones["shared"]["switched_at"]  # switched by hand before, and left as it was
    assert any(t["id"] == "short-names" for t in zoned.governance_templates()["templates"])


def test_a_zone_update_keeps_the_people_the_file_leaves_out(zoned, repo, tmp_path):
    zoned.git_platform_export(repo)
    f = tmp_path / "platform" / "landing_zones" / "hr_secure.toml"
    f.write_text(f.read_text(encoding="utf-8").replace('devtest = "none"', 'devtest = "read"'), encoding="utf-8")
    commit(zoned, repo, "Dev and Test read HR")
    _, done = deploy(zoned, repo)
    assert [(s["op"], s["id"]) for s in done["done"]] == [("save_landing_zone", "hr_secure")]
    zone = next(z for z in zoned._catalog().call("list_landing_zones") if z["id"] == "hr_secure")
    assert zone["devtest"] == "read" and zone["readers"] == ["HR Secure", "pat@contoso.com"]
    assert zone["lakehouse_id"] == "lh-LH_Landing_HR_Secure"


def test_a_persons_email_as_a_zone_reader_is_a_problem_in_a_file(tmp_path):
    (tmp_path / "landing_zones").mkdir()
    (tmp_path / "landing_zones" / "hr.toml").write_text('id = "hr"\nname = "HR"\nreaders = ["pat@contoso.com"]\n',
                                                        encoding="utf-8")
    got = platform_files.read_repo(tmp_path)
    assert got["landing_zones"] == {} and "A person's email" in got["problems"][0]


def test_zones_wait_for_the_landing_workspace_and_a_builtin_template_id_is_refused():
    git = {"landing_zones": {"shared": platform_files.zone_record({"id": "shared", "name": "Shared"}),
                             "hr": platform_files.zone_record({"id": "hr", "name": "HR"})},
           "sources": {"workday": platform_files.source_record({"code": "WORKDAY", "source_id": 1, "zone": "hr"})},
           "policy_templates": {"table-names": platform_files.template_record(
               {"id": "table-names", "definition": NAMING})}, "problems": []}
    deployed = platform_files.from_platform({"key_tables": {"prod": []}})
    out = platform_files.plan(git, {"prod": deployed}, "prod", set(), "note")
    assert out["steps"] == []
    why = {b["id"]: b["why"] for b in out["blocked"]}
    assert "made with the landing workspace" in why["shared"] and "doesn't have yet" in why["hr"]
    assert "landing zone hr" in why["workday"] and "built-in" in why["table-names"]


@pytest.mark.parametrize("op", ["list_landing_zones", "list_policy_templates"])
def test_platform_functions_older_than_zones_or_templates_are_explained(svc, repo, monkeypatch, op):
    cat = svc._catalog()
    real = cat.call

    def old_functions(o, **kw):
        if o == op:
            raise LookupError("not found")
        return real(o, **kw)

    monkeypatch.setattr(cat, "call", old_functions)
    with pytest.raises(GitError, match="Finish or repair"):
        svc.git_platform_export(repo)
    with pytest.raises(GitError, match="Finish or repair"):
        svc.git_platform_diff(repo)
