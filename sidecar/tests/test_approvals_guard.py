"""Teams ask, admins approve: no way around it. Every operation that approves, declines, withdraws or
gives or takes away a right is listed here, is a role write (test_central_roles checks each one refuses
anyone without the role and leaves an audit row), and is fail-closed: when its audit row can't be
written, the decision doesn't land. Two people deciding the same request at the same moment decide it
once. SQLite only; no tenant calls."""

import re
import sqlite3
import threading
from contextlib import closing

import pytest

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations to core.OPERATIONS)
from fabric_desktop.central.client import LocalPlatform

from test_central_roles import ANYONE, NOT_AUDITED, ROLE_WRITES, _copy, _db, _dump, seeded  # noqa: F401

ADMIN, OTHER_ADMIN, DEV = "ada@example.com", "al@example.com", "dev@example.com"

# Every approve or deny path, and what it decides.
APPROVALS = {
    "decide_request": "approves or declines a request",
    "withdraw_request": "takes a waiting request back",
    "record_request_progress": "records what approving a request made in Fabric",
    "revoke_grant": "takes a project's access to a product away",
    "confirm_access": "confirms projects still need a product",
    "accept_shortcut_risk": "accepts a flagged shortcut on purpose",
    "withdraw_shortcut_risk": "takes that acceptance back",
    "end_policy_exception": "ends an approved policy exception",
    "add_admin": "makes someone a platform admin",
    "remove_admin": "takes a platform admin's role away",
    "claim_admin": "makes the first platform admin, only while there is none",
    "set_project_member": "adds a project member or owner, or takes one off",
    "set_template_authors": "says who may write policy templates",
}
# Names that look like one but decide nothing, each with why.
NOT_APPROVALS = {"claim_exception_warning": "one PC's claim to post an exception's ending notice once"}
DECIDES = re.compile(r"decide|approv|withdraw|revoke|grant|confirm|accept|admin|member|author|exception")


def test_every_approve_or_deny_path_is_listed():
    found = {op for op in core.OPERATIONS if DECIDES.search(op) and op not in _reads() and op not in NOT_APPROVALS}
    assert sorted(found - set(APPROVALS)) == [], "a new approve or deny path: list it in APPROVALS"
    assert sorted(set(APPROVALS) - set(core.OPERATIONS)) == []


def _reads():
    from test_central_roles import READS
    return set(READS)


def test_each_one_needs_a_role_and_is_audited():
    roles = {op for op, _who, _args in ROLE_WRITES}
    assert sorted(set(APPROVALS) - roles - {"claim_admin"}) == [], "an approve or deny path anyone may call"
    assert "claim_admin" not in ANYONE or "no admin" in ANYONE["claim_admin"].lower()
    assert sorted(set(APPROVALS) & set(NOT_AUDITED)) == []


def _no_audit(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("""CREATE TRIGGER no_audit BEFORE INSERT ON "catalog__audit"
                        BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")


def _drop_no_audit(dump):
    return [line for line in dump if "no_audit" not in line]


@pytest.mark.parametrize("op,allowed,args", [r for r in ROLE_WRITES if r[0] in APPROVALS], ids=lambda v: v if isinstance(v, str) else "")
def test_a_decision_whose_audit_row_cant_be_written_doesnt_land(seeded, tmp_path, op, allowed, args):  # noqa: F811
    path = _copy(seeded, tmp_path)
    db = _db(path)
    kwargs = args(db)
    db.conn.close()
    _no_audit(path)
    before = _dump(path)
    with pytest.raises(sqlite3.DatabaseError, match="audit unavailable"):
        LocalPlatform(path, lambda: allowed).call(op, **kwargs)
    assert _dump(path) == before


# ---- the same request decided twice at once


def _platform(tmp_path):
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("add_admin", user=OTHER_ADMIN)
    req = LocalPlatform(admin.path, lambda: DEV).call("request", kind="source", target="erp", reason="needed")
    return admin.path, req["id"]


def _at_once(path, calls):
    """Run each (user, op, kwargs) on its own platform connection, all released together."""
    start, results = threading.Barrier(len(calls)), [None] * len(calls)

    def run(i, user, op, kwargs):
        p = LocalPlatform(path, lambda: user)
        start.wait(10)
        try:
            results[i] = p.call(op, **kwargs)
        except core.PlatformError as exc:
            results[i] = exc

    threads = [threading.Thread(target=run, args=(i, *c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def _decisions(path, rid):
    p = LocalPlatform(path, lambda: ADMIN)
    rows = [a for a in p.call("audit", limit=1000) if a["target"] == "erp" and not a["action"].startswith(("request ", "refused "))]
    return sorted(a["action"] for a in rows)


@pytest.mark.parametrize("run", range(5))
def test_two_admins_approving_the_same_request_at_once_approve_it_once(tmp_path, run):
    path, rid = _platform(tmp_path)
    results = _at_once(path, [(ADMIN, "decide_request", {"id": rid, "approve": True}),
                              (OTHER_ADMIN, "decide_request", {"id": rid, "approve": True})])
    assert sum(isinstance(r, core.PlatformError) for r in results) == 1
    assert "already" in str(next(r for r in results if isinstance(r, core.PlatformError)))
    assert _decisions(path, rid) == ["approved source"]
    assert [s["name"] for s in LocalPlatform(path, lambda: ADMIN).call("list_sources")] == ["erp"]


@pytest.mark.parametrize("run", range(5))
def test_an_approval_and_a_decline_at_once_leave_one_decision(tmp_path, run):
    path, rid = _platform(tmp_path)
    results = _at_once(path, [(ADMIN, "decide_request", {"id": rid, "approve": True}),
                              (OTHER_ADMIN, "decide_request", {"id": rid, "approve": False}),
                              (DEV, "withdraw_request", {"id": rid})])
    assert sum(not isinstance(r, core.PlatformError) for r in results) == 1
    [decision] = _decisions(path, rid)
    status = {"approved source": "approved", "declined source": "declined", "withdrew source": "withdrawn"}[decision]
    [req] = [r for r in LocalPlatform(path, lambda: ADMIN).call("list_requests") if r["id"] == rid]
    assert req["status"] == status
    sources = [s["name"] for s in LocalPlatform(path, lambda: ADMIN).call("list_sources")]
    assert sources == (["erp"] if status == "approved" else [])


def test_deciding_a_decided_request_is_refused_and_changes_nothing(tmp_path):
    path, rid = _platform(tmp_path)
    LocalPlatform(path, lambda: ADMIN).call("decide_request", id=rid, approve=False)
    before = _drop_no_audit(_dump(path))
    with pytest.raises(core.PlatformError, match="already declined"):
        LocalPlatform(path, lambda: OTHER_ADMIN).call("decide_request", id=rid, approve=True)
    after = [line for line in _dump(path) if "refused decide_request" not in line]
    assert after == before
