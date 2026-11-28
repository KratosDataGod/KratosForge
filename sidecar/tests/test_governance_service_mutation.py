"""central/governance_service.py paths that its first mutation run (batch 379,
docs/quality/mutation-testing.md) showed no test checked: what each Governance call hands back."""

from __future__ import annotations

import pytest

from fabric_desktop.service import Service
from tests.test_governance import NAMING
from tests.test_requests_core_four import NO_PII, ROUTE, TODAY, _same_day, as_user, svc, who  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def gov(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def test_each_policy_call_hands_back_what_the_platform_answers(gov):
    svc = gov
    created = svc.governance_create(NAMING)
    assert created["id"] == "table-names" and created["status"] == "draft"
    published = svc.governance_publish("table-names", "first")
    assert published["status"] == "published" and published["current_version"] == 1
    assert svc.governance_version("table-names", 1)["version"] == 1
    drafted = svc.governance_save_draft("table-names", dict(NAMING, severity="high"), "stricter")
    assert drafted["draft_version"] == 2
    assert svc.governance_discard_draft("table-names")["id"] == "table-names"
    assert "problems" not in svc.governance_policy("table-names")
    deprecated = svc.governance_set_status("table-names", "deprecated", "old")
    assert deprecated["status"] == "deprecated"
    assigned = svc.governance_assign("table-names", "project", "crm", {"enforcement": "block"})
    assert assigned["id"] == "table-names"
    assert [(a["scope"], a["target"]) for a in assigned["assignments"]] == [("project", "crm")]
    assert assigned["assignments"][0]["settings"] == {"enforcement": "block"}
    unassigned = svc.governance_unassign("table-names", "project", "crm")
    assert unassigned["id"] == "table-names" and unassigned["assignments"] == []
    assert isinstance(svc.governance_applicability("table-names"), dict)


def test_resolving_for_an_environment_applies_only_the_policies_for_it(gov):
    svc = gov
    svc.governance_create(dict(NAMING, applies_to={"environments": ["prod"]}))
    svc.governance_publish("table-names")
    svc.governance_assign("table-names", "enterprise")
    assert [p["id"] for p in svc.governance_resolve(environment="prod")["policies"]] == ["table-names"]
    assert svc.governance_resolve(environment="test")["policies"] == []


def ending(svc, who, monkeypatch, routing=None, **exception):
    """A published, enterprise-wide NO_PII policy and one granted exception ending in 10 days; the
    Teams events the notice sends go to the returned list."""
    import datetime as dt

    svc.governance_create(NO_PII)
    svc.governance_publish("no-pii-in-dev")
    svc.governance_assign("no-pii-in-dev", "enterprise")
    as_user(who, exception.pop("by", "dev@contoso.com"))
    detail = {"policy": "no-pii-in-dev", "scope": "project", "scope_target": "hr-analytics",
              "until": (TODAY + dt.timedelta(days=10)).isoformat()} | exception
    asked = svc.platform_request("exception", "", "A masked copy isn't ready yet", detail)
    as_user(who, "ana@contoso.com")
    svc.platform_decide(asked["id"], True)
    sent: list = []
    raw = routing or {"routes": [{"name": "default", "url": ROUTE}]}
    monkeypatch.setattr(type(svc), "_pn_raw", lambda self: raw)
    monkeypatch.setattr(type(svc), "platform_notify", lambda self, events: sent.extend(events) or {})
    return sent


def test_an_ending_exception_whose_notice_is_sent_reports_no_problem(svc, who, monkeypatch):
    sent = ending(svc, who, monkeypatch)
    out = svc.governance_exceptions_ending()
    assert [e["target"] for e in out["ending"]] == ["hr-analytics"] and len(sent) == 1
    assert out["problems"] == []


def test_quiet_hours_saved_with_seconds_by_an_older_build_still_let_the_notice_go(svc, who, monkeypatch):
    sent = ending(svc, who, monkeypatch, {"routes": [{"name": "default", "url": ROUTE}], "quiet": "22:00:00-07:00:00"})
    svc.governance_exceptions_ending()
    assert len(sent) == 1


def test_a_notice_another_pc_already_claimed_is_not_sent(svc, who, monkeypatch):
    from fabric_desktop.central import governance_service

    sent = ending(svc, who, monkeypatch)
    real = governance_service.call_or_repair

    def claimed_elsewhere(platform, method, *args, **kwargs):
        if method == "claim_exception_warning":
            return {"claimed": False}
        return real(platform, method, *args, **kwargs)

    monkeypatch.setattr(governance_service, "call_or_repair", claimed_elsewhere)
    assert svc.governance_exceptions_ending()["ending"] and sent == []


def test_a_domain_exception_is_for_you_only_when_it_covers_your_domain(svc, who, monkeypatch):
    svc.platform_save_domain({"name": "Finance"})
    ending(svc, who, monkeypatch, by="someone@contoso.com", scope="domain", scope_target="finance")
    as_user(who, "dev@contoso.com")
    [row] = svc.governance_exceptions_ending()["ending"]
    assert row["scope"] == "domain" and row["for_you"] is False


@pytest.mark.parametrize("scope, target", [("domain", "sales"), ("domain", "emea")])
def test_the_open_project_resolves_with_the_domain_and_subdomain_it_is_in(gov, scope, target):
    gov.platform_save_domain({"name": "Sales"})
    gov.platform_save_domain({"name": "EMEA", "parent_id": "sales"})
    # project.toml names the domain, and the platform hasn't registered the project: only what this PC
    # reads from the file says where it is.
    from fabric_desktop.meta import mesh
    mesh.write_project_file(gov._project().root, {"name": "crm", "domain": "sales", "subdomain": "emea"})
    gov.governance_create(NAMING)
    gov.governance_publish("table-names")
    gov.governance_assign("table-names", scope, target)
    assert [p["id"] for p in gov.governance_resolve()["policies"]] == ["table-names"]


def test_an_entity_the_open_project_does_not_have_resolves_without_a_layer(gov):
    gov.governance_create(NAMING)
    gov.governance_publish("table-names")
    gov.governance_assign("table-names", "enterprise")
    out = gov.governance_resolve(entity="gold.nothing_here")
    assert [p["id"] for p in out["policies"]] == ["table-names"] and out["problems"] == []


def _open(svc, tmp_path, name="hr-analytics", domain="sales"):
    from fabric_desktop.meta import mesh

    svc.meta_init(str(tmp_path / name), "3")
    mesh.write_project_file(svc._project().root, {"name": name, "domain": domain})


def test_a_project_exception_on_the_open_project_is_for_you_whoever_asked(svc, who, monkeypatch, tmp_path):
    _open(svc, tmp_path)
    ending(svc, who, monkeypatch, by="someone@contoso.com")
    [row] = svc.governance_exceptions_ending()["ending"]
    assert row["target"] == "hr-analytics" and row["for_you"] is True


def test_a_project_exception_named_like_your_domain_is_not_for_you(svc, who, monkeypatch, tmp_path):
    _open(svc, tmp_path)
    ending(svc, who, monkeypatch, by="someone@contoso.com", scope_target="sales")
    [row] = svc.governance_exceptions_ending()["ending"]
    assert (row["scope"], row["target"], row["for_you"]) == ("project", "sales", False)


def test_an_exception_ending_in_exactly_the_warning_days_is_ending(svc, who, monkeypatch):
    import datetime as dt

    from fabric_desktop.central.governance_service import EXCEPTION_WARN_DAYS

    ending(svc, who, monkeypatch, until=(TODAY + dt.timedelta(days=EXCEPTION_WARN_DAYS)).isoformat())
    [row] = svc.governance_exceptions_ending()["ending"]
    assert row["days_left"] == EXCEPTION_WARN_DAYS and row["ending"] is True


def test_an_exception_past_its_date_is_listed_as_ended_with_no_days_left(svc, who, monkeypatch):
    import datetime as dt

    from fabric_desktop.central import core, policy

    ending(svc, who, monkeypatch, until=(TODAY + dt.timedelta(days=2)).isoformat())
    later = TODAY + dt.timedelta(days=5)
    monkeypatch.setattr(core, "_today", lambda: later)
    monkeypatch.setattr(policy, "_today", lambda: later)
    [row] = svc.governance_policy("no-pii-in-dev", include_ended=True)["exceptions"]
    assert (row["active"], row["days_left"], row["ending"]) == (False, None, False)
