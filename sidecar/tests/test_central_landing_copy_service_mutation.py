"""Mutation batch 370: central/landing_copy_service.py with a stand-in platform and Fabric.

The host class gives the mixin what the service would (the catalog, landing zones, Fabric), so each
test sees exactly which workspace a Copy job goes to and what is recorded."""

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.landing_copy_service import LandingCopyMixin, _gone
from fabric_desktop.central.requests_service import StepError
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.orchestration import copyjob_build

CONN = "0f0e0d0c-0b0a-0908-0706-050403020100"
WS, LH = "1a2b3c4d-0000-4000-8000-00000000a001", "1a2b3c4d-0000-4000-8000-00000000a002"
JOB = "00000000-0000-4000-8000-0000000000aa"
COPY = {"connection_id": CONN, "connection_name": "SQL Server: erp-sql01 / ERP",
        "tables": [{"table": "dbo.Customer"}], "times": ["02:00"]}


class Catalog:
    def __init__(self, requests=(), admin=True):
        self.requests, self.admin = list(requests), admin

    def call(self, op, **kw):
        if op == "whoami":
            return {"admin": self.admin}
        if op == "list_requests":
            return [r for r in self.requests if not kw.get("status") or r.get("status") == kw["status"]]
        raise AssertionError(op)


class Fabric:
    def __init__(self, items=(), schedules=None):
        self.items, self.schedules, self.saved, self.created_schedules = list(items), schedules or {}, [], []

    def list_items(self, workspace_id):
        return [dict(i) for i in self.items]

    def save_item(self, workspace_id, item_type, name, parts=None, description=""):
        item = {"id": JOB, "type": item_type, "displayName": name, "description": description}
        self.saved.append((workspace_id, name, description))
        return item

    def list_schedules(self, workspace_id, item_id, job_type):
        return list(self.schedules.get(item_id, []))

    def create_schedule(self, workspace_id, item_id, job_type, configuration, enabled=True):
        s = {"id": "new-schedule"}
        self.created_schedules.append(s)
        return s


class Base:
    def platform_request(self, kind, target, reason="", detail=None, project=""):
        return {"sent": detail}

    def platform_decide(self, id, approve, note="", capacity_id=""):
        return {"decided": id}

    def platform_requests(self, status=""):
        return self.listed

    def platform_delete_source(self, name):
        return ["deleted"]

    def platform_landing(self, env="dev"):
        return {"sources": [dict(s) for s in self.sources]}


class Host(LandingCopyMixin, Base):
    def __init__(self, catalog=None, central=None, zones=(), config=None, fabric=None):
        self.catalog = catalog or Catalog()
        self.central, self.zones, self.config = central or {}, list(zones), config or {}
        self.fabric, self.progress, self.listed, self.sources = fabric or Fabric(), [], [], []

    def _catalog(self):
        return self.catalog

    def _central_landing(self):
        return self.central

    def _landing_zones(self):
        return self.zones

    def _platform_config(self):
        return self.config

    def _rq_fabric(self):
        return self.fabric

    @property
    def fabric_jobs(self):
        return self.fabric

    def _rq_step(self, label, fn):
        return fn()

    def _rq_progress(self, req, change):
        self.progress.append(change)


def request(**detail):
    return {"id": "r1", "kind": "source", "target": "erp", "status": "waiting", "reason": "",
            "detail": {"copy": dict(COPY), **detail}}


PROD = {"prod": {"workspace_id": WS, "landing_id": LH}}


# --------------------------------------------------------------------------- whether a workspace is gone


@pytest.mark.parametrize("status, code, gone", [
    (404, None, True), (404, "Forbidden", True), (403, "WorkspaceNotFound", True), (403, "EntityNotFound", True),
    (403, "Forbidden", False), (405, None, False), (None, None, False)])
def test_gone_is_a_404_or_a_not_found_code(status, code, gone):
    assert _gone(FabricError("x", code=code, status=status)) is gone


class Admin:
    def __init__(self, raise_=None, state=""):
        self.raise_, self.state = raise_, state

    def admin_get_workspace(self, workspace_id):
        if self.raise_:
            raise self.raise_
        return {"state": self.state}


@pytest.mark.parametrize("admin, gone", [
    (Admin(FabricError("x", status=404)), True), (Admin(FabricError("x", code="WorkspaceNotFound", status=400)), True),
    (Admin(FabricError("x", code="Forbidden", status=405)), False), (Admin(FabricError("x", status=403)), False),
    (Admin(state="Deleted"), True), (Admin(state="Active"), False)])
def test_the_admin_api_decides_whether_a_workspace_is_gone(admin, gone):
    h = Host()
    h._fabric_admin = lambda: admin
    assert h._lc_workspace_gone(WS) is gone


# --------------------------------------------------------------------------- where it lands


def test_without_one_landing_workspace_it_lands_in_the_environments_lh_landing():
    where = Host(config=PROD)._lc_where(request(), "prod")
    assert where == {"workspace_id": WS, "lakehouse_id": LH, "lakehouse_name": "LH_Landing", "workspace_name": "",
                     "zone": "", "zone_name": "", "secure": False}


@pytest.mark.parametrize("cfg", [{"workspace_id": WS}, {"landing_id": LH}, {}])
def test_an_environment_without_both_ids_has_no_lh_landing_yet(cfg):
    with pytest.raises(StepError, match="Prod has no LH_Landing"):
        Host(config={"prod": cfg})._lc_where(request(), "prod")


# --------------------------------------------------------------------------- where each waiting request lands


def test_only_waiting_source_requests_with_a_copy_job_are_told_where_they_land():
    h = Host(central={"workspace_id": WS, "lakehouse_id": LH, "workspace_name": "Landing"})
    h.listed = [request(), dict(request(), kind="sink"), dict(request(), status="approved"),
                dict(request(), detail={"owner": "x"}), dict(request(), detail=None)]
    out = h.platform_requests()
    assert [("lands_in" in r) for r in out] == [True, False, False, False, False]
    assert out[0]["lands_in"]["lakehouse_name"] == "LH_Landing"


def test_without_one_landing_workspace_no_request_is_told_where_it_lands():
    h = Host()
    h.listed = [request()]
    assert "lands_in" not in h.platform_requests()[0]


# --------------------------------------------------------------------------- making the Copy job


def test_a_new_copy_job_is_made_recorded_and_scheduled():
    h = Host(config=PROD)
    made = h._lc_make(dict(request(), reason="  Customer 360  "), "prod")
    assert made == {"copy_job_id": JOB, "workspace_id": WS, "copy_job_name": "CJ_Landing_erp", "schedule_id": "new-schedule"}
    assert h.progress == [{"copy_job_id": JOB, "workspace_id": WS, "copy_job_name": "CJ_Landing_erp"},
                          {"schedule_id": "new-schedule"}]
    assert h.fabric.saved[0][2].startswith("Lands erp in the landing zone. Customer 360 ")


def test_a_reason_of_none_still_makes_a_description():
    h = Host(config=PROD)
    h._lc_make(dict(request(), reason=None), "prod")
    assert h.fabric.saved[0][2].startswith("Lands erp in the landing zone. (KratosForge")


def _theirs(**kw):
    return {"id": JOB, "type": "CopyJob", "displayName": "CJ_Landing_erp", "description": "", **kw}


def test_the_job_this_request_made_is_found_by_id_and_not_recorded_again():
    h = Host(config=PROD, fabric=Fabric([_theirs()], {JOB: [{"id": "old-schedule"}, {"id": "other"}]}))
    made = h._lc_make(request(made={"copy_job_id": JOB}), "prod")
    assert h.fabric.saved == [] and h.progress == [{"schedule_id": "old-schedule"}]
    assert made["schedule_id"] == "old-schedule" and h.fabric.created_schedules == []


def test_the_job_is_found_by_its_marker_when_its_id_wasnt_recorded():
    h = Host(config=PROD, fabric=Fabric([_theirs(displayName="cj_landing_ERP", description="x (KratosForge request r1)")]))
    made = h._lc_make(request(), "prod")
    assert h.fabric.saved == [] and made["copy_job_id"] == JOB
    assert h.progress[0] == {"copy_job_id": JOB, "workspace_id": WS, "copy_job_name": "CJ_Landing_erp"}


def test_a_job_of_the_same_name_it_didnt_make_is_never_taken_over():
    h = Host(config=PROD, fabric=Fabric([_theirs(id="someone-else")]))
    with pytest.raises(core.PlatformError, match="already in the landing workspace"):
        h._lc_make(request(), "prod")


def test_an_item_of_another_type_or_name_isnt_the_copy_job():
    h = Host(config=PROD, fabric=Fabric([_theirs(type="Notebook", id="nb"), _theirs(displayName="CJ_Landing_crm", id="other")]))
    assert h._lc_make(request(), "prod")["copy_job_id"] == JOB and len(h.fabric.saved) == 1


def test_a_request_with_no_times_gets_no_schedule_and_one_already_scheduled_keeps_it():
    h = Host(config=PROD)
    made = h._lc_make(request(copy=dict(COPY, times=[])), "prod")
    assert "schedule_id" not in made and h.fabric.created_schedules == []
    h2 = Host(config=PROD, fabric=Fabric([_theirs()]))
    made2 = h2._lc_make(request(made={"copy_job_id": JOB, "schedule_id": "kept"}), "prod")
    assert made2["schedule_id"] == "kept" and h2.progress == []


# --------------------------------------------------------------------------- which Copy jobs hold a source


def _made(target="erp", kind="source", jid=JOB, ws=WS, **m):
    return {"id": "r1", "kind": kind, "target": target, "status": "approved",
            "detail": {"made": {"copy_job_id": jid, "workspace_id": ws, **m}}}


def test_only_source_requests_for_this_source_with_a_job_count():
    h = Host(catalog=Catalog([_made(), _made(target=" ERP "), _made(kind="sink"), _made(target="crm"),
                              _made(jid=""), _made(jid="00000000-0000-4000-8000-0000000000bb", copy_job_name="Mine")]))
    assert h._lc_jobs_for("erp") == [{"id": JOB, "workspace_id": WS, "name": "CJ_Landing_erp"},
                                     {"id": "00000000-0000-4000-8000-0000000000bb", "workspace_id": WS, "name": "Mine"}]


# --------------------------------------------------------------------------- the landing zone's Copy jobs


def test_the_landing_zone_shows_copy_jobs_of_approved_source_requests_only():
    approved = [dict(_made(copy_job_name=""), detail={"made": {"copy_job_id": JOB}, "copy": {"tables": [1, 2], "times": ["02:00"]}}),
                dict(_made(target="crm", kind="sink"), status="approved"),
                dict(_made(target="hr"), detail={"made": {}})]
    h = Host(catalog=Catalog(approved))
    h.sources = [{"name": "erp"}, {"name": "crm"}, {"name": "hr"}]
    out = h.platform_landing()
    assert out["sources"][0]["copy_job"] == {"id": JOB, "workspace_id": "", "name": copyjob_build.job_name("erp"),
                                             "tables": 2, "times": ["02:00"], "request_id": "r1"}
    assert "copy_job" not in out["sources"][1] and "copy_job" not in out["sources"][2]


def test_a_copy_job_without_its_copy_details_shows_no_tables_or_times():
    h = Host(catalog=Catalog([_made(copy_job_name="Mine")]))
    h.sources = [{"name": "erp"}]
    job = h.platform_landing()["sources"][0]["copy_job"]
    assert (job["name"], job["tables"], job["times"], job["workspace_id"]) == ("Mine", 0, [], WS)
