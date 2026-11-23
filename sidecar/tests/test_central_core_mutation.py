"""Mutation tests for the platform's database logic (central/core.py), on a SQLite platform, with a T-SQL
recorder where only the statements sent matter. Found by tools/mutate.py."""

import sqlite3

import pytest

from fabric_desktop.central import core

ADMIN = "ada@example.com"


@pytest.fixture
def db():
    d = core.Db(sqlite3.connect(":memory:"), "sqlite")
    core.ensure_schema(d)
    core.claim_admin(d, ADMIN)
    return d


def _count(db, table, where="1 = 1", params=()):
    return db.rows(f"SELECT COUNT(*) AS n FROM {db.t('catalog', table)} WHERE {where}", params)[0]["n"]


# --------------------------------------------------------------------------- keys


def test_a_key_table_made_now_says_so_and_a_second_ask_doesnt(db):
    assert core.ensure_key_table(db, "customer", ADMIN) is True
    assert core.ensure_key_table(db, "customer", ADMIN) is False


def test_a_setting_never_written_reads_as_empty_text(db):
    assert core._setting(db, "nothing_here") == ""
    assert core.keys_state(db) == {"live": False, "staging": False, "closed": ""}


class _Tsql(core.Db):
    """A T-SQL platform that only records what it's sent: an empty keys table, nothing else read."""

    def __init__(self):
        super().__init__(None, "tsql")
        self.sent = []

    def run(self, sql, params=()):
        self.sent.append((sql, list(params)))

    def rows(self, sql, params=()):
        return []

    def commit(self):
        pass


def test_keys_copied_into_sql_server_go_500_rows_a_statement_then_reseed(monkeypatch):
    for name in ("require_admin", "_require_open", "ensure_key_table", "_audit"):
        monkeypatch.setattr(core, name, lambda *a, **k: None)
    t = _Tsql()
    rows = [[n, f"bk{n}", "2026-01-01"] for n in range(1, 1002)]
    assert core.import_keys(t, "customer", rows, ADMIN) == {"imported": 1001, "already_there": 0}
    inserts = [p for sql, p in t.sent if "INSERT INTO" in sql]
    assert [len(p) // 3 for p in inserts] == [500, 500, 1]
    assert all(len(p) <= 2100 for p in inserts)
    assert inserts[1][:3] == [501, "bk501", "2026-01-01"]
    assert t.sent[-1][0] == "DBCC CHECKIDENT ('keys.customer', RESEED, 1001)"


# --------------------------------------------------------------------------- catalog


def test_a_project_in_a_domain_nobody_made_is_refused(db):
    with pytest.raises(core.PlatformError, match="domain sales isn't one of your organization's domains"):
        core.register_project(db, {"name": "crm", "domain": "sales"}, ADMIN)
    assert core.list_projects(db) == []


def test_product_status_is_added_once_and_says_what_it_added(db):
    db.run(f"DROP TABLE {db.t('catalog', 'product_status')}")
    assert core.ensure_product_status(db) == ["catalog.product_status"]
    assert core.ensure_product_status(db) == []


def test_a_promise_of_only_a_time_keeps_the_time_for_prod():
    assert core._promise({"by": "06:00"}) == {"by": "06:00", "envs": ["prod"]}
    assert core._promise({"within_hours": 4, "by": ""}) == {"within_hours": 4.0, "envs": ["prod"]}


def test_saving_a_term_answers_with_what_was_saved(db):
    row = core.save_term(db, {"term": "churn", "definition": "Customers lost"}, ADMIN)
    assert (row["term"], row["definition"], row["updated_by"]) == ("churn", "Customers lost", ADMIN)
    assert [t["term"] for t in core.list_glossary(db)] == ["churn"]


def test_taking_access_away_forgets_its_last_review_too(db):
    core._upsert(db, "catalog", "product", "id", {"id": "crm/customers", "project": "crm", "name": "customers", "owner": ADMIN})
    for project in ("hr", "fin"):
        core._upsert(db, "catalog", "grant", "id", {"id": f"crm/customers|{project}", "product_id": "crm/customers",
                                                     "project": project, "granted_by": ADMIN, "granted_at": core._now()})
    core.confirm_access(db, "crm/customers", ["hr", "fin"], ADMIN)
    assert _count(db, "grant_review") == 2
    assert [g["project"] for g in core.revoke_grant(db, "crm/customers", "hr", ADMIN)] == ["fin"]
    assert [r["id"] for r in db.rows(f"SELECT id FROM {db.t('catalog', 'grant_review')}")] == ["crm/customers|fin"]


def test_a_promise_for_environments_not_given_as_a_list_of_dev_test_prod_is_refused():
    for envs in ({"prod"}, ["uat"], ["prod", "uat"]):
        with pytest.raises(core.PlatformError, match="envs list some of dev, test, prod"):
            core._promise({"within_hours": 4, "envs": envs})


def test_a_source_whose_connections_arent_one_table_per_environment_is_refused():
    with pytest.raises(core.PlatformError, match="connections is one set of fields per environment"):
        core._source_row({"name": "erp", "connections": ["dev"]}, ADMIN)


def test_a_project_with_its_own_row_is_in_the_catalog(db):
    core.register_project(db, {"name": "crm"}, ADMIN)
    assert core._in_catalog(db, "crm") is True
    assert core._in_catalog(db, "hr") is False


def test_joining_a_subdomain_of_another_domain_is_refused_before_anyone_decides(db):
    core.save_domain(db, {"id": "sales", "name": "Sales"}, ADMIN)
    core.save_domain(db, {"id": "people", "name": "People"}, ADMIN)
    core.save_domain(db, {"id": "payroll", "name": "Payroll", "parent_id": "people"}, ADMIN)
    core.register_project(db, {"name": "crm"}, ADMIN)
    with pytest.raises(core.PlatformError, match="payroll isn't a subdomain of sales"):
        core.request(db, "join_domain", "crm", project="crm", detail={"domain": "sales", "subdomain": "payroll"}, by=ADMIN)
    assert _count(db, "request") == 0


def test_two_workspaces_are_two_requests_and_one_in_another_case_is_the_same(db):
    def ask(name):
        return core.request(db, "workspace", name, reason="Reports", detail={"admins": ADMIN}, by=ADMIN)

    ask("Sales Reports")
    ask("HR Reports")
    with pytest.raises(core.PlatformError, match="already a request waiting for sales reports"):
        ask("sales reports")
    assert _count(db, "request") == 2


def test_access_to_a_workspace_for_two_people_is_two_requests_and_the_same_person_twice_is_one(db):
    ws = "0f8fad5b-d9cb-469f-a165-70867728950e"

    def ask(who, role="Viewer"):
        return core.request(db, "workspace_access", "Sales", detail={"workspace_id": ws, "role": role}, by=who)

    ask("rae@example.com")
    ask("dana@example.com")
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        ask("rae@example.com", "Member")
    assert _count(db, "request") == 2


def test_a_scan_that_no_longer_confirms_an_item_takes_its_badge_away(db):
    core.record_endorsements(db, [{"item_id": "AAA", "endorsement": "Certified"}, {"item_id": "bbb", "endorsement": "Promoted"}], ADMIN)
    out = core.record_endorsements(db, [], ADMIN, cleared=["aaa", "missing", "", "x" * (core.MAX_BK + 1)])
    assert (out["recorded"], out["cleared"]) == (0, 1)
    assert [r["item_id"] for r in db.rows(f"SELECT item_id FROM {db.t('catalog', 'endorsement')}")] == ["bbb"]


def test_closing_and_reopening_keys_are_told_apart_on_the_audit(db):
    core.close_keys(db, "Moving to Fabric", ADMIN)
    assert core.audit(db, 1)[0]["action"] == "close keys"
    core.close_keys(db, "", ADMIN)
    assert core.audit(db, 1)[0]["action"] == "reopen keys"


def test_a_subdomain_of_a_domain_nobody_made_is_refused(db):
    with pytest.raises(core.PlatformError, match="parent domain people doesn't exist"):
        core.save_domain(db, {"id": "payroll", "name": "Payroll", "parent_id": "people"}, ADMIN)
    assert core.list_domains(db) == []


def test_a_change_window_starts_before_it_ends():
    window = {"days": ["mon"], "start": "08:00", "end": "16:00"}
    assert core.checked_change_window(window)["start"] == "08:00"
    for start, end in (("16:00", "08:00"), ("08:00", "08:00")):
        with pytest.raises(core.PlatformError, match="start first"):
            core.checked_change_window(dict(window, start=start, end=end))


def test_a_workload_action_keeps_what_the_app_reported_on_the_audit(db):
    core.log_workload_action(db, "sales", "run", "succeeded", {"id": "r1", "env": "prod", "other": "x"}, ADMIN)
    assert core.audit(db, 1)[0]["detail"] == '{"id": "r1", "env": "prod"}'
    core.log_workload_action(db, "sales", "run", "failed", {}, ADMIN)
    assert core.audit(db, 1)[0]["detail"] is None


def test_access_for_a_group_is_asked_by_its_object_id(db):
    ws, group = "0f8fad5b-d9cb-469f-a165-70867728950e", "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    req = core.request(db, "workspace_access", "Sales", by=ADMIN,
                       detail={"workspace_id": ws, "role": "Viewer", "who": "group", "group_id": group, "group_name": "Sales readers"})
    assert req["detail"]["principal"] == {"type": "Group", "id": group, "email": "", "name": "Sales readers"}
    with pytest.raises(core.PlatformError, match="A group is given by its object ID"):
        core.request(db, "workspace_access", "Sales", by=ADMIN,
                     detail={"workspace_id": ws, "role": "Viewer", "who": "group", "group_id": "rae@example.com"})


PORT = [{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": {"id": "int64"}}]


def _publish(db, **product):
    product = {"project": "crm", "name": "customers", "access": "public", "status": "published", "version": "1",
               "owner": ADMIN} | product
    return core.register_product(db, product, PORT, "prod", ADMIN)


def test_a_shortcuts_definition_keeps_its_target():
    target = {"oneLake": {"workspaceId": "w", "itemId": "i", "path": "Tables/x"}}
    assert core._definition({"path": "/Tables/", "name": "x", "target": target})["target"] == target
    assert core._definition({"path": "/Tables/", "name": "x"}) == {"workspace_id": None, "lakehouse_id": None,
                                                                   "path": "Tables", "name": "x", "target": {}}


def test_an_accepted_risk_says_who_accepted_it():
    row = {"shortcut_id": "s1", "kind": "external", "reason": "Known", "accepted_by": ADMIN, "accepted_at": "2026-01-01"}
    assert core._risk_out(row) == {"shortcut_id": "s1", "kind": "external", "reason": "Known", "by": ADMIN, "at": "2026-01-01"}


def test_a_deprecation_date_is_kept_for_the_environment_it_was_published_to(db):
    _publish(db, status="deprecated", deprecated_on="2027-01-31")
    [row] = db.rows(f"SELECT * FROM {db.t('catalog', 'product_env')}")
    assert (row["id"], row["status"], row["deprecated_on"]) == ("crm/customers:prod", "deprecated", "2027-01-31")


def test_a_status_report_holds_at_most_500_products(db):
    assert core.report_product_status(db, "crm", "prod", [{"product": f"p{n}"} for n in range(500)], ADMIN) == []
    with pytest.raises(core.PlatformError, match="at most 500 products"):
        core.report_product_status(db, "crm", "prod", [{"product": f"p{n}"} for n in range(501)], ADMIN)


def test_a_saved_change_window_reads_back_and_clearing_it_leaves_none(db):
    window = {"days": ["mon", "tue"], "start": "08:00", "end": "16:00", "time_zone": "UTC", "envs": ["prod"]}
    assert core.save_change_window(db, window, ADMIN) == {"window": window}
    assert core.change_window(db) == {"window": window}
    assert core.save_change_window(db, None, ADMIN) == {"window": None}
    assert core.change_window(db) == {"window": None}
    assert core._setting(db, "change_window") == ""


def test_sources_moved_in_say_which_project_they_came_from(db):
    core.adopt_sources(db, [{"code": "ERP", "id": 7}], "crm", ADMIN)
    assert (core.audit(db, 1)[0]["action"], core.audit(db, 1)[0]["target"]) == ("move sources", "crm")
    core.adopt_sources(db, [{"code": "CRM", "id": 8}], by=ADMIN)
    assert core.audit(db, 1)[0]["target"] == "a project"


def test_asking_for_a_change_to_a_product_names_its_owner(db):
    _publish(db, owner="chris@example.com")
    req = core.request(db, "data", "crm/customers", project="hr", reason="Add the region", detail={"what": "change"}, by=ADMIN)
    assert (req["detail"]["owners"], req["detail"]["product_name"]) == ("chris@example.com", "customers")


def test_asking_for_a_source_the_landing_zone_has_is_refused(db):
    core.save_source(db, {"name": "erp"}, ADMIN)
    with pytest.raises(core.PlatformError, match="erp is already a source in the landing zone"):
        core.request(db, "source", "ERP", reason="Orders", by=ADMIN)
    assert _count(db, "request") == 0


def test_a_shortcut_into_a_product_is_decided_by_that_products_owner(db):
    _publish(db, owner="chris@example.com")
    req = {"kind": "shortcut", "target": "x", "detail": {"product_id": "crm/customers"}}
    assert core._deciders(db, req) == {"chris@example.com"}
    assert core._deciders(db, dict(req, detail={"product_id": "crm/none"})) == set()


def test_two_shortcut_targets_with_their_fields_in_another_order_are_the_same_target():
    assert core._canon({"b": 1, "a": {"y": 2, "x": 3}}) == core._canon({"a": {"x": 3, "y": 2}, "b": 1}) == '{"a": {"x": 3, "y": 2}, "b": 1}'


def test_shortcut_rules_take_what_was_saved(db):
    out = core.save_shortcut_rules(db, {"who_creates": "admins"}, ADMIN)
    assert out == dict(core.DEFAULT_SHORTCUT_RULES, who_creates="admins")
    with pytest.raises(core.PlatformError, match="nope isn't a shortcut rule"):
        core.save_shortcut_rules(db, {"nope": 1}, ADMIN)


def test_a_group_naming_pattern_needs_the_domain_and_the_role():
    assert core.checked_entra_naming({"pattern": "{PREFIX}-{DOMAIN}-{ROLE}"})["pattern"] == "{PREFIX}-{DOMAIN}-{ROLE}"
    for pattern in ("{PREFIX}-{DOMAIN}", "{ROLE}-only", "{DOMAIN} {ROLE}"):
        with pytest.raises(core.PlatformError, match="The pattern needs"):
            core.checked_entra_naming({"pattern": pattern})


def test_a_source_is_active_unless_it_says_it_isnt():
    assert [core._source_row({"name": "erp", **a}, ADMIN)["active"] for a in ({}, {"active": True}, {"active": False})] == ["1", "1", "0"]


def test_a_group_naming_case_is_upper_lower_or_as_typed():
    assert core.checked_entra_naming({"case": "lower"})["case"] == "lower"
    with pytest.raises(core.PlatformError, match="case is upper, lower or as_typed"):
        core.checked_entra_naming({"case": "title"})


def test_someone_is_seen_when_they_last_changed_something(db):
    assert core._last_seen(db) == {ADMIN: core.audit(db, 1)[0]["at"]}


def test_expected_hours_may_have_one_decimal_point():
    assert core._source_row({"name": "erp", "expected_hours": "1.5"}, ADMIN)["expected_hours"] == "1.5"
    for hours in ("1.2.3", "a day"):
        with pytest.raises(core.PlatformError, match="Expected every"):
            core._source_row({"name": "erp", "expected_hours": hours}, ADMIN)


def test_a_shortcut_added_from_the_app_is_in_the_inventory_under_its_path(db):
    shortcut = {"lakehouse_id": "lh", "workspace_id": "ws", "path": "/Tables/", "name": "orders"}
    core.log_shortcut_change(db, "added", shortcut, after={"target": {"b": 1, "a": 2}}, by=ADMIN)
    row = core._row(db, "shortcut", "id", "lh|Tables/orders")
    assert (row["path"], row["name"], row["target"]) == ("Tables", "orders", '{"a": 2, "b": 1}')


def test_a_platform_with_no_settings_table_yet_reads_every_setting_as_empty_text():
    bare = core.Db(sqlite3.connect(":memory:"), "sqlite")
    assert core._setting(bare, core.KEYS_CLOSED) == ""
    assert core.keys_state(bare) == {"live": False, "staging": False, "closed": ""}


def test_a_project_known_only_by_its_product_is_in_the_catalog(db):
    _publish(db)
    assert core._row(db, "project", "name", "crm") is None
    assert core._in_catalog(db, "crm") is True
