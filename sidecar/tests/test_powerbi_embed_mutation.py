"""powerbi_embed.py paths that mutation testing (batch 77, docs/quality/mutation-testing.md) showed no test
pinned: each kind's whole capability profile, an error Power BI gives that isn't 401, 403 or 404, a token
already past its expiry, the padding of every token length, and the empty values a reply falls back to."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import powerbi_embed
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.powerbi_embed import EmbedClient

WS = "11111111-1111-1111-1111-111111111111"
RID = "22222222-2222-2222-2222-222222222222"
DID = "33333333-3333-3333-3333-333333333333"
EMBED = f"https://app.powerbi.com/reportEmbed?reportId={RID}&groupId={WS}"
DASH = f"https://app.powerbi.com/dashboardEmbed?dashboardId={DID}"


class Resp:
    def __init__(self, status: int, body: object):
        self.status_code, self._body, self.headers, self.text = status, body, {}, json.dumps(body)

    def json(self) -> object:
        return self._body


class Http:
    def __init__(self, routes: dict[str, Resp]):
        self.routes = routes

    def request(self, method, url, headers=None, timeout=None, **kw):
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return Resp(404, {"error": {"code": "ItemNotFound", "message": "Not found"}})


def client(routes: dict[str, Resp]) -> EmbedClient:
    return EmbedClient(FabricClient(lambda: "tok", session=Http(routes), sleep=lambda s: None))


def jwt(claims: dict) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{part}.sig"


def test_each_kind_has_exactly_its_capability_profile():
    caps = powerbi_embed.CAPABILITIES
    assert caps["report"] == {
        "embed_type": "report", "pages": True, "filters": True, "bookmarks": True, "slicers": True,
        "visuals": True, "events": ["loaded", "rendered", "error", "pageChanged", "dataSelected", "buttonClicked",
                                    "saved"],
        "limits": ["Export data follows the tenant setting and your permission."]}
    assert caps["paginated"] == {
        "embed_type": "report", "pages": False, "filters": False, "bookmarks": False, "slicers": False,
        "visuals": False, "parameters": True, "events": ["loaded", "error"],
        "limits": ["No bootstrap.", "No multi-value parameters.", "No rdl: parameters.",
                   "Not on real-time (push) semantic models."]}
    assert caps["dashboard"] == {
        "embed_type": "dashboard", "pages": False, "filters": False, "bookmarks": False, "slicers": False,
        "visuals": False, "tiles": True, "events": ["loaded", "error", "tileClicked"],
        "limits": ["No filters or bookmarks."]}


def test_an_error_that_isnt_401_403_or_404_comes_through_as_it_was():
    c = client({f"/groups/{WS}/reports/{RID}": Resp(400, {"error": {"code": "BadRequest", "message": "m"}})})
    with pytest.raises(FabricError) as raised:
        c.content(WS, RID, "Report")
    assert raised.value.status == 400


def test_a_token_past_its_expiry_has_no_seconds_left_not_a_negative_count():
    out = powerbi_embed.token_reply(jwt({"exp": 1_000}), now=1_500)
    assert out["expires_at"] == 1_000 and out["seconds_left"] == 0


@pytest.mark.parametrize("pad", ["", "a", "ab", "abc", "abcd", "abcde"])
def test_the_expiry_is_read_whatever_padding_the_token_needs(pad):
    # Claims of every length mod 4 lose a different amount of "=" when the token is made.
    assert powerbi_embed.token_expiry(jwt({"exp": 2_000, "p": pad})) == 2_000


def test_a_report_keeps_the_id_power_bi_gives_and_its_name_and_model():
    c = client({f"/groups/{WS}/reports/{RID}": Resp(200, {"id": "other-id", "name": "Sales", "embedUrl": EMBED,
                                                          "datasetId": "ds1"}),
                f"/groups/{WS}/reports/{RID}/pages": Resp(200, {"value": []})})
    out = c.report(WS, RID)
    assert out["id"] == "other-id" and out["name"] == "Sales" and out["dataset_id"] == "ds1"


def test_a_report_with_no_id_name_or_model_falls_back_to_the_asked_id_and_empty_text():
    c = client({f"/groups/{WS}/reports/{RID}": Resp(200, {"embedUrl": EMBED}),
                f"/groups/{WS}/reports/{RID}/pages": Resp(200, {"value": []})})
    out = c.report(WS, RID)
    assert out["id"] == RID and out["name"] == "" and out["dataset_id"] == ""


def test_a_dashboard_keeps_what_power_bi_gives_for_it_and_its_tiles():
    tile = f"https://app.powerbi.com/embed?dashboardId={DID}&tileId=t1"
    c = client({f"/groups/{WS}/dashboards/{DID}": Resp(200, {"id": "dash-id", "displayName": "Ops", "embedUrl": DASH}),
                f"/groups/{WS}/dashboards/{DID}/tiles": Resp(200, {"value": [
                    {"id": "t1", "title": "Revenue", "embedUrl": tile, "reportId": "r1", "datasetId": "d1"}]})})
    out = c.dashboard(WS, DID)
    assert out["id"] == "dash-id" and out["name"] == "Ops"
    assert out["tiles"] == [{"id": "t1", "title": "Revenue", "embed_url": tile, "report_id": "r1", "dataset_id": "d1"}]


def test_a_dashboard_and_tile_with_nothing_but_addresses_fall_back_to_the_asked_id_and_empty_text():
    tile = f"https://app.powerbi.com/embed?dashboardId={DID}&tileId=t1"
    c = client({f"/groups/{WS}/dashboards/{DID}": Resp(200, {"embedUrl": DASH}),
                f"/groups/{WS}/dashboards/{DID}/tiles": Resp(200, {"value": [{"embedUrl": tile}]})})
    out = c.dashboard(WS, DID)
    assert out["id"] == DID and out["name"] == ""
    assert out["tiles"] == [{"id": "", "title": "", "embed_url": tile, "report_id": "", "dataset_id": ""}]


def test_an_item_with_no_type_is_called_this_item():
    with pytest.raises(ValueError, match=r"dashboards, not this item\.$"):
        client({}).content(WS, RID, "")
