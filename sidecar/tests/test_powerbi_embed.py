"""Embed Studio's engine calls (PBE-04): embed URLs only on app.powerbi.com, ids checked before they go into a
path, plain errors, and the token never recorded or offered to external tools."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, powerbi_embed, server
from fabric_desktop.fabric_api import FabricClient
from fabric_desktop.powerbi_embed import EmbedClient

WS = "11111111-1111-1111-1111-111111111111"
RID = "22222222-2222-2222-2222-222222222222"
DID = "33333333-3333-3333-3333-333333333333"
EMBED = f"https://app.powerbi.com/reportEmbed?reportId={RID}&groupId={WS}&w=2"


class Resp:
    def __init__(self, status: int, body: object):
        self.status_code, self._body, self.headers, self.text = status, body, {}, json.dumps(body)

    def json(self) -> object:
        return self._body


class Http:
    """Answers GETs by URL suffix and records what was asked."""

    def __init__(self, routes: dict[str, Resp]):
        self.routes, self.asked = routes, []

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.asked.append((method, url))
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return Resp(404, {"error": {"code": "ItemNotFound", "message": "Not found"}})


def client(routes: dict[str, Resp]) -> tuple[EmbedClient, Http]:
    http = Http(routes)
    return EmbedClient(FabricClient(lambda: "tok", session=http, sleep=lambda s: None)), http


def jwt(claims: dict) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{part}.sig"


def test_a_report_comes_back_with_its_embed_url_model_pages_and_profile():
    c, http = client({f"/groups/{WS}/reports/{RID}": Resp(200, {"id": RID, "name": "Sales", "embedUrl": EMBED,
                                                                "datasetId": "ds1", "reportType": "PowerBIReport"}),
                      f"/groups/{WS}/reports/{RID}/pages": Resp(200, {"value": [
                          {"name": "ReportSection1", "displayName": "Overview", "order": 0}]})})
    out = c.content(WS, RID, "Report")
    assert out["kind"] == "report" and out["embed_url"] == EMBED and out["dataset_id"] == "ds1"
    assert out["pages"] == [{"name": "ReportSection1", "display_name": "Overview", "order": 0}]
    assert out["capabilities"]["filters"] is True
    assert all(url.startswith("https://api.powerbi.com/v1.0/myorg/") for _, url in http.asked)
    assert all(method == "GET" for method, _ in http.asked)


def test_a_paginated_report_has_no_pages_and_says_what_it_cant_do():
    c, http = client({f"/groups/{WS}/reports/{RID}": Resp(200, {"id": RID, "name": "Invoice", "embedUrl": EMBED,
                                                                "reportType": "PaginatedReport"})})
    out = c.content(WS, RID, "PaginatedReport")
    assert out["kind"] == "paginated" and out["pages"] == []
    assert out["capabilities"]["parameters"] is True and "No bootstrap." in out["capabilities"]["limits"]
    assert not any(url.endswith("/pages") for _, url in http.asked)


def test_a_dashboard_lists_only_tiles_on_power_bi():
    good = f"https://app.powerbi.com/embed?dashboardId={DID}&tileId=t1"
    c, _ = client({f"/groups/{WS}/dashboards/{DID}": Resp(200, {"id": DID, "displayName": "Ops",
                                                                "embedUrl": f"https://app.powerbi.com/dashboardEmbed?dashboardId={DID}"}),
                   f"/groups/{WS}/dashboards/{DID}/tiles": Resp(200, {"value": [
                       {"id": "t1", "title": "Revenue", "embedUrl": good, "reportId": RID},
                       {"id": "t2", "title": "Elsewhere", "embedUrl": "https://evil.example/embed"},
                       {"id": "t3", "title": "No url"}]})})
    out = c.content(WS, DID, "Dashboard")
    assert out["kind"] == "dashboard" and [t["id"] for t in out["tiles"]] == ["t1"]


@pytest.mark.parametrize("url", ["http://app.powerbi.com/reportEmbed", "https://evil.example/reportEmbed",
                                 "https://app.powerbi.com.evil.example/x", "https://x@app.powerbi.com/x",
                                 "https://app.powerbi.com:444/x", "https://msit.powerbi.com/reportEmbed",
                                 "javascript:alert(1)", "", None])
def test_embed_urls_off_app_powerbi_com_are_refused(url):
    with pytest.raises(ValueError, match="only opens content on app.powerbi.com"):
        powerbi_embed.embed_url(url)


def test_a_report_whose_embed_url_is_elsewhere_is_refused():
    c, _ = client({f"/groups/{WS}/reports/{RID}": Resp(200, {"id": RID, "embedUrl": "https://evil.example/r",
                                                             "reportType": "PowerBIReport"})})
    with pytest.raises(ValueError):
        c.content(WS, RID, "Report")


@pytest.mark.parametrize("ws,item", [("../admin", RID), (WS, "x/../../admin"), ("", RID), (WS, "")])
def test_ids_are_checked_before_any_request(ws, item):
    c, http = client({})
    with pytest.raises(ValueError, match="isn't a"):
        c.content(ws, item, "Report")
    assert http.asked == []


def test_other_item_types_and_qna_are_refused():
    c, http = client({})
    for kind in ("Lakehouse", "QnA", ""):
        with pytest.raises(ValueError, match="reports, paginated reports and dashboards"):
            c.content(WS, RID, kind)
    assert http.asked == []


@pytest.mark.parametrize("status,error", [(404, LookupError), (403, PermissionError), (401, PermissionError)])
def test_errors_people_see_are_plain(status, error):
    c, _ = client({f"/groups/{WS}/reports/{RID}": Resp(status, {"error": {"code": "X", "message": "m"}})})
    with pytest.raises(error):
        c.content(WS, RID, "Report")


def test_the_token_reply_says_when_it_expires():
    out = powerbi_embed.token_reply(jwt({"exp": 2_000}), now=1_000)
    assert out["expires_at"] == 2_000 and out["seconds_left"] == 1_000 and out["token_type"] == "Aad"
    assert powerbi_embed.token_reply("not-a-jwt")["expires_at"] is None


def test_the_calls_are_read_only_and_never_offered_to_external_tools():
    assert {"pbi_embed_token", "pbi_embed_content"} <= server.METHODS
    assert not audit.audited("pbi_embed_token") and not audit.audited("pbi_embed_content")
    from fabric_desktop.integrations import capabilities
    source = Path(capabilities.__file__).read_text(encoding="utf-8")
    assert "pbi_embed" not in source and "powerbi_embed" not in source


def test_an_error_never_carries_the_token():
    reply = server.handle(_Service(), json.dumps({"id": 1, "method": "pbi_embed_content",
                                                  "params": {"workspace_id": WS, "item_id": RID}}))
    assert "error" in reply and "eyJ" not in reply["error"]


class _Service:
    def pbi_embed_content(self, **_):
        raise RuntimeError(f"failed with Authorization: Bearer {jwt({'exp': 1})}")


def test_the_token_asks_for_ten_minutes_left_with_the_embed_scopes():
    from fabric_desktop.auth import POWERBI_EMBED_SCOPES
    from fabric_desktop.powerbi_embed_service import PowerBiEmbedMixin
    asked = []

    class Auth:
        def _token(self, scopes, min_seconds=0):
            asked.append((scopes, min_seconds))
            return jwt({"exp": 9_999_999_999})

    svc = PowerBiEmbedMixin()
    svc.auth = Auth()
    assert svc.pbi_embed_token()["token_type"] == "Aad"
    assert asked == [(POWERBI_EMBED_SCOPES, 600)]
