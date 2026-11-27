"""central/core.py, mutation batch 401: what a run of each operation gives back, and the refusals that
guard them, on a platform kept in a SQLite file (LocalPlatform runs the same core.py as UDF_Platform).
No tenant calls."""

import json

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations)
from fabric_desktop.central.client import LocalPlatform

ADMIN = "ada@example.com"


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.db", user=lambda: ADMIN)
    p.call("ensure_schema")
    return p


def run(p, fn, *args, **kw):
    with p._db() as db:
        return fn(db, *args, **kw)


def actions(p):
    with p._db() as db:
        return [r["action"] for r in db.rows(f"SELECT action FROM {db.t('catalog', 'audit')} ORDER BY at, action")]


def test_a_new_key_table_is_reported_as_made(platform):
    assert platform.call("ensure_key_table", name="customer", business_key="code") is True


def test_a_setting_on_a_platform_without_settings_is_empty(tmp_path):
    p = LocalPlatform(tmp_path / "empty.db", user=lambda: ADMIN)
    assert run(p, core._setting, core.KEYS_CLOSED) == ""


def test_closing_and_reopening_keys_is_audited_as_each(platform):
    platform.call("close_keys", reason="Moving the platform")
    platform.call("close_keys", reason="")
    found = actions(platform)
    assert "close keys" in found and "reopen keys" in found


def test_a_subdomain_of_a_missing_domain_is_refused(platform):
    with pytest.raises(core.PlatformError, match="parent domain nowhere doesn't exist"):
        platform.call("save_domain", domain={"name": "EMEA", "parent_id": "nowhere"})


def test_a_project_in_a_missing_domain_is_refused(platform):
    with pytest.raises(core.PlatformError, match="domain nowhere isn't one of your organization's domains"):
        platform.call("register_project", project={"name": "ledger", "domain": "nowhere"})


def test_the_product_status_table_is_reported_when_it_is_made(platform):
    with platform._db() as db:
        db.run(f"DROP TABLE {db.t('catalog', 'product_status')}")
        db.commit()
    assert run(platform, core.ensure_product_status) == ["catalog.product_status"]
    assert run(platform, core.ensure_product_status) == []


def test_a_status_report_of_more_than_500_products_is_refused(platform):
    with pytest.raises(core.PlatformError, match="at most 500 products"):
        platform.call("report_product_status", project="p", env="dev", statuses=[{}] * 501)


def test_an_unknown_classification_is_refused(platform):
    with pytest.raises(core.PlatformError, match="classification must be one of"):
        platform.call("save_enrichment", target="ledger:orders", fields={"classification": "top secret"})


def test_saving_a_term_gives_back_what_was_saved(platform):
    row = platform.call("save_term", term={"term": "Churn", "definition": "Customers who left"})
    assert row["term"] == "Churn" and row["definition"] == "Customers who left" and row["updated_by"] == ADMIN


def test_a_group_cant_be_a_platform_admin(platform):
    with pytest.raises(core.PlatformError, match="Groups can't be platform admins"):
        platform.call("add_admin", user="Data team")


def test_a_source_row_checks_its_connections_and_keeps_its_active_flag():
    with pytest.raises(core.PlatformError, match="connections is one set of fields per environment"):
        core._source_row({"name": "crm", "connections": ["dev"]}, ADMIN)
    assert core._source_row({"name": "crm", "active": True}, ADMIN)["active"] == "1"
    assert core._source_row({"name": "crm"}, ADMIN)["active"] == "1"
    assert core._source_row({"name": "crm", "active": False}, ADMIN)["active"] == "0"


def test_each_new_source_takes_the_next_id_and_deleting_one_lists_the_rest(platform):
    with platform._db() as db:
        core._put_source(db, core._source_row({"name": "crm"}, ADMIN))
        core._put_source(db, core._source_row({"name": "erp"}, ADMIN))
        db.commit()
        ids = {r["name"]: r["source_id"] for r in db.rows(f"SELECT name, source_id FROM {db.t('catalog', 'source')}")}
    assert ids == {"crm": "1", "erp": "2"}
    left = platform.call("delete_source", name="crm")
    assert [s["name"] for s in left] == ["erp"]


def test_a_project_known_by_its_row_is_in_the_catalog(platform):
    platform.call("register_project", project={"name": "ledger"})
    assert run(platform, core._in_catalog, "ledger") is True
    assert run(platform, core._in_catalog, "nothing") is False


def test_joining_a_subdomain_of_another_domain_is_refused(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("save_domain", domain={"name": "Finance"})
    platform.call("save_domain", domain={"name": "EMEA", "parent_id": "finance"})
    with pytest.raises(core.PlatformError, match="emea isn't a subdomain of sales"):
        run(platform, core._check_join_domain, "ledger", {"domain": "sales", "subdomain": "emea"}, "", ADMIN)


def test_a_data_request_says_what_is_needed_and_names_a_published_product(platform):
    with pytest.raises(core.PlatformError, match="Say what you need"):
        run(platform, core._check_data_request, "sales", {"what": "new"}, " ")
    platform.call("save_domain", domain={"name": "Sales"})
    assert run(platform, core._check_data_request, "sales", {"what": "new"}, "a churn table")[0] == "sales"


def test_note_seen_says_who_and_which_day(platform):
    out = platform.call("note_seen")
    assert out["user"] == ADMIN and len(out["last_seen"]) == 10


def test_a_targets_canonical_text_sorts_its_keys():
    assert core._canon({"b": 1, "a": 2}) == json.dumps({"a": 2, "b": 1})
    assert core._canon(None) == "{}"


def test_whoami_lists_only_the_domains_the_caller_owns(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": ADMIN})
    platform.call("save_domain", domain={"name": "Finance", "owners": "fin@example.com"})
    assert platform.call("whoami")["domains_owned"] == ["sales"]


def test_a_registered_key_table_is_listed_with_its_own_business_key(platform):
    platform.call("ensure_key_table", name="customer", business_key="code", description="Customers")
    row = next(r for r in platform.call("list_key_tables") if r["name"] == "customer")
    assert row["business_key"] == "code" and row["description"] == "Customers"


def test_a_change_request_names_a_published_product_and_its_owners(platform):
    with pytest.raises(core.PlatformError, match="crm/none isn't a published data product"):
        run(platform, core._check_data_request, "crm/none", {"what": "change"}, "add a column")
    with platform._db() as db:
        core._upsert(db, "catalog", "product", "id", {"id": "crm/c360", "project": "crm", "name": "c360",
                                                     "status": "published", "owner": "pat@example.com",
                                                     "domain_id": "sales"})
        core._upsert(db, "catalog", "product", "id", {"id": "crm/draft", "project": "crm", "name": "draft",
                                                     "status": "draft", "owner": "pat@example.com"})
        db.commit()
    with pytest.raises(core.PlatformError, match="crm/draft isn't a published data product"):
        run(platform, core._check_data_request, "crm/draft", {"what": "change"}, "add a column")
    target, out = run(platform, core._check_data_request, "crm/c360", {"what": "change"}, "add a column")
    assert target == "crm/c360"
    assert (out["domain_id"], out["owners"], out["product_name"]) == ("sales", "pat@example.com", "c360")


def test_a_source_takes_hours_with_a_decimal_point_and_refuses_a_secret_in_its_connections():
    assert core._source_row({"name": "crm", "expected_hours": "1.5"}, ADMIN)["expected_hours"] == "1.5"
    assert json.loads(core._source_row({"name": "crm", "connections": {"dev": {"server": "sql1"}}}, ADMIN)
                      ["connections"]) == {"dev": {"server": "sql1"}}
    with pytest.raises(core.PlatformError, match="dev.password: keep secrets in Key Vault"):
        core._source_row({"name": "crm", "connections": {"dev": {"password": "p"}}}, ADMIN)  # pragma: allowlist secret (test value)


# Second pass on batch 401: the survivors of the first.

BOB = "bob@example.com"
PAT = "pat@example.com"


def audit_rows(p):
    with p._db() as db:
        return db.rows(f"SELECT action, target, detail FROM {db.t('catalog', 'audit')}")


def put(p, table, key, row):
    with p._db() as db:
        if table == "grant_review":
            core.ensure_grant_reviews(db)
        if table == "person":
            core.ensure_people(db)
        core._upsert(db, "catalog", table, key, row)
        db.commit()


def put_product(p, pid, **kw):
    project, name = pid.split("/")
    with p._db() as db:
        core.ensure_contracts(db)
    put(p, "product", "id", {"id": pid, "project": project, "name": name, "status": "published",
                             "access": "private", "owner": PAT, "version": "1", **kw})


def test_a_setting_on_a_store_without_the_settings_table_is_empty_text():
    import sqlite3
    db = core.Db(sqlite3.connect(":memory:"), "sqlite")
    assert core._setting(db, core.KEYS_CLOSED) == ""


def test_close_keys_audit_names_match_what_was_done(platform):
    platform.call("close_keys", reason="Moving the platform")
    platform.call("close_keys", reason="")
    found = {json.loads(r["detail"])["reason"]: r["action"] for r in audit_rows(platform) if r["target"] == "keys"}
    assert found == {"Moving the platform": "close keys", "": "reopen keys"}


def test_a_new_fabric_domain_keeps_its_description_and_a_second_import_changes_nothing(platform):
    domains = [{"id": "f1", "displayName": "Sales", "description": "Selling things"}]
    assert platform.call("import_fabric_domains", domains=domains)["added"] == ["sales"]
    row = next(d for d in platform.call("list_domains") if d["id"] == "sales")
    assert row["description"] == "Selling things"
    again = platform.call("import_fabric_domains", domains=domains)
    assert again["unchanged"] == ["sales"] and again["updated"] == []


def test_a_renamed_fabric_domain_is_updated(platform):
    platform.call("import_fabric_domains", domains=[{"id": "f1", "displayName": "Sales"}])
    out = platform.call("import_fabric_domains", domains=[{"id": "f1", "displayName": "Selling"}])
    assert out["updated"] == ["sales"] and out["unchanged"] == []
    assert next(d for d in platform.call("list_domains") if d["id"] == "sales")["name"] == "Selling"


def test_a_project_keeps_its_team_and_its_landing_readers(platform):
    row = platform.call("register_project", project={"name": "ledger", "team": "Finance data",
                                                     "landing": {"crm": ["orders", "accounts"]}})
    assert row["team"] == "Finance data"
    with platform._db() as db:
        readers = db.rows(f"SELECT source, entities FROM {db.t('catalog', 'reader')} WHERE project = ?", ("ledger",))
    assert [(r["source"], json.loads(r["entities"])) for r in readers] == [("crm", ["accounts", "orders"])]


def test_a_published_environment_keeps_the_products_deprecation_date(platform):
    platform.call("register_product", product={"project": "crm", "name": "c360", "status": "deprecated",
                                               "deprecated_on": "2027-01-01", "owner": ADMIN}, ports=[], env="prod")
    with platform._db() as db:
        env = core._row(db, "product_env", "id", "crm/c360:prod")
    assert env["deprecated_on"] == "2027-01-01"


def test_a_first_publish_naming_other_owners_is_refused(platform):
    run(platform, core.register_project, {"name": "crm"}, by=BOB)
    with pytest.raises(core.PlatformError, match="crm/c360 names pat@example.com as its owners, but you publish it"):
        run(platform, core.register_product, {"project": "crm", "name": "c360", "owner": PAT}, [], "prod", by=BOB)


def test_the_owner_sees_who_follows_their_product(platform):
    put_product(platform, "crm/c360")
    with platform._db() as db:
        core.ensure_follows(db)
    run(platform, core.follow_product, "crm/c360", by=BOB)
    assert run(platform, core.product_followers, "crm/c360", by=PAT) == [BOB]


def test_a_public_or_granted_product_links_from_any_domain(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    put_product(platform, "crm/open", access="public", domain_id="sales")
    put_product(platform, "crm/near", access="domain", domain_id="sales")
    put(platform, "grant", "id", {"id": "crm/near|ledger", "product_id": "crm/near", "project": "ledger"})
    assert platform.call("register_link", consumer="ledger", alias="o", product_id="crm/open", port="p",
                         reads={})["id"] == "ledger:o"
    assert platform.call("register_link", consumer="ledger", alias="n", product_id="crm/near", port="p",
                         reads={})["id"] == "ledger:n"
    with pytest.raises(core.PlatformError, match="open to the sales domain only"):
        platform.call("register_link", consumer="other", alias="n", product_id="crm/near", port="p", reads={})


def test_an_admin_certifies_a_project_that_has_no_domain(platform):
    platform.call("register_project", project={"name": "ledger"})
    out = platform.call("save_enrichment", target="ledger:orders", fields={"certified": "yes"})
    assert out["certified"] == "yes"


def test_a_project_known_by_a_product_is_in_the_catalog(platform):
    put_product(platform, "crm/c360")
    assert run(platform, core._in_catalog, "crm") is True


def test_a_projects_stored_members_are_listed_and_an_older_project_lists_its_earlier_people(platform):
    with platform._db() as db:
        core.ensure_project_members(db)
    put(platform, "project", "name", {"name": "legacy", "updated_by": PAT})
    old = platform.call("list_project_members", project="legacy")
    assert old["earlier"] is True and old["members"] == [{"user": PAT, "role": "owner"}]
    put(platform, "project_member", "id", {"id": "legacy|" + BOB, "project": "legacy", "user_name": BOB,
                                            "role": "reader"})
    now = platform.call("list_project_members", project="legacy")
    assert now["earlier"] is False and now["members"] == [{"user": BOB, "role": "reader"}]


def test_moved_sources_are_audited_against_the_project(platform):
    platform.call("adopt_sources", systems=[{"code": "CRM", "id": 4}], project="ledger")
    assert ("move sources", "ledger") in [(r["action"], r["target"]) for r in audit_rows(platform)]


def test_a_request_for_a_source_the_platform_has_is_refused(platform):
    with platform._db() as db:
        core._put_source(db, core._source_row({"name": "crm"}, ADMIN))
        db.commit()
    with pytest.raises(core.PlatformError, match="crm is already a source in the landing zone"):
        platform.call("request", kind="source", target="crm")


def test_an_access_request_for_a_project_that_has_access_is_refused(platform):
    put_product(platform, "crm/c360")
    put(platform, "grant", "id", {"id": "crm/c360|ledger", "product_id": "crm/c360", "project": "ledger"})
    with pytest.raises(core.PlatformError, match="ledger already has access to crm/c360"):
        platform.call("request", kind="access", target="crm/c360", project="ledger")


def test_a_shortcut_request_for_a_product_is_decided_by_its_owners(platform):
    put_product(platform, "crm/c360")
    req = {"kind": "shortcut", "target": "x", "detail": {"product_id": "crm/c360"}}
    assert run(platform, core._deciders, req) == {PAT}


def test_taking_access_away_also_drops_its_review(platform):
    put_product(platform, "crm/c360")
    put(platform, "grant", "id", {"id": "crm/c360|ledger", "product_id": "crm/c360", "project": "ledger"})
    put(platform, "grant_review", "id", {"id": "crm/c360|ledger", "product_id": "crm/c360", "project": "ledger"})
    platform.call("revoke_grant", product_id="crm/c360", project="ledger")
    with platform._db() as db:
        assert core._row(db, "grant_review", "id", "crm/c360|ledger") is None


def test_a_product_owner_reviews_the_access_to_their_own_product(platform):
    put_product(platform, "crm/c360")
    put(platform, "grant", "id", {"id": "crm/c360|ledger", "product_id": "crm/c360", "project": "ledger",
                                  "granted_at": core._now()})
    grants = run(platform, core.access_reviews, by=PAT)["grants"]
    assert [(g["product_id"], g["project"]) for g in grants] == [("crm/c360", "ledger")]
    assert run(platform, core.access_reviews, by=BOB)["grants"] == []


def coverage(p, presence=True):
    return {g["id"]: g["problems"] for g in run(p, core.owner_coverage, presence)["gaps"]}


def test_owner_coverage_names_no_owner_and_an_owner_never_seen(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("save_domain", domain={"name": "Finance", "owners": f"{ADMIN}, {BOB}"})
    gaps = coverage(platform)
    assert gaps["sales"] == ["No owner"]
    # ada made changes (the audit says so); bob never used the platform
    assert gaps["finance"] == [f"{BOB} hasn't used the platform yet"]


def test_owners_seen_lately_or_written_as_names_are_covered(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "Sales team; Finance team"})
    platform.call("save_domain", domain={"name": "Finance", "owners": f"{ADMIN}, {BOB}"})
    put(platform, "person", "user_name", {"user_name": BOB, "last_seen": core._now()})
    assert coverage(platform) == {}


def test_without_presence_nobody_is_checked_for_being_quiet(platform):
    platform.call("save_domain", domain={"name": "Finance", "owners": f"{BOB}, {PAT}"})
    assert coverage(platform, presence=False) == {}


def test_anyone_may_report_a_shortcut_broken(platform):
    put(platform, "shortcut", "id", {"id": "s1", "lakehouse_id": "lh1", "name": "orders", "health": "healthy"})
    out = run(platform, core.record_shortcut_health, [{"id": "s1", "health": "broken", "detail": "gone"}], by=BOB)
    assert [c["kind"] for c in out] == ["broke"]


def test_shortcut_changes_go_back_thirty_days_by_default(platform):
    import datetime as dt
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30, hours=12)).isoformat()
    put(platform, "shortcut_change", "id", {"id": "c1", "at": old, "kind": "broke", "shortcut_id": "s1"})
    assert platform.call("shortcut_changes") == []
    assert [c["id"] for c in platform.call("shortcut_changes", days=31)] == ["c1"]
