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
