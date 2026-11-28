"""central/core.py: the refusals and quiet paths the platform tests leave out, on a platform kept in a SQLite
file (LocalPlatform runs the same core.py as UDF_Platform). No tenant calls."""

from __future__ import annotations

import json
import sqlite3

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations)
from fabric_desktop.central.client import LocalPlatform

ADMIN = "ada@example.com"
OTHER = "bo@example.com"
E = core.PlatformError


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.db", user=lambda: ADMIN)
    p.call("ensure_schema")
    p.call("claim_admin")
    return p


def run(p, fn, *args, **kw):
    with p._db() as db:
        return fn(db, *args, **kw)


def as_other(p, fn, *args, **kw):
    return run(p, fn, *args, **{**kw, "by": OTHER})


def test_a_db_is_tsql_or_sqlite():
    with pytest.raises(ValueError, match="dialect is tsql or sqlite"):
        core.Db(sqlite3.connect(":memory:"), "postgres")


def test_a_key_table_made_twice_is_made_once(platform):
    assert run(platform, core._make_key_table, "customer") is True
    assert run(platform, core._make_key_table, "customer") is False


def test_a_key_table_made_before_registration_is_registered_on_ensure(platform):
    run(platform, core._make_key_table, "legacy")
    assert run(platform, core._row, "key_table", "name", "legacy") is None
    run(platform, core.ensure_key_table, "legacy", by=ADMIN)
    assert run(platform, core._row, "key_table", "name", "legacy")["name"] == "legacy"


def test_issuing_from_a_missing_key_table_is_refused(platform):
    with pytest.raises(E, match="keys.nothere isn't one of the platform's key tables"):
        platform.call("issue_keys", name="nothere", business_keys=["a"])


def test_none_business_keys_are_skipped(platform):
    platform.call("ensure_key_table", name="customer", business_key="code")
    out = platform.call("issue_keys", name="customer", business_keys=["a", None, "b"])
    assert out == {"keys": {"a": 1, "b": 2}, "inserted": 2}


def test_looking_up_in_a_missing_key_table_is_none(platform):
    assert run(platform, core.lookup_key, "nothere", "a") is None


@pytest.mark.parametrize("domain, words", [
    ({"id": "sales", "name": "  "}, "A domain needs a name"),
    ({"id": "sales", "name": "Sales", "parent_id": "sales"}, "A domain can't be its own parent"),
])
def test_domain_refusals(platform, domain, words):
    if "parent_id" in domain:
        platform.call("save_domain", domain={"id": "sales", "name": "Sales"})
    with pytest.raises(E, match=words):
        platform.call("save_domain", domain=domain)


def test_a_slug_steps_past_taken_ids():
    assert core._slug("Sales", {"sales", "sales-2"}) == "sales-3"
    assert core._slug("!!!", set()) == "domain"


def test_project_refusals(platform):
    platform.call("save_domain", domain={"id": "sales", "name": "Sales"})
    platform.call("save_domain", domain={"id": "hr", "name": "HR"})
    platform.call("save_domain", domain={"id": "emea", "name": "EMEA", "parent_id": "hr"})
    with pytest.raises(E, match="The project needs a name"):
        platform.call("register_project", project={"name": " "})
    with pytest.raises(E, match="emea isn't a subdomain of sales"):
        platform.call("register_project", project={"name": "ledger", "domain": "sales", "subdomain": "emea"})
    with pytest.raises(E, match="nope isn't a subdomain of sales"):
        platform.call("register_project", project={"name": "ledger", "domain": "sales", "subdomain": "nope"})


@pytest.mark.parametrize("product, words", [
    ({"project": "", "name": "orders"}, "A product needs its project and name"),
    ({"project": "ledger", "name": ""}, "A product needs its project and name"),
    ({"project": "ledger", "name": "orders", "access": "secret"}, "access must be one of"),
    ({"project": "ledger", "name": "orders", "status": "gone"}, "status must be one of"),
])
def test_product_refusals(platform, product, words):
    with pytest.raises(E, match=words):
        platform.call("register_product", product=product, ports=[], env="dev")


def test_following_needs_a_sign_in(platform):
    with pytest.raises(E, match="Sign in to follow a product"):
        run(platform, core.follow_product, "ledger/orders", by="")


def test_ensure_follows_makes_the_table_once(platform):
    with platform._db() as db:
        if db.exists("catalog", "follow"):
            db.run(f"DROP TABLE {db.t('catalog', 'follow')}")
            db.commit()
    assert run(platform, core.ensure_follows) == ["catalog.follow"]
    assert run(platform, core.ensure_follows) == []


@pytest.mark.parametrize("naming, words", [
    ({"domain_part": "code"}, "domain_part is id or name"),
    ({"roles_off": ["owner", "janitor"]}, "roles_off names roles from"),
])
def test_entra_naming_refusals(naming, words):
    with pytest.raises(E, match=words):
        core.checked_entra_naming(naming)


def test_entra_naming_takes_roles_off():
    role = next(iter(core.ENTRA_ROLES))
    assert core.checked_entra_naming({"roles_off": [role, role]})["roles_off"] == [role]


def test_a_change_window_on_a_platform_without_settings_is_none(tmp_path):
    p = LocalPlatform(tmp_path / "empty.db", user=lambda: ADMIN)
    assert run(p, core.change_window) == {"window": None}


def test_a_promise_with_nothing_usable_is_none():
    assert core._promise({"within_hours": None, "by": ""}) is None
    assert core._promise({"envs": ["prod"]}) is None


def test_an_enrichment_target_names_a_project_and_entity(platform):
    with pytest.raises(E, match="target is <project>:<entity>"):
        platform.call("save_enrichment", target="orders", fields={"description": "x"})


def test_a_glossary_term_needs_a_name(platform):
    with pytest.raises(E, match="A glossary term needs a name"):
        platform.call("save_term", term={"term": " "})


def test_refusal_counts_start_again_past_ten_thousand(platform, monkeypatch):
    monkeypatch.setattr(core, "_REFUSALS", {(f"u{i}", "op"): 1 for i in range(10_001)})
    assert run(platform, core.record_refusal, OTHER, "save_domain") is True
    assert core._REFUSALS == {}  # the old counts were dropped, and this one went into its row
    rows = run(platform, lambda db: db.rows(f"SELECT detail FROM {db.t('catalog', 'audit')} WHERE action = ?",
                                            ("refused save_domain",)))
    assert [json.loads(r["detail"]) for r in rows] == [{"count": 1}]


def test_claiming_admin_needs_a_sign_in(platform):
    with pytest.raises(E, match="Sign in to become a platform admin"):
        run(platform, core.claim_admin, by="")


def test_key_tables_with_names_that_arent_key_names_are_left_out(platform):
    platform.call("ensure_key_table", name="customer", business_key="code")
    with platform._db() as db:
        db.run('CREATE TABLE "keys__Bad-Name" (x INT)')
        db.commit()
    assert [t["name"] for t in platform.call("list_key_tables")] == ["customer"]


@pytest.mark.parametrize("source, words", [
    ({"name": "crm", "expected_hours": "daily"}, "Expected every ... hours is a number"),
    ({"name": "crm", "connections": ["dev"]}, "connections is one set of fields per environment"),
    ({"name": "crm", "connections": {"dev": {"api_token": "abc"}}},  # pragma: allowlist secret (test value)
     "dev.api_token: keep secrets in Key Vault"),
])
def test_source_refusals(platform, source, words):
    with pytest.raises(E, match=words):
        platform.call("save_source", source=source)


def test_a_source_id_must_be_a_whole_number_above_zero(platform):
    row = core._source_row({"name": "crm"}, ADMIN)
    with pytest.raises(E, match="A source system id is a whole number$"):
        run(platform, core._put_source, dict(row), "abc")
    with pytest.raises(E, match="A source system id is a whole number above 0"):
        run(platform, core._put_source, dict(row), "0")
    run(platform, core._put_source, dict(row), "7")
    assert run(platform, core._row, "source", "name", "crm")["source_id"] == "7"


def test_adopting_a_source_with_a_bad_id_is_a_conflict(platform):
    out = platform.call("adopt_sources", systems=[{"code": "CRM", "id": "x"}, {"code": "", "id": 1}])
    assert [c.split(":")[0] for c in out["conflicts"]] == ["CRM", "?"]
    assert json.dumps(out)  # plain data


# ---- landing zones, members, requests


def test_a_landing_zone_needs_a_name_and_must_exist_to_be_removed(platform):
    with pytest.raises(E, match="A landing zone needs a name"):
        platform.call("save_landing_zone", zone={"id": "finance"})
    with pytest.raises(E, match="finance isn't a landing zone"):
        platform.call("delete_landing_zone", id="finance")


@pytest.mark.parametrize("project, user, role, words", [
    ("", OTHER, "member", "Say which project"),
    ("ledger", "not-an-email", "member", "isn't an email address"),
    ("ledger", OTHER, "boss", "role must be one of"),
])
def test_member_refusals(platform, project, user, role, words):
    with pytest.raises(E, match=words):
        platform.call("set_project_member", project=project, user=user, role=role)


def test_request_refusals(platform):
    with pytest.raises(E, match="kind must be one of"):
        platform.call("request", kind="pony", target="x")
    with pytest.raises(E, match="Say which lakehouse, where in it"):
        platform.call("request", kind="shortcut", target="", detail={"workspace_id": "w", "item_id": "i"})
    with pytest.raises(E, match="ledger/orders isn't a published data product"):
        platform.call("request", kind="access", target="ledger/orders", project="sales")


def test_an_access_request_needs_a_project_and_a_private_product(platform):
    platform.call("register_project", project={"name": "ledger"})
    platform.call("register_product", product={"project": "ledger", "name": "orders", "access": "public"},
                  ports=[], env="dev")
    platform.call("register_product", product={"project": "ledger", "name": "pay", "access": "private"},
                  ports=[], env="dev")
    with pytest.raises(E, match="Say which project needs access"):
        platform.call("request", kind="access", target="ledger/pay")
    with pytest.raises(E, match="ledger/orders is open to everyone"):
        platform.call("request", kind="access", target="ledger/orders", project="sales")


def test_progress_and_decisions_need_a_waiting_request(platform):
    with pytest.raises(E, match="Only a waiting request can be carried out"):
        platform.call("record_request_progress", id="nope", made={"x": 1})
    with pytest.raises(E, match="That request isn't there any more"):
        platform.call("decide_request", id="nope", approve=True)


def test_a_key_table_made_since_the_request_is_declined_by_name(platform):
    req = as_other(platform, core.request, "key_table", "invoice", reason="needed")
    platform.call("ensure_key_table", name="invoice", business_key="no")
    with pytest.raises(E, match="keys.invoice was made since: decline this request"):
        platform.call("decide_request", id=req["id"], approve=True)


# ---- shortcuts and endorsements


def test_shortcut_refusals(platform):
    with pytest.raises(E, match="kind is added, removed or repointed"):
        platform.call("log_shortcut_change", kind="renamed", shortcut={})
    with pytest.raises(E, match="health is one of"):
        platform.call("record_shortcut_health", results=[{"id": "x", "health": "meh"}])
    with pytest.raises(E, match="nope isn't a shortcut rule"):
        platform.call("save_shortcut_rules", rules={"nope": 1})
    with pytest.raises(E, match="who_creates is anyone or admins"):
        platform.call("save_shortcut_rules", rules={"who_creates": "everyone"})
    with pytest.raises(E, match="That shortcut isn't in the platform's inventory"):
        platform.call("accept_shortcut_risk", shortcut_id="nope", kind="cross_env", reason="on purpose")
    with pytest.raises(E, match="That risk isn't accepted"):
        platform.call("withdraw_shortcut_risk", shortcut_id="nope", kind="cross_env")


def test_health_of_a_shortcut_not_in_the_inventory_is_skipped(platform):
    assert platform.call("record_shortcut_health", results=[{"id": "gone", "health": "healthy"}]) == []
    assert "shortcut health" not in [a["action"] for a in platform.call("audit")]  # nothing checked, nothing logged


def test_endorsement_labels():
    assert core.endorsement_label(None) == "None"
    assert core.endorsement_label("  ") == "None"
    assert core.endorsement_label("Something New") == "Something New"


def test_each_scanned_item_needs_its_id(platform):
    with pytest.raises(E, match="Each scanned item needs its Fabric item id"):
        platform.call("record_endorsements", items=[{"endorsement": "Certified"}])
    with pytest.raises(E, match="Each scanned item needs its Fabric item id"):
        platform.call("record_endorsements", items=[{"item_id": "x" * (core.MAX_BK + 1)}])
