"""xray_service.py's staging lakehouse checks at their exact edges (mutation batch 165, a boundary
re-score of batch 15, docs/quality/mutation-testing.md): nothing written for exactly the quiet days, a
refresh exactly the margin after the last write, Power BI answering exactly 400, and a removal noted
exactly 90 days ago. Fabric, OneLake and Power BI are faked, as in test_xray_staging."""

from __future__ import annotations

import datetime as dt
import types

import pytest

import fabric_desktop.xray_service as xs
from fabric_desktop import xray

from test_xray_staging import OLD, WS, svc  # noqa: F401  (svc is the fixture)


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _files(monkeypatch, modified: str):
    class Files:
        def __init__(self, _t):
            pass

        def list(self, ws, lh, directory, recursive=False):
            entry = {"path": "Files/x.parquet", "is_dir": False, "size": 10, "modified": modified}
            return [entry] if directory == "Files" else []
    monkeypatch.setattr(xs, "FileStore", Files)


def test_nothing_written_for_exactly_the_quiet_days_is_quiet_enough(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc
    _files(monkeypatch, s.ago(xray.STAGING_QUIET_DAYS))
    s.flows.clear()
    s.flows["df1"] = {"name": "Sales load", "jobs": [{"status": "Completed", "startTimeUtc": s.ago(0)}]}
    p = s.xray_staging_preview(WS, "a")
    assert not any("days ago" in w for w in p["why"]), p["why"]
    _files(monkeypatch, s.ago(xray.STAGING_QUIET_DAYS - 1))
    assert "less than 30 days" in s.xray_staging_preview(WS, "a")["why"][0]


def test_a_refresh_exactly_the_margin_after_the_last_write_proves_it(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc
    written = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(days=40)
    _files(monkeypatch, written.isoformat())
    s.flows.clear()
    s.flows["df1"] = {"name": "Sales load",
                      "jobs": [{"status": "Completed", "startTimeUtc": (written + xs.STAGING_MARGIN).isoformat()}]}
    p = s.xray_staging_preview(WS, "a")
    assert p["dataflows"][0]["ok"] is True and p["ok"], p["why"]
    s.flows["df1"]["jobs"][0]["startTimeUtc"] = (written + xs.STAGING_MARGIN - dt.timedelta(seconds=1)).isoformat()
    assert "last refreshed before" in s.xray_staging_preview(WS, "a")["why"][-1]


def test_power_bi_answering_400_is_an_error(monkeypatch):
    answer = types.SimpleNamespace(status_code=400, json=lambda: {"value": [{"objectId": "x"}]})
    monkeypatch.setattr(xs, "send_with_retry", lambda call, method: answer)
    monkeypatch.setattr(xs.http_session, "session", lambda: None)
    me = types.SimpleNamespace(auth=types.SimpleNamespace(_token=lambda scopes: "t"))
    with pytest.raises(RuntimeError, match="Power BI answered 400"):
        xs.XRayMixin._powerbi_dataflows(me, WS)
    answer.status_code = 399
    assert xs.XRayMixin._powerbi_dataflows(me, WS) == [{"objectId": "x"}]


def test_a_removal_noted_exactly_90_days_ago_is_kept(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(xs, "dt", types.SimpleNamespace(datetime=Frozen, timedelta=dt.timedelta, timezone=dt.timezone))
    edge = (now - dt.timedelta(days=90)).isoformat(timespec="seconds")
    older = (now - dt.timedelta(days=90, seconds=1)).isoformat(timespec="seconds")
    s._save_settings(xray_removed={"edge": edge, "older": older})
    s.xray_remove_staging(WS, "a", OLD, "Sales", finding_id="new")
    assert sorted(s._settings()["xray_removed"]) == ["edge", "new"]
