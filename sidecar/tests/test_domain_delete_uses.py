"""Deleting a domain refuses while anything still names it (#1310): before, only subdomains and
projects counted, so a landing zone, a policy assignment or exception, an Entra group link or a
waiting request could be left pointing at nothing, or at a domain made later with the same id."""

import datetime as dt

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations)
from fabric_desktop.central.client import LocalPlatform
from test_central_roles import POLICY

ADMIN, DEV = "ada@example.com", "dev@example.com"
GID = "11111111-2222-3333-4444-555555555555"
@pytest.fixture
def platform(tmp_path):
    who = {"user": ADMIN}
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: who["user"])
    p.call("save_domain", domain={"id": "hr", "name": "HR"})
    p.who = who
    return p


def refused(p, *names):
    with pytest.raises(core.PlatformError, match="hr is still used by") as err:
        p.call("delete_domain", did="hr")
    for name in names:
        assert name in str(err.value)
    assert "hr" in {d["id"] for d in p.call("list_domains")}


def test_an_unused_domain_deletes_and_is_audited(platform):
    platform.call("delete_domain", did="hr")
    assert "hr" not in {d["id"] for d in platform.call("list_domains")}
    assert [e["action"] for e in platform.call("audit", limit=5) if e["target"] == "hr"][0] == "delete domain"


def test_a_landing_zone_in_the_domain_keeps_it(platform):
    platform.call("save_landing_zone", zone={"name": "People", "domain_id": "hr"})
    refused(platform, "landing zone People")


def test_a_policy_assigned_to_the_domain_keeps_it(platform):
    platform.call("create_policy", policy=POLICY)
    platform.call("publish_policy", id="table-names")
    platform.call("save_assignment", policy="table-names", scope="domain", target="hr", settings={})
    refused(platform, "policy table-names")


def test_an_active_exception_for_the_domain_keeps_it(platform):
    platform.call("create_policy", policy=POLICY)
    platform.call("publish_policy", id="table-names")
    until = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    platform.who["user"] = DEV
    asked = platform.call("request", kind="exception", target="table-names", reason="Legacy",
                          detail={"scope": "domain", "scope_target": "hr", "until": until})
    platform.who["user"] = ADMIN
    refused(platform, "a waiting exception request")  # waiting, it names the domain too
    platform.call("decide_request", id=asked["id"], approve=True)
    refused(platform, "an exception to table-names")


def test_a_group_link_keeps_it(platform):
    platform.call("link_entra_group", domain="hr", role="readers", env="prod", group_id=GID)
    refused(platform, "the readers group link (prod)")


@pytest.mark.parametrize("kind, target, detail", [
    ("domain", "payroll", {"name": "Payroll", "parent_id": "hr"}),
    ("workspace", "HR Dev", {"name": "HR Dev", "capacity_id": GID, "domain_id": "hr", "admins": [DEV]}),
], ids=["subdomain", "workspace"])
def test_a_waiting_request_that_names_it_keeps_it(platform, kind, target, detail):
    platform.who["user"] = DEV
    platform.call("request", kind=kind, target=target, reason="Needed", detail=detail)
    platform.who["user"] = ADMIN
    refused(platform, f"a waiting {kind} request for {target}")


def test_a_platform_without_the_policy_tables_still_deletes_an_unused_domain(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: ADMIN)
    p.call("save_domain", domain={"id": "hr", "name": "HR"})
    db = core.Db(__import__("sqlite3").connect(tmp_path / "platform.sqlite"), "sqlite")
    for table in ("policy_assignment", "policy_exception", "entra_group", "landing_zone"):
        if db.exists("catalog", table):
            db.run(f"DROP TABLE {db.t('catalog', table)}")
    db.commit()
    db.conn.close()
    conn = __import__("sqlite3").connect(tmp_path / "platform.sqlite")
    try:
        assert core.delete_domain(core.Db(conn, "sqlite"), "hr", by=ADMIN) is None
    finally:
        conn.close()
