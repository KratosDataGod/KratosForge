"""Who may change what on the central platform (#1033), held in one place.

core.py's and policy.py's docstrings each have a "Who may change what" table. Every operation the
platform exposes (core.OPERATIONS, the same list UDF_Platform serves) is listed here once: as a read,
as a write anyone may make (with the docstring line that allows it), or as a write with the role
it needs. For each role write, someone without the role is refused and the platform's database is
left byte for byte as it was, and someone with the role succeeds, so the refusal comes from the
role check and not from the arguments. An operation added without a line here fails the first test.
SQLite only; no tenant calls."""

import datetime as dt
import shutil
import sqlite3

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations to core.OPERATIONS)
from fabric_desktop.central.client import LocalPlatform

ADMIN = "ada@example.com"          # platform admin
DOMAIN_OWNER = "dora@example.com"  # owns the sales domain
MEMBER = "mo@example.com"          # owns the ledger project
PRODUCT_OWNER = "pat@example.com"  # owns crm/c360
REQUESTER = "rae@example.com"      # asked for a key table
SECOND_ADMIN = "al@example.com"    # a platform admin too, so one can be removed
NOBODY = "eve@example.com"         # signed in, no role anywhere

LH = "bbbbbbbb-0000-0000-0000-000000000002"
WS = "aaaaaaaa-0000-0000-0000-000000000001"
SM = "dddddddd-0000-0000-0000-000000000004"
GID = "cccccccc-0000-0000-0000-000000000003"
TERM_URL = "https://purview.microsoft.com/glossary/term/1"
POLICY = {"id": "table-names", "name": "Table names are snake_case", "category": "naming", "severity": "medium",
          "enforcement": "warn", "rule": "table_name_pattern",
          "params": {"pattern": {"value": "^[a-z][a-z0-9_]*$", "stricter": "fixed"}}}

# Operations that only read, with arguments for each that needs some. They take no caller, or take one
# only to shape what they show; a test runs each one as an admin and as someone with no role, and finds
# nothing changed.
# (list_policy_templates and the product term and item lists make their tables if they are missing, which
# changes nothing on a platform that has them.)
READS = {
    "audit": {}, "enrichment": {"project": "ledger"}, "entra_group_naming": {}, "export_keys": {"name": "customer"},
    "get_policy": {"id": "table-names"}, "key_stats": {"name": "customer"}, "keys_state": {}, "list_admins": {},
    "list_assignments": {}, "list_domains": {}, "list_entra_groups": {}, "list_glossary": {}, "list_grants": {},
    "list_key_tables": {}, "list_landing_zones": {}, "list_policies": {}, "list_policy_exceptions": {},
    "list_product_items": {"product_id": "crm/c360"}, "list_products": {}, "list_projects": {},
    "list_seen_shortcuts": {}, "list_shortcut_risks": {}, "list_sources": {},
    "lookup_key": {"name": "customer", "value": "UNKNOWN"}, "owner_coverage": {}, "policies_ready": {},
    "policy_applicability": {"id": "table-names"}, "policy_history": {"id": "table-names"},
    "policy_options": {}, "policy_snapshot": {}, "policy_version": {"id": "table-names", "version": 1},
    "product_history": {"product_id": "crm/c360"}, "resolve_policies": {"project": "ledger"},
    "resolve_port": {"product_id": "crm/c360", "port": "customers", "env": "prod"}, "shortcut_changes": {},
    "shortcut_rules": {}, "whoami": {}, "list_follows": {}, "product_followers": {"product_id": "crm/c360"},
    "list_requests": {}, "access_reviews": {}, "list_project_members": {"project": "ledger"},
    "product_trust": {"product_id": "crm/c360"}, "list_policy_templates": {},
}

# Writes anyone signed in may make, each with the docstring line that allows it.
ANYONE = {
    "ensure_schema": "Finish or repair: creates what is missing, changes no one's data",
    "ensure_policy_schema": "Finish or repair, as ensure_schema",
    "ensure_template_schema": "Finish or repair, as ensure_schema",
    "issue_keys": 'core.py: "everyone: ... issuing keys from existing key tables"',
    "register_project": "core.py register_project: \"the first to record a project owns it\" (later changes: members)",
    "register_link": 'core.py: "everyone: ... links" (refused when the product is not open to the project)',
    "save_enrichment": 'core.py: "everyone: ... catalog text"',
    "request": 'core.py: "everyone: ... requests"',
    "note_seen": "note_seen: records that the caller used the platform today",
    "call_once": "runs the named change under that change's own role check (pinned below); reads, repeatable "
                 "operations and call_once itself are refused",
    "follow_product": "a person follows a product for themselves",
    "unfollow_product": "a person stops following a product for themselves",
    "log_workload_action": "log_workload_action: the caller's own action, on the audit",
    "record_shortcuts": "a scan anyone may report; a removal or repoint applies only from an admin or a member "
                        "of a project with a product in that lakehouse, and is otherwise skipped or reported",
    "record_shortcut_health": "anyone may report a shortcut broken; any other result applies only from an "
                              "admin or a member of a project with a product in that lakehouse",
    "log_shortcut_change": "a shortcut change the caller made from the app",
    "claim_admin": "claim_admin: only while the platform has no admin",
    "claim_exception_warning": "policy.py: one PC claims an exception's ending notice so only one posts it",
}


def seed(path):
    """A platform with one of everything the role writes act on."""
    who = {"user": ADMIN}
    p = LocalPlatform(path, lambda: who["user"])  # ada is its first admin

    def as_(user, op, **kw):
        who["user"] = user
        try:
            return p.call(op, **kw)
        finally:
            who["user"] = ADMIN

    p.call("save_domain", domain={"id": "sales", "name": "Sales", "owners": DOMAIN_OWNER})
    p.call("ensure_key_table", name="customer")
    as_(MEMBER, "register_project", project={"name": "ledger", "domain": "sales"})
    as_(PRODUCT_OWNER, "register_project", project={"name": "crm", "domain": "sales"})
    port = {"port": "customers", "table": "dim_customer", "workspace_name": "CRM Prod", "lakehouse_name": "LH_Gold",
            "workspace_id": WS, "lakehouse_id": LH}
    as_(PRODUCT_OWNER, "register_product", product={"project": "crm", "name": "c360", "owner": PRODUCT_OWNER,
                                                     "access": "private", "status": "published"},
        ports=[port], env="prod")
    p.call("save_source", source={"name": "erp", "title": "ERP"})
    p.call("save_landing_zone", zone={"name": "Shared", "workspace_id": "ws-landing", "lakehouse_id": "lh-shared"})
    p.call("save_landing_zone", zone={"name": "Finance", "domain_id": "sales"})
    p.call("add_admin", user=SECOND_ADMIN)
    as_(REQUESTER, "request", kind="key_table", target="supplier", reason="Supplier dimension")
    access = as_(MEMBER, "request", kind="access", target="crm/c360", project="ledger", reason="Churn")
    as_(PRODUCT_OWNER, "decide_request", id=access["id"], approve=True)
    p.call("add_product_term", product_id="crm/c360", term="Customer", url=TERM_URL)
    p.call("add_product_item", product_id="crm/c360", item={"item_type": "SemanticModel", "item_id": SM,
                                                            "workspace_id": WS, "item_name": "SM"})
    p.call("link_entra_group", domain="sales", role="readers", env="prod", group_id=GID)
    p.call("create_policy", policy=POLICY)
    p.call("publish_policy", id="table-names")
    p.call("save_assignment", policy="table-names", scope="domain", target="sales", settings={})
    until = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    asked = as_(MEMBER, "request", kind="exception", target="table-names", project="ledger", reason="Legacy names",
                detail={"scope": "project", "scope_target": "ledger", "until": until})
    p.call("decide_request", id=asked["id"], approve=True)
    p.call("save_policy_draft", id="table-names", policy=dict(POLICY, severity="high"))
    p.call("save_policy_template", template=dict(POLICY, id="tpl-names"))
    shortcut = {"lakehouse_id": LH, "workspace_id": WS, "workspace_name": "CRM Dev", "lakehouse_name": "LH_Gold",
                "path": "Tables", "name": "orders", "target": {"oneLake": {"workspaceId": WS, "itemId": LH}}}
    lakehouse = {k: shortcut[k] for k in ("lakehouse_id", "workspace_id", "workspace_name", "lakehouse_name")}
    p.call("record_shortcuts", lakehouses=[lakehouse], shortcuts=[])
    p.call("record_shortcuts", lakehouses=[lakehouse], shortcuts=[shortcut])
    p.call("accept_shortcut_risk", shortcut_id=p.call("list_seen_shortcuts")[0]["id"], kind="cross_env", reason="on purpose")
    return p


def _requests(db, status="waiting"):
    return {r["kind"]: r["id"] for r in core.list_requests(db, status, ADMIN)}


def _shortcut(db):
    return core.list_seen_shortcuts(db)[0]["id"]


# Each role write: (operation, who may, arguments from the seeded database).
ROLE_WRITES = [
    ("ensure_key_table", ADMIN, lambda db: {"name": "supplier"}),
    ("import_keys", ADMIN, lambda db: {"name": "customer", "rows": [[7, "C-7", "2026-01-01"]]}),
    ("close_keys", ADMIN, lambda db: {"reason": "moving"}),
    ("stage_keys", ADMIN, lambda db: {"staging": True}),
    ("clear_keys", ADMIN, lambda db: core.stage_keys(db, True, by=ADMIN) and {}),
    ("save_domain", ADMIN, lambda db: {"domain": {"id": "hr", "name": "HR"}}),
    ("import_fabric_domains", ADMIN, lambda db: {"domains": [{"id": "f1", "displayName": "Finance"}]}),
    ("delete_domain", ADMIN, lambda db: {"did": "sales"}),
    ("register_product", PRODUCT_OWNER, lambda db: {
        "product": {"project": "crm", "name": "c360", "owner": PRODUCT_OWNER, "access": "private",
                    "status": "published", "description": "changed"},
        "ports": [{"port": "customers", "table": "dim_customer", "workspace_name": "CRM Prod",
                   "lakehouse_name": "LH_Gold", "workspace_id": WS, "lakehouse_id": LH}], "env": "prod"}),
    ("save_entra_group_naming", ADMIN, lambda db: {"naming": {"prefix": "kf"}}),
    ("link_entra_group", DOMAIN_OWNER, lambda db: {"domain": "sales", "role": "stewards", "env": "prod",
                                                   "group_id": GID}),
    ("unlink_entra_group", DOMAIN_OWNER, lambda db: {"domain": "sales", "role": "readers", "env": "prod"}),
    ("save_term", ADMIN, lambda db: {"term": {"term": "Active customer", "definition": "Bought this year"}}),
    ("add_admin", ADMIN, lambda db: {"user": NOBODY}),
    ("remove_admin", ADMIN, lambda db: {"user": SECOND_ADMIN}),
    ("save_source", ADMIN, lambda db: {"source": {"name": "crm", "title": "CRM"}}),
    ("adopt_sources", ADMIN, lambda db: {"systems": [{"id": 7, "code": "WMS", "name": "wms"}]}),
    ("delete_source", ADMIN, lambda db: {"name": "erp"}),
    ("save_landing_zone", ADMIN, lambda db: {"zone": {"name": "People", "domain_id": "sales"}}),
    ("switch_landing_reads", ADMIN, lambda db: {}),
    ("delete_landing_zone", ADMIN, lambda db: {"id": core.list_landing_zones(db)[-1]["id"]}),
    ("set_project_member", MEMBER, lambda db: {"project": "ledger", "user": NOBODY, "role": "member"}),
    ("record_request_progress", ADMIN, lambda db: {"id": _requests(db)["key_table"], "made": {"name": "supplier"}}),
    ("decide_request", ADMIN, lambda db: {"id": _requests(db)["key_table"], "approve": True}),
    ("withdraw_request", REQUESTER, lambda db: {"id": _requests(db)["key_table"]}),
    ("revoke_grant", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "project": "ledger"}),
    ("confirm_access", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "projects": ["ledger"]}),
    ("save_shortcut_rules", ADMIN, lambda db: {"rules": {"who_creates": "admins"}}),
    ("accept_shortcut_risk", ADMIN, lambda db: {"shortcut_id": _shortcut(db), "kind": "cross_env",
                                                "reason": "on purpose"}),
    ("withdraw_shortcut_risk", ADMIN, lambda db: {"shortcut_id": _shortcut(db), "kind": "cross_env"}),
    ("record_endorsements", ADMIN, lambda db: {"items": [{"item_id": SM, "endorsement": "Certified"}]}),
    ("add_product_term", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "term": "Churn", "url": TERM_URL}),
    ("remove_product_term", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "term": "Customer"}),
    ("add_product_item", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "item": {
        "item_type": "Report", "item_id": GID, "workspace_id": WS, "item_name": "Churn"}}),
    ("remove_product_item", PRODUCT_OWNER, lambda db: {"product_id": "crm/c360", "item_id": SM}),
    ("create_policy", ADMIN, lambda db: {"policy": dict(POLICY, id="other-names")}),
    ("save_policy_draft", ADMIN, lambda db: {"id": "table-names", "policy": dict(POLICY, severity="high")}),
    ("discard_policy_draft", ADMIN, lambda db: {"id": "table-names"}),
    ("publish_policy", ADMIN, lambda db: {"id": "table-names"}),
    ("set_policy_status", ADMIN, lambda db: {"id": "table-names", "status": "retired", "note": "gone"}),
    ("save_assignment", DOMAIN_OWNER, lambda db: {"policy": "table-names", "scope": "domain", "target": "sales",
                                                  "settings": {}}),
    ("remove_assignment", DOMAIN_OWNER, lambda db: {"policy": "table-names", "scope": "domain", "target": "sales"}),
    ("save_policy_template", ADMIN, lambda db: {"template": dict(POLICY, id="tpl-other")}),
    ("delete_policy_template", ADMIN, lambda db: {"id": "tpl-names"}),
    ("set_template_authors", ADMIN, lambda db: {"authors": "domain_owners"}),
    ("use_policy_template", ADMIN, lambda db: {"template": "tpl-names", "id": "from-tpl"}),
    ("save_policy_as_template", ADMIN, lambda db: {"policy": "table-names", "id": "tpl-x"}),
    ("end_policy_exception", ADMIN, lambda db: {"id": policy.list_policy_exceptions(db)[0]["id"], "reason": "Renamed"}),
]


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("central") / "platform.sqlite"
    seed(path)
    return path


def _copy(seeded, tmp_path):
    path = tmp_path / "platform.sqlite"
    shutil.copy(seeded, path)
    return path


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _db(path):
    return core.Db(sqlite3.connect(path), "sqlite")


def _unlisted(operations):
    """Operations with no line here, operations listed twice, and lines for operations that are gone."""
    listed = [*READS, *ANYONE, *(op for op, _, _ in ROLE_WRITES)]
    return (sorted(set(operations) - set(listed)), sorted(op for op in set(listed) if listed.count(op) > 1),
            sorted(set(listed) - set(operations)))


def test_every_operation_is_listed_once_as_a_read_an_open_write_or_a_role_write():
    assert _unlisted(core.OPERATIONS) == ([], [], []), "list each operation once in READS, ANYONE or ROLE_WRITES"
    assert all(reason.strip() for reason in ANYONE.values())


def test_a_new_operation_without_a_line_here_is_caught(monkeypatch):
    monkeypatch.setitem(core.OPERATIONS, "brand_new_operation", lambda db, by="": None)
    assert _unlisted(core.OPERATIONS)[0] == ["brand_new_operation"]


@pytest.mark.parametrize("op,allowed,args", ROLE_WRITES, ids=[r[0] for r in ROLE_WRITES])
def test_a_role_write_refuses_anyone_without_the_role_and_changes_nothing(seeded, tmp_path, op, allowed, args):
    path = _copy(seeded, tmp_path)
    db = _db(path)
    kwargs = args(db)
    db.conn.close()
    before = _dump(path)
    with pytest.raises(core.PlatformError):
        LocalPlatform(path, lambda: NOBODY).call(op, **kwargs)
    assert _dump(path) == before


@pytest.mark.parametrize("op,allowed,args", ROLE_WRITES, ids=[r[0] for r in ROLE_WRITES])
def test_a_role_write_succeeds_for_someone_with_the_role(seeded, tmp_path, op, allowed, args):
    path = _copy(seeded, tmp_path)
    db = _db(path)
    kwargs = args(db)
    db.conn.close()
    before = _dump(path)
    LocalPlatform(path, lambda: allowed).call(op, **kwargs)
    assert _dump(path) != before


def test_the_platform_s_own_lists_of_reads_and_repeatable_operations_match_these():
    assert core.READ_OPERATIONS == set(READS)
    assert core.REPEATABLE_OPERATIONS <= set(ANYONE)


@pytest.mark.parametrize("op,allowed,args", ROLE_WRITES, ids=[r[0] for r in ROLE_WRITES])
def test_a_role_write_sent_through_call_once_still_refuses_anyone_without_the_role(seeded, tmp_path, op, allowed, args):
    path = _copy(seeded, tmp_path)
    db = _db(path)
    kwargs = args(db)
    db.conn.close()
    before = _dump(path)
    with pytest.raises(core.PlatformError) as refused:
        LocalPlatform(path, lambda: NOBODY).call("call_once", call_id="0" * 32, operation=op, args=kwargs)
    assert not any(own in str(refused.value) for own in ("call id", "call_once", "never an argument")), \
        "refused by the operation's own role check, not by call_once"
    assert _dump(path) == before  # the change, and its call id too


@pytest.mark.parametrize("user", [ADMIN, NOBODY])
@pytest.mark.parametrize("op", sorted(READS))
def test_a_read_changes_nothing_whoever_calls_it(seeded, tmp_path, op, user):
    """As an admin too, so a write listed here by mistake changes the database and fails."""
    path = _copy(seeded, tmp_path)
    before = _dump(path)
    try:
        LocalPlatform(path, lambda: user).call(op, **READS[op])
    except core.PlatformError:
        pass  # silent-ok: a read some roles may not make (product_followers) changes nothing either way
    assert _dump(path) == before
