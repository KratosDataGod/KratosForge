"""The change window (data mesh P6): the platform agrees when Prod changes happen, and a promote that
changes products other projects read gets a warning outside it. Never a block."""

import datetime as dt

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.meta import mesh
from fabric_desktop.service import Service

WINDOW = {"days": ["mon", "tue", "wed", "thu"], "start": "08:00", "end": "16:00", "time_zone": "UTC"}
FRIDAY_EVENING = dt.datetime(2026, 11, 6, 17, 20, tzinfo=dt.timezone.utc)
TUESDAY_MORNING = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: "chris@example.com")
    p.call("claim_admin")
    return p


# --------------------------------------------------------------------------- keeping it on the platform


def test_no_window_until_an_admin_sets_one(platform):
    assert platform.call("change_window") == {"window": None}
    out = platform.call("save_change_window", window=dict(WINDOW, days=["thu", "mon", "tue", "wed"]))
    assert out["window"] == dict(WINDOW, envs=["prod"])  # days in week order, Prod by default
    assert platform.call("change_window") == out
    assert platform.call("audit")[0]["action"] == "save change window"
    assert platform.call("save_change_window", window=None) == {"window": None}
    assert platform.call("change_window") == {"window": None}


def test_only_platform_admins_set_it(tmp_path, platform):
    other = LocalPlatform(platform.path, lambda: "dana@example.com")
    with pytest.raises(core.PlatformError):
        other.call("save_change_window", window=WINDOW)
    assert other.call("change_window") == {"window": None}  # anyone may read it


@pytest.mark.parametrize("bad", [
    dict(WINDOW, days=[]), dict(WINDOW, days=["friday"]), dict(WINDOW, start="8am"), dict(WINDOW, start="16:00"),
    dict(WINDOW, end="24:00"), dict(WINDOW, time_zone="Europe London; rm"), dict(WINDOW, envs=["dev"]),
    dict(WINDOW, soon=True), "weekdays", dict(WINDOW, start="08:00\n"), dict(WINDOW, time_zone="UTC\n"),
])
def test_a_window_nobody_can_check_is_refused(platform, bad):
    with pytest.raises(core.PlatformError):
        platform.call("save_change_window", window=bad)


def test_a_stored_value_that_fails_the_checks_reads_as_none(platform):
    platform.call("save_change_window", window=WINDOW)
    with platform._db() as db:
        db.run(f"UPDATE {db.t('config', 'setting')} SET value = ? WHERE name = 'change_window'", ('{"days": "all"}',))
        db.commit()
    assert platform.call("change_window") == {"window": None}


# --------------------------------------------------------------------------- the warning


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    s.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "1",
                         "ports": [{"name": "customers", "entity": "silver.customers"}]})
    cols = s._project().entities["silver.customers"].columns
    s._catalog().call("register_product", product={"project": "crm", "name": "customers", "access": "public",
                                                   "status": "published", "version": "1"},
                      ports=[{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": cols}],
                      env="test")
    s._catalog().call("register_link", consumer="finance", alias="customers", product_id="crm/customers",
                      port="customers", reads={"dev": "test", "test": "test", "prod": "prod"})
    return s


def _notes(svc, window, env="prod", now=FRIDAY_EVENING):
    return mesh.change_window_notes(window, svc._project(), svc._catalog().call("list_products"), env, now)


def test_outside_the_window_with_readers_warns(svc):
    [note] = _notes(svc, dict(WINDOW, envs=["prod"]))
    assert note.startswith("Outside the change window: Prod changes to products other projects read are agreed "
                           "for Mon to Thu, 08:00 to 16:00 (UTC), and it's Fri 17:20 there.")
    assert "1 project reads what's changing: finance. You can still promote." in note


def test_inside_the_window_says_nothing(svc):
    assert _notes(svc, dict(WINDOW, envs=["prod"]), now=TUESDAY_MORNING) == []
    assert _notes(svc, dict(WINDOW, envs=["prod"]), now=TUESDAY_MORNING.replace(hour=8)) == []  # the start is inside


def test_the_end_time_is_outside(svc):
    assert _notes(svc, dict(WINDOW, envs=["prod"]), now=TUESDAY_MORNING.replace(hour=16))


def test_the_time_zone_decides_the_day_and_hour(svc):
    window = dict(WINDOW, envs=["prod"], time_zone="Asia/Tokyo")  # Friday 17:20 UTC is Saturday 02:20 there
    [note] = _notes(svc, window)
    assert "it's Sat 02:20 there" in note
    assert _notes(svc, window, now=dt.datetime(2026, 11, 3, 0, 0, tzinfo=dt.timezone.utc)) == []  # Tue 09:00 Tokyo


def test_an_unknown_time_zone_says_so(svc):
    [note] = _notes(svc, dict(WINDOW, envs=["prod"], time_zone="Nowhere/Land"))
    assert "doesn't know the time zone Nowhere/Land" in note


def test_only_the_window_s_environments_and_only_with_readers(svc):
    assert _notes(svc, dict(WINDOW, envs=["prod"]), env="test") == []
    assert _notes(svc, None) == []
    published = svc._catalog().call("list_products")
    assert mesh.window_readers(svc._project(), published, "prod") == ["finance"]
    for p in published:
        p["consumers"] = [dict(c, consumer_project="crm") for c in p["consumers"]]  # only the project itself reads
    assert mesh.change_window_notes(dict(WINDOW, envs=["prod"]), svc._project(), published, "prod", FRIDAY_EVENING) == []


def test_the_promote_check_shows_the_warning_and_never_blocks(svc, monkeypatch):
    path = svc._catalog().path
    LocalPlatform(path, lambda: "chris@example.com").call("claim_admin")
    svc._local_platforms["prod"] = LocalPlatform(path, lambda: "chris@example.com")
    svc.platform_save_change_window(dict(WINDOW, envs=["test"]))
    assert svc.platform_change_window()["window"]["envs"] == ["test"]
    monkeypatch.setattr(mesh, "change_window_notes",
                        lambda w, p, pub, env, now, real=mesh.change_window_notes: real(w, p, pub, env, FRIDAY_EVENING))
    check = svc.meta_promote_check("dev", "test")
    assert any(n.startswith("Outside the change window") for n in check["notes"])
    assert not any("change window" in e for e in check["errors"])


def test_an_older_platform_without_the_operation_says_nothing(svc, monkeypatch):
    real = svc._catalog().call

    def older(op, **kw):
        if op == "change_window":
            raise LookupError("UDF_Platform has no change_window")
        return real(op, **kw)

    monkeypatch.setattr(svc._catalog(), "call", older)
    assert svc._window_gate(svc._project(), "prod") == []
    assert svc.platform_change_window()["available"] is False
