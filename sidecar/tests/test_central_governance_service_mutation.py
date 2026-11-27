"""Mutation batch 371: what central/governance_service.py passes on to the platform and hands back.

A stand-in platform records each call, so these tests see the arguments the service sends and the
answer it returns, without a SQLite platform or Fabric.
"""

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.governance_service import GovernanceMixin

TODAY = dt.date(2026, 11, 21)
HOOK = "https://contoso.webhook.office.com/hook"


class FakePlatform:
    def __init__(self, answers=None, fail=None):
        self.calls, self.answers, self.fail = [], dict(answers or {}), dict(fail or {})

    def call(self, op, **kw):
        self.calls.append((op, kw))
        if op in self.fail:
            raise self.fail[op]
        answer = self.answers.get(op, {"op": op})
        return answer(**kw) if callable(answer) else answer


class Svc(GovernanceMixin):
    def __init__(self, platform, project=None, user="", routes=None, notify=None):
        self.p, self.project, self.user, self.routes = platform, project, user, routes or {}
        self.notify = notify or (lambda events: "")
        self.sent = []

    def _catalog(self):
        return self.p

    def _project(self):
        if self.project is None:
            raise LookupError("no project open")
        return self.project

    def _user(self):
        return self.user

    def _pn_raw(self):
        return self.routes

    def _governance_notify(self, make):
        events = make()
        self.sent.append(events)
        return self.notify(events)

    def platform_whoami(self):
        return {"user": self.user}


def project(name="crm", domain="sales", subdomain="emea", entities=None):
    return SimpleNamespace(info={"name": name, "domain": domain, "subdomain": subdomain},
                           root=Path("/nowhere") / name, entities=entities or {})


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(core, "_today", lambda: TODAY)


# --------------------------------------------------------------------------- pass-through calls


@pytest.mark.parametrize("method, args, op, sent", [
    ("governance_version", ("p1", 3), "policy_version", {"id": "p1", "version": 3}),
    ("governance_create", ({"id": "p1"},), "create_policy", {"policy": {"id": "p1"}}),
    ("governance_save_draft", ("p1", {"id": "p1"}, "why"), "save_policy_draft", {"id": "p1", "policy": {"id": "p1"}, "note": "why"}),
    ("governance_discard_draft", ("p1",), "discard_policy_draft", {"id": "p1"}),
    ("governance_publish", ("p1", "v1"), "publish_policy", {"id": "p1", "note": "v1"}),
    ("governance_set_status", ("p1", "retired", "old"), "set_policy_status", {"id": "p1", "status": "retired", "note": "old"}),
    ("governance_applicability", ("p1",), "policy_applicability", {"id": "p1"}),
])
def test_each_policy_call_returns_the_platforms_answer(method, args, op, sent):
    p = FakePlatform({op: {"answer": op}})
    assert getattr(Svc(p), method)(*args) == {"answer": op}
    assert p.calls == [(op, sent)]


def _policy_answers(**extra):
    return {"get_policy": lambda id: {"id": id}, "list_policy_exceptions": [], **extra}


def test_assigning_sends_the_settings_or_none_and_returns_the_policy_again():
    p = FakePlatform(_policy_answers())
    assert Svc(p).governance_assign("p1", "project", "crm") == {"id": "p1", "exceptions": []}
    assert p.calls[0] == ("save_assignment", {"policy": "p1", "scope": "project", "target": "crm", "settings": {}, "note": ""})
    Svc(p).governance_assign("p1", "domain", "sales", {"enforcement": "block"}, "tighter")
    assert ("save_assignment", {"policy": "p1", "scope": "domain", "target": "sales",
                                "settings": {"enforcement": "block"}, "note": "tighter"}) in p.calls


def test_unassigning_returns_the_policy_again():
    p = FakePlatform(_policy_answers())
    assert Svc(p).governance_unassign("p1", "project", "crm") == {"id": "p1", "exceptions": []}
    assert p.calls[0] == ("remove_assignment", {"policy": "p1", "scope": "project", "target": "crm"})


def test_a_policy_shows_problems_only_when_its_exceptions_cant_be_read():
    assert "problems" not in Svc(FakePlatform(_policy_answers())).governance_policy("p1")
    older = FakePlatform(_policy_answers(), fail={"list_policy_exceptions": LookupError("no such function")})
    out = Svc(older).governance_policy("p1")
    assert out["exceptions"] == [] and len(out["problems"]) == 1 and "older than this app" in out["problems"][0]


# --------------------------------------------------------------------------- exceptions and when they end


def _rows(*rows):
    return FakePlatform({"list_policy_exceptions": [dict(r) for r in rows]})


def test_an_ended_exception_is_not_active_even_when_the_row_says_active():
    rows, _ = Svc(_rows({"id": "e1", "active": True, "ended_at": "2026-11-01", "until": "2026-11-25"}))._exceptions()
    assert rows[0]["active"] is False and rows[0]["days_left"] is None and rows[0]["ending"] is False


def test_an_end_date_with_a_time_still_counts_its_days():
    rows, _ = Svc(_rows({"id": "e1", "active": True, "until": "2026-11-25T23:59:00Z"}))._exceptions()
    assert rows[0]["days_left"] == 4 and rows[0]["ending"] is True


def test_an_exception_ending_exactly_at_the_warning_days_is_ending():
    rows, _ = Svc(_rows({"id": "e1", "active": True, "until": "2026-12-05"},
                        {"id": "e2", "active": True, "until": "2026-12-06"}))._exceptions()
    assert [(r["days_left"], r["ending"]) for r in rows] == [(14, True), (15, False)]


# --------------------------------------------------------------------------- resolving


def test_resolving_sends_the_environment_only_when_one_is_named():
    p = FakePlatform({"resolve_policies": {"policies": []}})
    Svc(p).governance_resolve(project="crm", environment="prod")
    Svc(p).governance_resolve(project="crm")
    assert [kw["context"] for _, kw in p.calls] == [{"environment": "prod"}, {}]


def test_a_named_domain_wins_over_the_open_projects_and_a_missing_one_takes_it():
    p = FakePlatform({"resolve_policies": {"policies": []}})
    s = Svc(p, project=project())
    s.governance_resolve(domain="finance", subdomain="ap")
    s.governance_resolve()
    assert [(kw["domain"], kw["subdomain"]) for _, kw in p.calls] == [("finance", "ap"), ("sales", "emea")]


def test_an_entity_the_open_project_doesnt_have_adds_no_layer():
    p = FakePlatform({"resolve_policies": {"policies": []}})
    s = Svc(p, project=project(entities={"gold.sales": SimpleNamespace(layer="gold")}))
    assert s.governance_resolve(entity="silver.nope")["problems"] == []
    s.governance_resolve(entity="gold.sales")
    assert [kw["context"] for _, kw in p.calls] == [{}, {"layer": "gold"}]


# --------------------------------------------------------------------------- exceptions about to end


def _ending(**row):
    return {"id": "e1", "policy_id": "p1", "active": True, "until": "2026-11-25", **row}


def test_for_you_marks_the_asker_the_open_project_and_its_domains_only():
    rows = [_ending(id="mine", requested_by=" Ana@Contoso.com "), _ending(id="here", scope="project", target="crm"),
            _ending(id="other-project", scope="project", target="ledger"), _ending(id="sub", scope="domain", target="emea"),
            _ending(id="dom", scope="domain", target="sales"), _ending(id="far", scope="domain", target="finance"),
            _ending(id="project-named-like-a-domain", scope="project", target="sales"),
            _ending(id="domain-named-like-the-project", scope="domain", target="crm")]
    out = Svc(_rows(*rows), project=project(), user="ana@contoso.com").governance_exceptions_ending()
    assert {r["id"]: r["for_you"] for r in out["ending"]} == {
        "mine": True, "here": True, "other-project": False, "sub": True, "dom": True, "far": False,
        "project-named-like-a-domain": False, "domain-named-like-the-project": False}


def test_no_user_and_no_open_project_marks_nothing_for_you():
    out = Svc(_rows(_ending(requested_by=""), _ending(id="e2", scope="project", target=""))).governance_exceptions_ending()
    assert [r["for_you"] for r in out["ending"]] == [False, False]


def test_exceptions_ending_has_no_problems_when_the_platform_answers():
    out = Svc(_rows(_ending())).governance_exceptions_ending()
    assert out["problems"] == [] and out["warn_days"] == 14


def _routed(claimed, **kw):
    answers = {"list_policy_exceptions": [_ending(scope="domain", target="sales")],
               "claim_exception_warning": claimed}
    return Svc(FakePlatform(answers), routes={"routes": [{"name": "default", "url": HOOK}]}, **kw)


def test_a_claimed_notice_is_sent_and_a_notify_problem_is_reported():
    s = _routed({"claimed": True}, notify=lambda events: "Teams didn't answer")
    out = s.governance_exceptions_ending()
    assert len(s.sent) == 1 and s.sent[0][0]["dedupe"] == "exception.ending:e1:2026-11-25"
    assert out["problems"] == ["Teams didn't answer"]


def test_a_sent_notice_with_nothing_to_say_adds_no_problem():
    s = _routed({"claimed": True})
    assert s.governance_exceptions_ending()["problems"] == [] and len(s.sent) == 1


def test_an_unclaimed_notice_is_left_to_the_pc_that_claimed_it():
    s = _routed({"claimed": False})
    s.governance_exceptions_ending()
    assert s.sent == []


def test_quiet_hours_saved_with_seconds_still_let_the_notice_be_claimed():
    s = Svc(FakePlatform({"list_policy_exceptions": [_ending()], "claim_exception_warning": {"claimed": True}}),
            routes={"routes": [{"name": "default", "url": HOOK}], "quiet": "22:00:00-06:00:30"})
    s.governance_exceptions_ending()
    assert len(s.sent) == 1
