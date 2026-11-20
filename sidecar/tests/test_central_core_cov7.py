"""central/core.py: line and branch coverage of the platform's rules the other platform tests leave
out: refusals with the reason a person reads, the T-SQL statements a SQL database in Fabric gets
(through a stand-in connection that answers like one), and the reads of a platform made before a
table existed. SQLite in memory, or the stand-in; nothing reaches Fabric."""

import datetime as dt
import json
import sqlite3

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations to core.OPERATIONS)
from fabric_desktop.central.core import Db, PlatformError

ADMIN = "ada@example.com"
OWNER = "olu@example.com"
MEMBER = "mo@example.com"
NOBODY = "eve@example.com"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    d = Db(conn, "sqlite")
    core.ensure_schema(d)
    core.claim_admin(d, ADMIN)
    yield d
    conn.close()


def _audits(db, action):
    return db.rows(f"SELECT * FROM {db.t('catalog', 'audit')} WHERE action = ?", (action,))


# ---- names and the connection


def test_names_that_arent_ids_are_refused():
    with pytest.raises(PlatformError, match="isn't a domain id"):
        core.domain_id("Sales & Marketing!")
    with pytest.raises(ValueError, match="dialect is tsql or sqlite"):
        Db(None, "postgres")


# ---- keys


def test_lookup_in_a_key_table_that_isnt_there_is_none(db):
    assert core.lookup_key(db, "customer", "C1") is None


def test_an_unregistered_key_table_is_registered_when_asked_for(db):
    core._make_key_table(db, "legacy")  # made before key tables were registered
    assert core.ensure_key_table(db, "legacy", by=NOBODY) is False
    assert [t["name"] for t in core.list_key_tables(db)] == ["legacy"]


def test_clearing_moved_keys_skips_a_table_already_gone(db):
    core.ensure_key_table(db, "customer", by=ADMIN)
    db.run(f"DELETE FROM {db.t('config', 'setting')} WHERE name IN (?, ?)", (core.KEYS_LIVE, core.KEYS_STAGING))
    db.run(f"INSERT INTO {db.t('config', 'setting')} (name, value) VALUES (?, '1')", (core.KEYS_STAGING,))
    db.run(f"DROP TABLE {db.t('keys', 'customer')}")
    assert core.clear_keys(db, by=ADMIN) == {"cleared": ["customer"]}
    assert core.list_key_tables(db) == []


# ---- domains


def test_save_domain_refuses_what_isnt_a_domain(db):
    with pytest.raises(PlatformError, match="A domain needs a name"):
        core.save_domain(db, {"id": "sales"}, by=ADMIN)
    with pytest.raises(PlatformError, match="parent domain nope doesn't exist"):
        core.save_domain(db, {"id": "sales", "name": "Sales", "parent_id": "nope"}, by=ADMIN)
    core.save_domain(db, {"id": "sales", "name": "Sales"}, by=ADMIN)
    core.save_domain(db, {"id": "emea", "name": "EMEA", "parent_id": "sales"}, by=ADMIN)
    with pytest.raises(PlatformError, match="one level deep"):
        core.save_domain(db, {"id": "uk", "name": "UK", "parent_id": "emea"}, by=ADMIN)
    with pytest.raises(PlatformError, match="can't be its own parent"):
        core.save_domain(db, {"id": "sales", "name": "Sales", "parent_id": "sales"}, by=ADMIN)


def test_imported_domains_with_one_name_get_their_own_ids(db):
    out = core.import_fabric_domains(db, [{"id": "f1", "displayName": "Sales"}, {"id": "f2", "displayName": "Sales"}], by=ADMIN)
    assert sorted(out["added"]) == ["sales", "sales-2"]


# ---- projects


def test_save_project_refuses_what_isnt_a_project(db):
    core.save_domain(db, {"id": "sales", "name": "Sales"}, by=ADMIN)
    core.save_domain(db, {"id": "hr", "name": "HR"}, by=ADMIN)
    with pytest.raises(PlatformError, match="The project needs a name"):
        core.register_project(db, {"name": " "}, by=ADMIN)
    with pytest.raises(PlatformError, match="domain nope isn't one of your organization's domains"):
        core.register_project(db, {"name": "crm", "domain": "nope"}, by=ADMIN)
    with pytest.raises(PlatformError, match="hr isn't a subdomain of sales"):
        core.register_project(db, {"name": "crm", "domain": "sales", "subdomain": "hr"}, by=ADMIN)


def test_register_product_refuses_a_bad_product(db):
    with pytest.raises(PlatformError, match="needs its project and name"):
        core.register_product(db, {"project": "crm"}, [], "dev", by=ADMIN)
    with pytest.raises(PlatformError, match="access must be one of"):
        core.register_product(db, {"project": "crm", "name": "c360", "access": "secret"}, [], "dev", by=ADMIN)
    with pytest.raises(PlatformError, match="status must be one of"):
        core.register_product(db, {"project": "crm", "name": "c360", "status": "gone"}, [], "dev", by=ADMIN)


def test_follow_needs_a_sign_in_and_a_product(db):
    db.run(f"DROP TABLE {db.t('catalog', 'follow')}") if db.exists("catalog", "follow") else None
    with pytest.raises(PlatformError, match="Sign in to follow a product"):
        core.follow_product(db, "crm/c360", by="")
    assert db.exists("catalog", "follow"), "following adds its table to an older platform"
    with pytest.raises(PlatformError, match="isn't a published data product"):
        core.follow_product(db, "crm/c360", by=MEMBER)


# ---- settings read back


def test_a_stored_group_naming_that_fails_its_checks_reads_as_the_default(db):
    db.run(f"INSERT INTO {db.t('config', 'setting')} (name, value) VALUES (?, ?)",
           (core._NAMING_SETTING, json.dumps({"case": "sideways"})))
    assert core.entra_group_naming(db) == core.DEFAULT_GROUP_NAMING


def test_group_naming_checks_each_part():
    with pytest.raises(PlatformError, match="up to 40 letters"):
        core.checked_entra_naming({"prefix": "has space"})
    with pytest.raises(PlatformError, match="domain_part is id or name"):
        core.checked_entra_naming({"domain_part": "code"})
    role = next(iter(core.ENTRA_ROLES))
    assert core.checked_entra_naming({"words": {role: "Readers"}})["words"][role] == "Readers"
    assert core.checked_entra_naming({"roles_off": [role, role]})["roles_off"] == [role]


def test_change_window_on_a_platform_without_settings_is_none():
    conn = sqlite3.connect(":memory:")
    try:
        assert core.change_window(Db(conn, "sqlite")) == {"window": None}
    finally:
        conn.close()


def test_unlink_a_group_on_a_platform_without_linked_groups(db):
    core.save_domain(db, {"id": "sales", "name": "Sales"}, by=ADMIN)
    if db.exists("catalog", "entra_group"):
        db.run(f"DROP TABLE {db.t('catalog', 'entra_group')}")
    assert core.unlink_entra_group(db, "sales", next(iter(core.ENTRA_ROLES)), by=ADMIN) == core.list_entra_groups(db, "sales")
    assert _audits(db, "unlink Entra group") == []


# ---- promises, catalog text and glossary


def test_a_promise_of_only_empty_values_is_none():
    assert core._promise({"within_hours": "", "by": ""}) is None
    with pytest.raises(PlatformError, match="is a table"):
        core._promise("daily")


def test_enrichment_needs_a_project_and_a_table(db):
    with pytest.raises(PlatformError, match="target is <project>:<entity>"):
        core.save_enrichment(db, "orders", {"description": "x"}, by=ADMIN)


def test_a_glossary_term_needs_a_name(db):
    with pytest.raises(PlatformError, match="A glossary term needs a name"):
        core.save_term(db, {"term": " "}, by=ADMIN)


def test_refusal_counts_start_again_past_their_cap(db, monkeypatch):
    monkeypatch.setattr(core, "_REFUSALS", {(f"u{i}", "op"): 1 for i in range(10_001)})
    assert core.record_refusal(db, NOBODY, "save_domain") is True
    assert core._REFUSALS == {}


def test_claim_admin_needs_a_sign_in(db):
    with pytest.raises(PlatformError, match="Sign in to become a platform admin"):
        core.claim_admin(db, "")


# ---- sources and landing zones


def test_save_source_refuses_bad_fields(db):
    base = {"name": "erp", "code": "ERP", "title": "ERP"}
    with pytest.raises(PlatformError, match="Expected every ... hours is a number"):
        core.save_source(db, base | {"expected_hours": "daily"}, by=ADMIN)
    with pytest.raises(PlatformError, match="connections is one set of fields per environment"):
        core.save_source(db, base | {"connections": ["dev"]}, by=ADMIN)


def test_adopting_sources_reports_the_ones_it_cant_read(db):
    out = core.adopt_sources(db, [{"code": "bad code!"}, {"code": "CRM", "id": "x"}, {"code": "HR", "id": -3}], by=ADMIN)
    assert [c.split(":")[0] for c in out["conflicts"]] == ["bad code!", "CRM", "HR"]
    assert out["conflicts"][2] == "HR: A source system id is a whole number above 0"
    assert out["moved"] == []


def test_landing_zones_need_a_name_a_shared_zone_and_to_be_there(db):
    with pytest.raises(PlatformError, match="A landing zone needs a name"):
        core.save_landing_zone(db, {"id": "pii"}, by=ADMIN)
    with pytest.raises(PlatformError, match="it has no Shared zone yet"):
        core.switch_landing_reads(db, by=ADMIN)
    with pytest.raises(PlatformError, match="nope isn't a landing zone"):
        core.delete_landing_zone(db, "nope", by=ADMIN)


# ---- project members and joining a domain


def _domains(db):
    core.save_domain(db, {"id": "sales", "name": "Sales", "owners": OWNER}, by=ADMIN)
    core.save_domain(db, {"id": "emea", "name": "EMEA", "parent_id": "sales", "owners": "sue@example.com"}, by=ADMIN)
    core.save_domain(db, {"id": "apac", "name": "APAC", "parent_id": "sales"}, by=ADMIN)
    core.save_domain(db, {"id": "hr", "name": "HR"}, by=ADMIN)


def test_waiting_join_on_a_platform_without_requests_is_none(db):
    db.run(f"DROP TABLE {db.t('catalog', 'request')}")
    assert core._waiting_join(db, "crm", MEMBER) is None


def test_set_project_member_needs_a_project_and_a_known_role(db):
    with pytest.raises(PlatformError, match="Say which project"):
        core.set_project_member(db, " ", MEMBER, by=ADMIN)
    with pytest.raises(PlatformError, match="role must be one of owner, member"):
        core.set_project_member(db, "crm", MEMBER, role="boss", by=ADMIN)


def test_asking_to_join_again_keeps_the_same_ask_and_a_new_choice_withdraws_the_old_one(db):
    _domains(db)
    core.register_project(db, {"name": "crm"}, by=MEMBER)
    first = core.register_project(db, {"name": "crm", "domain": "sales", "subdomain": "emea"}, by=MEMBER)["domain_request"]
    assert "sue@example.com" in first["detail"]["owners"] and OWNER in first["detail"]["owners"]
    again = core.register_project(db, {"name": "crm", "domain": "sales", "subdomain": "emea"}, by=MEMBER)["domain_request"]
    assert again["id"] == first["id"]
    other = core.register_project(db, {"name": "crm", "domain": "sales", "subdomain": "apac"}, by=MEMBER)["domain_request"]
    assert other["id"] != first["id"]
    old = db.rows(f"SELECT status, decided_by FROM {db.t('catalog', 'request')} WHERE id = ?", (first["id"],))[0]
    assert old == {"status": "withdrawn", "decided_by": MEMBER}


def test_a_join_request_names_a_real_domain_and_subdomain(db):
    _domains(db)
    core.register_project(db, {"name": "crm"}, by=MEMBER)
    with pytest.raises(PlatformError, match="domain nope isn't one of your organization's domains"):
        core.request(db, "join_domain", "crm", project="crm", detail={"domain": "nope"}, by=MEMBER)
    with pytest.raises(PlatformError, match="hr isn't a subdomain of sales"):
        core.request(db, "join_domain", "crm", project="crm", detail={"domain": "sales", "subdomain": "hr"}, by=MEMBER)


def test_approving_a_join_whose_domain_or_project_went_is_refused(db):
    _domains(db)
    core.register_project(db, {"name": "crm"}, by=MEMBER)
    ask = core.register_project(db, {"name": "crm", "domain": "hr"}, by=MEMBER)["domain_request"]
    with pytest.raises(PlatformError, match="Only the domain's owners or a platform admin can decide this request"):
        core.decide_request(db, ask["id"], True, by=NOBODY)
    db.run(f"DELETE FROM {db.t('catalog', 'project')} WHERE name = 'crm'")
    with pytest.raises(PlatformError, match="crm isn't in the catalog any more"):
        core.decide_request(db, ask["id"], True, by=ADMIN)
    db.conn.rollback()
    db.run(f"DELETE FROM {db.t('catalog', 'domain')} WHERE id = 'hr'")
    with pytest.raises(PlatformError, match="domain hr isn't there any more"):
        core.decide_request(db, ask["id"], True, by=ADMIN)


def test_principals_are_listed_once():
    out = core._principals("ann@example.com; ann@example.com, bob@example.com")
    assert [p["email"] for p in out] == ["ann@example.com", "bob@example.com"]


# ---- requests


def _product(db, access="private"):
    core.register_project(db, {"name": "crm"}, by=OWNER)
    core.register_product(db, {"project": "crm", "name": "c360", "access": access, "owner": OWNER, "status": "published"},
                          [], "prod", by=OWNER)
    return "crm/c360"


def test_a_request_that_couldnt_be_carried_out_is_refused_before_anyone_decides(db, monkeypatch):
    with pytest.raises(PlatformError, match="kind must be one of"):
        core.request(db, "pony", "x", by=MEMBER)
    core.save_source(db, {"name": "erp", "code": "ERP"}, by=ADMIN)
    with pytest.raises(PlatformError, match="erp is already a source"):
        core.request(db, "source", "erp", by=MEMBER)
    with pytest.raises(PlatformError, match="Say which lakehouse"):
        core.request(db, "shortcut", "", detail={"workspace_id": "w"}, by=MEMBER)
    monkeypatch.delitem(core.REQUEST_HOOKS, "exception", raising=False)  # as on a platform without policy.py
    with pytest.raises(PlatformError, match="can't take policy exceptions yet"):
        core.request(db, "exception", "table-names", by=MEMBER)
    with pytest.raises(PlatformError, match="nope/p isn't a published data product"):
        core.request(db, "access", "nope/p", project="ledger", by=MEMBER)


def test_an_access_request_needs_a_project_and_a_private_product(db):
    pid = _product(db)
    with pytest.raises(PlatformError, match="Say which project needs access"):
        core.request(db, "access", pid, by=MEMBER)
    public = _product(db, "public")
    with pytest.raises(PlatformError, match="is open to everyone"):
        core.request(db, "access", public, project="ledger", by=MEMBER)


def test_a_shortcut_thats_already_there_isnt_asked_for(db):
    lh = "bbbbbbbb-0000-0000-0000-000000000002"
    core.record_shortcuts(db, [{"lakehouse_id": lh}], [{"lakehouse_id": lh, "path": "Tables", "name": "orders",
                                                         "target": {"oneLake": {"path": "Tables/o"}}}], by=ADMIN)
    with pytest.raises(PlatformError, match="There's already a shortcut orders there"):
        core.request(db, "shortcut", "", detail={"workspace_id": "w", "item_id": lh, "where": "Tables", "name": "orders",
                                                 "source": "x"}, by=MEMBER)


def test_a_domain_request_needs_a_new_top_level_parent(db):
    _domains(db)
    with pytest.raises(PlatformError, match="A domain needs a name"):
        core.request(db, "domain", "", detail={}, by=MEMBER)
    with pytest.raises(PlatformError, match="sales is already a domain"):
        core.request(db, "domain", "Sales", detail={}, by=MEMBER)
    with pytest.raises(PlatformError, match="parent domain nope doesn't exist"):
        core.request(db, "domain", "Retail", detail={"parent_id": "nope"}, by=MEMBER)
    with pytest.raises(PlatformError, match="one level deep"):
        core.request(db, "domain", "Retail", detail={"parent_id": "emea"}, by=MEMBER)


def test_progress_and_decisions_only_on_a_waiting_request(db):
    with pytest.raises(PlatformError, match="Only a waiting request can be carried out"):
        core.record_request_progress(db, "nope", {"workspace_id": "w"}, by=ADMIN)
    with pytest.raises(PlatformError, match="That request isn't there any more"):
        core.decide_request(db, "nope", True, by=ADMIN)


def test_a_key_table_made_since_the_request_is_refused_on_approval(db):
    req = core.request(db, "key_table", "customer", by=MEMBER)
    core.ensure_key_table(db, "customer", by=ADMIN)
    with pytest.raises(PlatformError, match="keys.customer was made since: decline this request"):
        core.decide_request(db, req["id"], True, by=ADMIN)


def _waiting(db, kind, target, detail, project=""):
    rid = f"r-{kind}"
    db.run(f"INSERT INTO {db.t('catalog', 'request')} (id, kind, target, detail, project, reason, requested_by, requested_at, "
           "status) VALUES (?, ?, ?, ?, ?, '', ?, ?, 'waiting')",
           (rid, kind, target, json.dumps(detail), project, MEMBER, dt.datetime.now(dt.timezone.utc).isoformat()))
    return rid


def test_approving_workspace_access_needs_the_role_added_first(db):
    rid = _waiting(db, "workspace_access", "Sales Dev", {"workspace_id": "w", "role": "Viewer"})
    with pytest.raises(PlatformError, match="Add the role first"):
        core.decide_request(db, rid, True, by=ADMIN)


def test_an_exception_request_cant_be_approved_without_policies(db, monkeypatch):
    monkeypatch.delitem(core.REQUEST_HOOKS, "exception", raising=False)  # as on a platform without policy.py
    rid = _waiting(db, "exception", "table-names", {})
    with pytest.raises(PlatformError, match="can't record policy exceptions yet"):
        core.decide_request(db, rid, True, by=ADMIN)


def test_a_shortcut_request_for_a_product_is_decided_by_its_owner(db):
    pid = _product(db)
    req = {"kind": "shortcut", "target": "x", "detail": {"product_id": pid}}
    assert core._deciders(db, req) == {OWNER}


def test_revoking_access_on_a_platform_without_reviews(db):
    pid = _product(db)
    db.run(f"INSERT INTO {db.t('catalog', 'grant')} (id, product_id, project, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
           (f"{pid}|ledger", pid, "ledger", OWNER, "2026-01-01T00:00:00+00:00"))
    if db.exists("catalog", "grant_review"):
        db.run(f"DROP TABLE {db.t('catalog', 'grant_review')}")
    assert core.revoke_grant(db, pid, "ledger", by=OWNER) == []
    assert json.loads(_audits(db, "revoke access")[0]["detail"]) == {
        "project": "ledger", "granted_by": OWNER, "granted_at": "2026-01-01T00:00:00+00:00"}


# ---- people and access reviews


def test_note_seen_adds_its_table_and_skips_a_signed_out_caller(db):
    if db.exists("catalog", "person"):
        db.run(f"DROP TABLE {db.t('catalog', 'person')}")
    assert core.note_seen(db, "") == {"user": "", "last_seen": ""}
    assert db.exists("catalog", "person")


def test_an_owner_whose_last_seen_cant_be_read_counts_as_never_seen(db):
    core.save_domain(db, {"id": "sales", "name": "Sales", "owners": OWNER}, by=ADMIN)
    core.ensure_people(db)
    db.run(f"INSERT INTO {db.t('catalog', 'person')} (user_name, last_seen) VALUES (?, ?)", (OWNER, "last week"))
    (gap,) = core.owner_coverage(db)["gaps"]
    assert gap["problems"] == ["No second owner to cover", f"{OWNER} hasn't used the platform yet"]


def test_access_reviews_add_their_table_and_read_a_bad_date_as_due(db):
    pid = _product(db)
    db.run(f"INSERT INTO {db.t('catalog', 'grant')} (id, product_id, project, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
           (f"{pid}|ledger", pid, "ledger", OWNER, "a while ago"))
    if db.exists("catalog", "grant_review"):
        db.run(f"DROP TABLE {db.t('catalog', 'grant_review')}")
    assert core.ensure_grant_reviews(db) == ["catalog.grant_review"]
    (row,) = core.access_reviews(db, by=OWNER)["grants"]
    assert row["project"] == "ledger" and row["due"] is True


# ---- shortcuts

LH = "bbbbbbbb-0000-0000-0000-000000000002"
OTHER_LH = "bbbbbbbb-0000-0000-0000-000000000009"


def _shortcut(name="orders", lh=LH):
    return {"lakehouse_id": lh, "workspace_id": "w", "workspace_name": "Sales", "lakehouse_name": "LH", "path": "Tables",
            "name": name, "target": {"oneLake": {"path": f"Tables/{name}"}}}


def test_shortcut_risks_table_is_added_to_an_older_platform(db):
    if db.exists("catalog", "shortcut_risk"):
        db.run(f"DROP TABLE {db.t('catalog', 'shortcut_risk')}")
    assert "catalog.shortcut_risk" in core.ensure_shortcut_risks(db)


def test_a_scan_ignores_shortcuts_in_lakehouses_it_didnt_read(db):
    out = core.record_shortcuts(db, [{"lakehouse_id": LH}], [_shortcut(), _shortcut("stray", OTHER_LH)], by=ADMIN)
    assert [s["name"] for s in core.list_seen_shortcuts(db)] == ["orders"]
    assert out is not None


def test_shortcut_changes_and_health_refuse_what_they_dont_know(db):
    with pytest.raises(PlatformError, match="kind is added, removed or repointed"):
        core.log_shortcut_change(db, "renamed", _shortcut(), by=ADMIN)
    with pytest.raises(PlatformError, match="health is one of"):
        core.record_shortcut_health(db, [{"id": "x", "health": "sick"}], by=ADMIN)
    assert core.record_shortcut_health(db, [{"id": "not-there", "health": "healthy"}], by=ADMIN) == []
    assert _audits(db, "shortcut health") == [], "nothing checked, nothing recorded"


def test_a_change_with_no_lakehouse_cant_be_undone(db):
    assert core._undoable(db, {"kind": "added", "note": "", "before": None, "after": {"lakehouse_id": ""}}, {}) is False


def test_shortcut_rules_refuse_unknown_rules(db):
    with pytest.raises(PlatformError, match="nope isn't a shortcut rule"):
        core.save_shortcut_rules(db, {"nope": "warn"}, by=ADMIN)
    with pytest.raises(PlatformError, match="who_creates is anyone or admins"):
        core.save_shortcut_rules(db, {"who_creates": "everyone"}, by=ADMIN)


def test_risks_are_accepted_only_on_known_shortcuts_and_withdrawn_only_when_accepted(db):
    with pytest.raises(PlatformError, match="isn't in the platform's inventory"):
        core.accept_shortcut_risk(db, "nope", "cross_env", "on purpose", by=ADMIN)
    with pytest.raises(PlatformError, match="That risk isn't accepted"):
        core.withdraw_shortcut_risk(db, "nope", "cross_env", by=ADMIN)


# ---- endorsement


def test_endorsements_need_an_item_id_and_an_empty_one_reads_none(db):
    assert core.endorsement_label("  ") == "None"
    with pytest.raises(PlatformError, match="needs its Fabric item id"):
        core.record_endorsements(db, [{"item_id": " ", "endorsement": "Certified"}], by=ADMIN)


# ---- call_once


class _CallTaken(Db):
    """A platform whose catalog.call insert fails though no row holds the id (the database refused it)."""

    def run(self, sql, params=()):
        if sql.startswith("INSERT INTO") and "call" in sql:
            raise sqlite3.OperationalError("database is locked")
        return super().run(sql, params)


def test_call_once_says_the_databases_own_error_when_the_id_isnt_there(db):
    taken = _CallTaken(db.conn, "sqlite")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        core.call_once(taken, "a" * 32, "save_term", {"term": {"term": "ARR", "definition": "x"}}, by=ADMIN)


def test_call_once_stops_waiting_for_an_answer_that_doesnt_come(db, monkeypatch):
    db.run(f"INSERT INTO {db.t('catalog', 'call')} (id, op, by_user, at, result) VALUES (?, ?, ?, ?, NULL)",
           ("b" * 32, "save_term", ADMIN, core._now()))
    db.commit()  # the first call's row, still waiting for its answer
    monkeypatch.setattr(core, "CALL_WAITS", 0)
    with pytest.raises(PlatformError, match="was made, but its answer wasn't kept"):
        core.call_once(db, "b" * 32, "save_term", {"term": {"term": "ARR", "definition": "x"}}, by=ADMIN)


# ---- SQL database in Fabric (T-SQL), through a stand-in connection


class _SqlDatabase:
    """Answers like a SQL database in Fabric for the statements these tests make: ``tables`` are the
    (schema, table) pairs sys.tables holds, ``answer(sql, params)`` gives (columns, rows) for the rest."""

    def __init__(self, tables=(), answer=None):
        self.tables, self.answer, self.statements = set(tables), answer, []
        self.description, self._rows, self.rowcount = [("x",)], [], 0

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        cols, rows = ["x"], []
        if "FROM sys.tables t JOIN sys.schemas s" in sql and "s.name = ?" in sql:
            rows = [(1,)] if tuple(params) in self.tables else []
        elif "WHERE s.name = 'keys'" in sql:
            cols, rows = ["name"], [(t,) for s, t in sorted(self.tables) if s == "keys"]
        elif self.answer is not None:
            got = self.answer(sql, tuple(params))
            if got is not None:
                cols, rows = got
        self.description, self._rows = [(c,) for c in cols], list(rows)

    def fetchall(self):
        return self._rows

    def commit(self):
        pass

    def rollback(self):
        pass


def test_a_new_sql_database_gets_its_schemas_and_tables():
    conn = _SqlDatabase()
    out = core.ensure_schema(Db(conn, "tsql"))
    sqls = [s for s, _ in conn.statements]
    assert sqls[:3] == [f"IF SCHEMA_ID('{s}') IS NULL EXEC('CREATE SCHEMA [{s}]')" for s in ("keys", "config", "catalog")]
    assert "catalog.domain" in out["created"] and "catalog.audit" in out["created"]
    assert any(s.startswith("CREATE TABLE [catalog].[audit] (id INT IDENTITY(1,1) PRIMARY KEY") for s in sqls)
    assert any("FROM sys.columns WHERE object_id = OBJECT_ID('catalog.source')" in s for s in sqls)


def test_a_key_table_in_a_sql_database_starts_with_its_unknown_member():
    conn = _SqlDatabase()
    assert core._make_key_table(Db(conn, "tsql"), "customer") is True
    create, unknown = conn.statements[-2:]
    assert create[0].startswith("CREATE TABLE [keys].[customer] (customer_sk INT IDENTITY(1,1) NOT NULL PRIMARY KEY")
    assert "SET IDENTITY_INSERT [keys].[customer] ON" in unknown[0] and unknown[1] == (core.UNKNOWN_KEY, core.UNKNOWN_BK)


def test_keys_are_issued_in_one_statement_in_a_sql_database():
    def answer(sql, params):
        if "OPENJSON" in sql:
            return ["bk", "sk", "inserted"], [("a", 7, 1), ("b", 3, 1)]
        return None
    conn = _SqlDatabase(tables={("keys", "customer"), ("config", "setting")}, answer=answer)
    out = core.issue_keys(Db(conn, "tsql"), "customer", ["b", "a", "a", None, " "], by=ADMIN)
    assert out == {"keys": {"a": 7, "b": 3}, "inserted": 1}
    issue = next(p for s, p in conn.statements if "OPENJSON" in s)
    assert json.loads(issue[0]) == ["a", "b"]
    assert any("[catalog].[audit]" in s and p[2] == "issue keys" for s, p in conn.statements)


def test_keys_already_issued_add_no_audit_row_in_a_sql_database():
    conn = _SqlDatabase(tables={("keys", "customer")}, answer=lambda sql, p: (["bk", "sk", "inserted"], [("a", 7, 0)])
                        if "OPENJSON" in sql else None)
    assert core.issue_keys(Db(conn, "tsql"), "customer", ["a"]) == {"keys": {"a": 7}, "inserted": 0}
    assert not any("[catalog].[audit]" in s for s, _ in conn.statements)
    empty = _SqlDatabase(tables={("keys", "customer")}, answer=lambda sql, p: (["bk", "sk", "inserted"], []) if "OPENJSON" in sql else None)
    assert core.issue_keys(Db(empty, "tsql"), "customer", ["a"]) == {"keys": {}, "inserted": 0}


def test_key_tables_in_a_sql_database_are_read_from_its_keys_schema():
    def answer(sql, params):
        if "[catalog].[key_table]" in sql:
            return ["name", "business_key", "description", "domain_id", "created_at", "created_by"], []
        if "COUNT(*) AS n" in sql:
            return ["n", "top"], [(2, 2)]
        return None
    conn = _SqlDatabase(tables={("keys", "customer"), ("keys", "Bad Name")}, answer=answer)
    (only,) = core.list_key_tables(Db(conn, "tsql"))
    assert only["name"] == "customer" and only["keys"] == 2 and only["next"] == 3


# ---- what earlier rounds left


def test_a_key_table_is_made_once_and_blank_business_keys_issue_nothing(db):
    assert core.ensure_key_table(db, "customer", by=ADMIN) is True
    assert core._make_key_table(db, "customer") is False
    assert core.issue_keys(db, "customer", [None, "   "], by=ADMIN) == {"keys": {}, "inserted": 0}
    out = core.issue_keys(db, "customer", [None, " C1 ", ""], by=ADMIN)
    assert list(out["keys"]) == ["C1"] and out["inserted"] == 1


def test_an_empty_stored_group_naming_reads_as_the_default(db):
    db.run(f"INSERT INTO {db.t('config', 'setting')} (name, value) VALUES (?, '')", (core._NAMING_SETTING,))
    assert core.entra_group_naming(db) == core.DEFAULT_GROUP_NAMING


def test_roles_off_names_only_domain_roles():
    with pytest.raises(PlatformError, match="roles_off names roles from"):
        core.checked_entra_naming({"roles_off": ["janitors"]})


def test_a_project_on_a_platform_from_before_members_and_links_lists_its_earlier_people(db):
    _product(db)
    db.run(f"DROP TABLE {db.t('catalog', 'project_member')}")
    db.run(f"DROP TABLE {db.t('catalog', 'link')}")
    out = core.list_project_members(db, "crm", by=OWNER)
    assert out["earlier"] is True
    assert out["members"] == [{"user": OWNER, "role": "owner"}]
    assert out["can_change"] is True


def test_keys_from_a_table_that_isnt_there_are_refused_with_how_to_ask(db):
    with pytest.raises(PlatformError, match="keys.vendor isn't one of the platform's key tables. Ask a platform admin"):
        core.issue_keys(db, "vendor", ["V1"], by=ADMIN)


def test_group_naming_on_a_platform_without_settings_is_the_default():
    conn = sqlite3.connect(":memory:")
    try:
        assert core.entra_group_naming(Db(conn, "sqlite")) == core.DEFAULT_GROUP_NAMING
    finally:
        conn.close()


def test_switching_reads_twice_keeps_the_first_switch_and_audits_once(db):
    core.save_landing_zone(db, {"name": "Shared", "workspace_id": "ws-landing", "lakehouse_id": LH}, by=ADMIN)
    [first] = core.switch_landing_reads(db, by=ADMIN)
    [again] = core.switch_landing_reads(db, by=ADMIN)
    assert again["switched_at"] == first["switched_at"] and again["switched_by"] == ADMIN
    assert len(_audits(db, "switch landing reads")) == 1
