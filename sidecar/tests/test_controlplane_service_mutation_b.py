"""Mutation-testing follow-ups for controlplane_service.py (batch 463): a preview's lifetime, count and token
at their edges, a preview that expires between its check and its use, the field and message limits of a plan
request, the typed name of a workspace that isn't Prod, adopt without a central lakehouse, cost's days, an
upgrade whose manifest went away or that has nothing left to do, a check whose tables can't be read, and the
canonical JSON behind a preview's digest. Nothing here reaches Fabric."""
from __future__ import annotations

import pytest

from fabric_desktop import controlplane_service as cpsvc
from fabric_desktop.controlplane import health
from fabric_desktop.controlplane import store as st
from test_controlplane_service import CENTRAL, WS, _central, svc  # noqa: F401 - svc is the fixture


class _Clock:
    def __init__(self, *ticks: float):
        self.ticks = list(ticks)

    def monotonic(self) -> float:
        return self.ticks.pop(0)


def _clock(monkeypatch, *ticks: float) -> None:
    monkeypatch.setattr(cpsvc, "time", _Clock(*ticks))


# ---- previews


def test_a_preview_lasts_fifteen_minutes_to_the_second(svc, monkeypatch):  # noqa: F811
    _clock(monkeypatch, 0.0, 900.0, 900.0, 901.0)
    kept = svc._cp_keep("create", request={}, digest="d")
    assert kept["expires_in_seconds"] == 900
    assert svc._cp_peek("create", kept["token"])["kind"] == "create"
    other = svc._cp_keep("create", request={}, digest="e")  # at 900 s the first one isn't cleared away
    assert kept["token"] in svc._cp_previews() and other["token"] in svc._cp_previews()
    with pytest.raises(PermissionError, match="expired"):
        svc._cp_peek("create", kept["token"])


def test_a_preview_is_taken_at_exactly_fifteen_minutes(svc, monkeypatch):  # noqa: F811
    _clock(monkeypatch, 0.0, 900.0)
    kept = svc._cp_keep("create", request={}, digest="d")
    assert svc._cp_take("create", kept["token"])["digest"] == "d"


def test_a_preview_that_expires_between_its_check_and_its_use_runs_nothing(svc, monkeypatch):  # noqa: F811
    _clock(monkeypatch, 0.0, 900.0, 901.0)
    plan = svc.cp_plan({"name": "Ours", "capacity_id": "cap"})
    with pytest.raises(PermissionError, match="expired"):
        svc.cp_create(plan["token"], confirmed=True)
    assert svc.fake.created == []


def test_twenty_previews_are_kept_and_the_twenty_first_drops_the_oldest(svc):  # noqa: F811
    tokens = [svc._cp_keep("create", request={}, digest=str(i))["token"] for i in range(20)]
    assert len(svc._cp_previews()) == 20 and tokens[0] in svc._cp_previews()
    svc._cp_keep("create", request={}, digest="20")
    assert len(svc._cp_previews()) == 20 and tokens[0] not in svc._cp_previews()


def test_a_preview_token_is_32_characters(svc):  # noqa: F811
    assert len(svc._cp_keep("create", request={}, digest="d")["token"]) == 32


def test_the_digest_reads_keys_in_any_order():
    assert cpsvc._digest({"a": 1, "b": [2]}) == cpsvc._digest({"b": [2], "a": 1})


# ---- plan requests


def test_a_field_of_256_characters_is_taken_and_257_isnt(svc):  # noqa: F811
    assert svc.cp_plan({"name": "n" * 256, "capacity_id": "cap"})["request"]["name"] == "n" * 256
    with pytest.raises(ValueError, match="name must be text of at most 256 characters"):
        svc.cp_plan({"name": "n" * 257})


def test_unknown_fields_are_named_in_at_most_200_characters(svc):  # noqa: F811
    request = {f"field_{i:03d}": "x" for i in range(60)}
    with pytest.raises(ValueError) as info:
        svc.cp_plan(request)
    assert len(str(info.value)) == len("Unknown request fields: ") + 200


def test_a_workspace_that_isnt_prod_needs_no_typed_name(svc):  # noqa: F811
    plan = svc.cp_plan({"name": "Ours", "workspace_id": WS})
    assert plan["token"] and plan["typed_name"] == ""


def test_adopt_needs_a_central_lakehouse_first(svc):  # noqa: F811
    with pytest.raises(ValueError, match="Connect this PC to a central lakehouse first"):
        svc.cp_adopt_preview("Ours")


# ---- cost


def test_cost_covers_30_days_by_default_and_takes_1_and_366(svc):  # noqa: F811
    svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    assert svc.cp_cost()["period"]["days"] == 30
    assert svc.cp_cost(days=1)["period"]["days"] == 1 and svc.cp_cost(days=366)["period"]["days"] == 366
    with pytest.raises(ValueError, match=r"not '9{19}\.$"):
        svc.cp_cost(days="9" * 500)


# ---- upgrade


def _adopted(svc):  # noqa: F811
    _central(svc)
    svc.cp_adopt(svc.cp_adopt_preview("Ours")["token"], confirmed=True)


def _drop_manifest(svc):  # noqa: F811
    folder = svc.lakes / WS / CENTRAL / st.FOLDER
    for f in folder.iterdir():
        if f.name.startswith(st.NAME):
            f.unlink()
    assert st.local_store(svc.lakes / WS / CENTRAL).read() is None


def test_an_upgrade_plan_needs_the_manifest_still_there(svc):  # noqa: F811
    _adopted(svc)
    _drop_manifest(svc)
    with pytest.raises(ValueError, match="isn't in its lakehouse any more"):
        svc.cp_upgrade_plan()


def test_an_upgrade_whose_manifest_went_away_runs_nothing(svc):  # noqa: F811
    _adopted(svc)
    plan = svc.cp_upgrade_plan()
    _drop_manifest(svc)
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_upgrade(plan["token"], confirmed=True)


def test_an_upgrade_with_nothing_left_to_do_says_so(svc):  # noqa: F811
    _adopted(svc)
    first, second = svc.cp_upgrade_plan(), svc.cp_upgrade_plan()
    assert svc.cp_upgrade(second["token"], confirmed=True)["ok"]
    with pytest.raises(PermissionError, match="Nothing to upgrade"):
        svc.cp_upgrade(first["token"], confirmed=True)


# ---- check


def test_a_check_whose_tables_cant_be_read_is_connected_and_offline(svc, monkeypatch):  # noqa: F811
    svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)

    def broken(*_a, **_k):
        raise RuntimeError("network down")
    monkeypatch.setattr(svc, "_cp_inspections", broken)
    out = svc.cp_check()
    assert out["status"] == health.OFFLINE and "network down" in out["error"]


def test_a_connection_saved_without_its_schemas_flag_reads_a_schema_enabled_lakehouse(svc):  # noqa: F811
    import json
    _central(svc)
    with_flag = svc.cp_adopt_preview("Ours")
    path = svc.optimize_registry.path
    data = json.loads(path.read_text(encoding="utf-8"))
    for conn in data["connections"]:
        conn.pop("schemas", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    without = svc.cp_adopt_preview("Ours")
    assert without["manifest"]["schema"] == with_flag["manifest"]["schema"]
    assert without["manifest"]["analytical_store"] == with_flag["manifest"]["analytical_store"]
