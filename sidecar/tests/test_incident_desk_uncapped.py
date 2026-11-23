"""The incident desk (incident_desk.py): an edge an uncapped mutation run found unchecked (batch 255,
docs/quality/mutation-testing.md). Incident history keeps one order for incidents opened at the same
moment: the order their latest snapshots were taken in, the same on every read, and a limit keeps
the first of them."""

import types

import polars as pl
import pytest

from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(
        signed_in_user=lambda: "chris@contoso.com",
        silent_token=lambda scopes: None,
        fabric_token=lambda: None,
        storage_token=lambda: None,
    )
    return s


class Lake:
    def __init__(self, df):
        self.df = df

    def rows(self, table):
        return self.df


def test_incidents_opened_at_the_same_moment_keep_the_order_they_were_last_changed_in(
    svc, monkeypatch
):
    # Snapshots given newest first, so the order out isn't simply the order in.
    rows = [
        {
            "incident_id": f"inc-{n:03d}",
            "env": "dev",
            "state": "open",
            "as_of": f"2026-11-01T10:{n // 60:02d}:{n % 60:02d}Z",
            "created_at": "2026-11-01T11:00:00Z",
            "opened_at": "2026-11-01T09:00:00Z",
            "record_id": f"r-{n}",
            "installation_id": "pc",
            "app_version": "1",
            "schema_version": 1,
        }
        for n in reversed(range(200))
    ]
    df = pl.DataFrame(
        rows,
        schema_overrides={
            "as_of": pl.String,
            "opened_at": pl.String,
            "created_at": pl.String,
        },
    )
    monkeypatch.setattr(
        svc,
        "central_lakehouse",
        lambda: {
            "workspace_id": "w1",
            "lakehouse_id": "l1",
            "lakehouse_name": "Central",
        },
    )
    monkeypatch.setattr(
        Service, "_optimize_lake", lambda self, ws, lh, schemas=None: Lake(df)
    )
    want = [f"inc-{n:03d}" for n in range(200)]
    for _ in range(3):
        assert [r["incident_id"] for r in svc.incidents_history()["incidents"]] == want
    assert [
        r["incident_id"] for r in svc.incidents_history(limit=20)["incidents"]
    ] == want[:20]
