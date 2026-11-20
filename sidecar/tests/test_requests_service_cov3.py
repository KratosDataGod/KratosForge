"""central/requests_service.py: an older platform's refusals, Fabric or the catalog that can't be read
while a request is checked or closed, and a domain request retried after it was recorded. Fakes only."""

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import requests_service as rs


class Refuses:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kw):
        raise self.exc


def test_an_older_platform_that_doesnt_know_the_kind_says_to_repair():
    with pytest.raises(LookupError, match="UDF_Platform is older than this app"):
        rs.call_or_repair(Refuses(core.PlatformError("kind must be one of access, data")), "request", "take this request")
    with pytest.raises(LookupError, match="can't review access yet"):
        rs.call_or_repair(Refuses(core.PlatformError("access_reviews: no operation of that name")), "access_reviews",
                          "review access")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        rs.call_or_repair(Refuses(core.PlatformError("Only platform admins can do that")), "request", "x")


class Probe(rs.RequestsMixin):
    def __init__(self, fabric=None, domains=None, admin=None):
        self.fabric, self._domains, self._admin, self.progress = fabric, domains, admin, []

    def platform_domains(self):
        if isinstance(self._domains, Exception):
            raise self._domains
        return self._domains

    def _fabric_admin(self):
        if isinstance(self._admin, Exception):
            raise self._admin
        return self._admin

    def _rq_progress(self, req, fields):
        self.progress.append(fields)


def test_requests_are_carried_out_as_the_signed_in_person():
    fabric = object()
    assert Probe(fabric=fabric)._rq_fabric() is fabric


def test_a_workspace_name_that_cant_be_checked_is_left_to_the_approver():
    class Offline:
        def list_workspaces(self):
            raise RuntimeError("offline")
    assert Probe(fabric=Offline())._rq_name_free("Sales") is None


def test_a_closed_domain_request_with_unreadable_domains_adds_no_note():
    probe = Probe(domains=RuntimeError("catalog offline"))
    out = {"id": "r1", "kind": "domain", "target": "sales", "detail": {}}
    assert probe._rq_closed(dict(out)) == out  # nothing is known to be left, so nothing is said


def test_a_retried_domain_request_already_recorded_is_left_as_it_is():
    probe = Probe(domains=[{"id": "sales", "name": "Sales"}])
    req = {"target": "sales", "detail": {"name": "Sales", "made": {"domain_id": "sales"}}}
    assert probe._rq_make_domain(req) == {}
    assert probe.progress == []


def test_a_found_fabric_domain_whose_maker_cant_be_read_is_recorded_as_found():
    probe = Probe(admin=RuntimeError("no admin rights"))
    req = {"id": "r1", "target": "sales", "detail": {"name": "Sales"}}
    probe._rq_domain_found(req, {"id": "sales", "fabric_domain": "fd-1"}, {})
    assert probe.progress == [{"domain_id": "sales", "fabric_domain_id": "fd-1", "found": True}]


def test_only_workspace_admins_count_as_admins():
    class Api:
        def list_role_assignments(self, ws):
            return [{"role": "Member", "principal": {"id": "m1", "userDetails": {"userPrincipalName": "lee@x.com"}}},
                    {"role": "Admin", "principal": {"id": "A1", "userDetails": {"userPrincipalName": "Ana@X.com"}}},
                    {"role": "Admin", "principal": {"id": "g1"}}]
    assert rs.RequestsMixin._rq_admins(Api(), "w") == {"a1", "ana@x.com", "g1"}
