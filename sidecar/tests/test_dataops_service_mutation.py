"""The Data health service edges the main tests don't pin down (mutation batch 197,
docs/quality/mutation-testing.md): its limits at their exact boundaries, the saved list's odd
entries, the lock it holds, and the wording of what it says."""

import copy
import datetime as dt
import threading
from contextlib import contextmanager

import pytest

from fabric_desktop import dataops_service as ds
from fabric_desktop.service import Service
from tests.test_dataops import svc  # noqa: F401  (fixture)

UTC = dt.timezone.utc


class _Project:
    def __init__(self, names):
        self.entities = {n: None for n in names}


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A service whose project is only a list of entity names, and whose checks say what each test sets."""
    s = Service(root=tmp_path / "app")
    names = ["silver.customers", "silver.customers2", "gold.dim_customer"]
    monkeypatch.setattr(type(s), "_project", lambda self: _Project(names))
    s.checks = {}
    monkeypatch.setattr(
        s,
        "meta_data_health",
        lambda name, env: copy.deepcopy(
            s.checks.get(name, {"entity": name, "env": env, "alerts": []})
        ),
    )
    return s


# ---- the limits and their wording


def test_the_limits_are_two_minutes_a_hundred_tables_ten_seconds_and_a_week():
    assert ds.DATA_HEALTH_LIST_SECONDS == 120 and ds.DATA_HEALTH_MAX == 100
    assert ds.DATA_HEALTH_LOCK_SECONDS == 10 and ds.DATA_HEALTH_RESET_DAYS == 7
    assert ds.DATA_HEALTH_FILE == "data_health.json"


@pytest.mark.parametrize(
    "seconds, said",
    [
        (60, "1 minute"),
        (120, "2 minutes"),
        (180, "3 minutes"),
        (60.0, "1 minute"),
        (90, "90 s"),
        (30, "30 s"),
        (59, "59 s"),
        (0, "0 s"),
        (0.5, "0.5 s"),
        (61, "61 s"),
        (150, "150 s"),
    ],
)
def test_a_duration_is_whole_minutes_only_when_it_is_some(seconds, said):
    assert ds._dh_duration(seconds) == said


def test_a_key_is_the_environment_entity_kind_and_version_joined_by_bars():
    assert (
        ds._dh_key("dev", "silver.customers", "volume", 5)
        == "dev|silver.customers|volume|5"
    )
    assert ds._dh_key("prod", "a", "schema", None) == "prod|a|schema|None"


def test_a_time_that_cant_be_read_is_the_earliest_and_a_naive_one_is_utc():
    assert ds._dh_time("not a time") == dt.datetime.min.replace(tzinfo=UTC)
    assert ds._dh_time("2026-11-17T10:00:00") == dt.datetime(
        2026, 11, 17, 10, tzinfo=UTC
    )
    plus2 = dt.timezone(dt.timedelta(hours=2))
    assert ds._dh_time("2026-11-17T10:00:00+02:00") == dt.datetime(
        2026, 11, 17, 10, tzinfo=plus2
    )
    assert ds._dh_time("2026-11-17T10:00:00+02:00").utcoffset() == dt.timedelta(hours=2)


def test_the_check_that_doesnt_finish_says_the_default_limit_in_minutes(
    bare, monkeypatch
):
    waited = []
    monkeypatch.setattr(
        ds, "wait", lambda futures, timeout: waited.append(timeout)
    )  # stops waiting at once
    release = threading.Event()
    bare.meta_data_health = lambda name, env: (
        release.wait(5) or {"entity": name, "env": env, "alerts": []}
    )
    bare.meta_data_health_watch("gold.dim_customer", "dev")
    try:
        [t] = bare.meta_data_health_list()["tables"]
    finally:
        release.set()
    assert waited == [120]
    assert t == {
        "entity": "gold.dim_customer",
        "env": "dev",
        "state": "error",
        "why": "The check of gold.dim_customer didn't finish within 2 minutes, so it's not shown as healthy. "
        "Check again later.",
    }


def test_the_checks_run_four_at_a_time(bare, monkeypatch):
    made = []
    real = ds.ThreadPoolExecutor

    def pool(max_workers):
        made.append(max_workers)
        return real(max_workers=max_workers)

    monkeypatch.setattr(ds, "ThreadPoolExecutor", pool)
    bare.meta_data_health_watch("silver.customers", "dev")
    bare.meta_data_health_list()
    assert made == [4]


def test_the_hundredth_table_can_be_watched_and_the_hundred_and_first_cant(
    bare, monkeypatch
):
    names = [f"silver.t{i:03}" for i in range(101)]
    monkeypatch.setattr(type(bare), "_project", lambda self: _Project(names))
    bare._dh_write(
        {"watched": [{"entity": n, "env": "dev"} for n in names[:99]], "quiet": {}}
    )
    bare.meta_data_health_watch(names[99], "dev")
    assert len(bare._dh_read()["watched"]) == 100
    with pytest.raises(ValueError) as e:
        bare.meta_data_health_watch(names[100], "prod")
    assert (
        str(e.value)
        == "Data health watches at most 100 tables; stop watching one first"
    )
    bare.meta_data_health_watch(
        names[0], "dev"
    )  # watching one again replaces it, so it still fits
    assert len(bare._dh_read()["watched"]) == 100


# ---- the lock


def test_a_save_holds_this_process_s_lock_and_a_file_lock_waited_on_for_ten_seconds(
    bare, monkeypatch
):
    seen = []

    @contextmanager
    def file_lock(path, timeout=None):
        seen.append((path.name, timeout, ds._DH_LOCK.locked()))
        yield

    monkeypatch.setattr(ds, "_file_lock", file_lock)
    with bare._dh_locked():
        assert ds._DH_LOCK.locked()
    assert not ds._DH_LOCK.locked()
    assert seen == [("data_health.json.lock", 10, True)]
    assert (bare.root / "data_health.json.lock").parent == bare.root


def test_every_change_to_the_list_takes_the_lock(bare, monkeypatch):
    taken = []

    @contextmanager
    def file_lock(path, timeout=None):
        taken.append(path.name)
        yield

    monkeypatch.setattr(ds, "_file_lock", file_lock)
    bare.meta_data_health_watch("silver.customers", "dev")
    assert taken == ["data_health.json.lock"]
    bare.meta_data_health_quiet("silver.customers", "dev", "volume", 1)
    assert len(taken) == 2
    bare.meta_data_health_watch("silver.customers", "dev", watch=False)
    assert len(taken) == 3


def test_a_lock_that_isnt_given_up_is_this_error_and_nothing_else(bare, monkeypatch):
    @contextmanager
    def file_lock(path, timeout=None):
        raise TimeoutError("raw")
        yield  # pragma: no cover

    monkeypatch.setattr(ds, "_file_lock", file_lock)
    with pytest.raises(TimeoutError) as e:
        bare.meta_data_health_watch("silver.customers", "dev")
    assert (
        str(e.value)
        == "Another KratosForge window or tool is saving the Data health list. Try again in a moment."
    )
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert not ds._DH_LOCK.locked()


# ---- the saved list


def test_odd_entries_in_the_saved_list_are_left_out(bare):
    bare._dh_write(
        {
            "watched": [
                {"entity": "a", "env": "dev"},
                {"entity": "", "env": "dev"},
                {"entity": "b"},
                {"env": "dev"},
                "c",
                None,
                {"entity": "d", "env": ""},
            ],
            "quiet": {"k": {"at": "x"}, "j": "acked", "n": None},
        }
    )
    state = bare._dh_read()
    assert state == {
        "watched": [{"entity": "a", "env": "dev"}],
        "quiet": {"k": {"at": "x"}},
    }


def test_an_empty_or_missing_list_is_nothing_watched(bare):
    assert bare._dh_read() == {"watched": [], "quiet": {}}
    bare._dh_write({"watched": None, "quiet": None})
    assert bare._dh_read() == {"watched": [], "quiet": {}}


def test_a_reset_is_said_for_just_under_a_week(bare):
    now = dt.datetime.now(UTC)

    def said(at):
        bare._dh_write({"reset": {"at": at, "copy": "data_health.json.corrupt-1"}})
        return bare._dh_read().get("reset")

    recent = (now - dt.timedelta(days=7) + dt.timedelta(minutes=5)).isoformat()
    assert said(recent) == {"at": recent, "copy": "data_health.json.corrupt-1"}
    assert said((now - dt.timedelta(days=7, seconds=1)).isoformat()) is None
    assert said((now - dt.timedelta(days=6, hours=23)).isoformat()) is not None
    assert said("not a time") is None  # read as the earliest time, so long ago


@pytest.mark.parametrize(
    "reset",
    [
        "2026-11-17",
        {"at": 1, "copy": "c"},
        {"at": "2026-11-17T00:00:00+00:00"},
        {"copy": "c"},
        {"at": "2026-11-17T00:00:00+00:00", "copy": 3},
    ],
)
def test_a_reset_note_of_the_wrong_shape_isnt_said(bare, reset):
    bare._dh_write({"reset": reset})
    assert "reset" not in bare._dh_read()


def test_a_damaged_list_starts_again_with_a_note_of_when_and_the_copy(bare):
    (bare.root).mkdir(parents=True, exist_ok=True)
    (bare.root / "data_health.json").write_text("{cut", encoding="utf-8")
    before = dt.datetime.now(UTC).replace(microsecond=0)
    state = bare._dh_read()
    assert state["watched"] == [] and state["quiet"] == {}
    at = dt.datetime.fromisoformat(state["reset"]["at"])
    assert before <= at <= dt.datetime.now(UTC) and "." not in state["reset"]["at"]
    [copy] = bare.root.glob("data_health.json.corrupt-*")
    assert state["reset"]["copy"] == copy.name
    import json

    saved = json.loads((bare.root / "data_health.json").read_text(encoding="utf-8"))
    assert saved == {"reset": state["reset"]}


# ---- watching


def test_watching_says_so_and_records_when_to_the_second(bare):
    before = dt.datetime.now(UTC).replace(microsecond=0)
    assert bare.meta_data_health_watch("silver.customers", "test") == {
        "entity": "silver.customers",
        "env": "test",
        "watched": True,
    }
    [w] = bare._dh_read()["watched"]
    assert (
        w["entity"] == "silver.customers"
        and w["env"] == "test"
        and "." not in w["added_at"]
    )
    assert before <= dt.datetime.fromisoformat(w["added_at"]) <= dt.datetime.now(UTC)


def test_watching_checks_the_environment_and_the_entity(bare):
    with pytest.raises(ValueError) as e:
        bare.meta_data_health_watch("silver.customers", "staging")
    assert str(e.value) == "env is one of dev, test, prod"
    with pytest.raises(ValueError) as e:
        bare.meta_data_health_watch("silver.nope", "dev")
    assert str(e.value) == "silver.nope isn't an entity in the metadata project"
    assert (
        bare.meta_data_health_watch("silver.nope", "dev", watch=False)["watched"]
        is False
    )


def test_stopping_watching_forgets_only_that_table_s_quiet_alerts_in_that_environment(
    bare,
):
    for name, env in (
        ("silver.customers", "dev"),
        ("silver.customers2", "dev"),
        ("silver.customers", "prod"),
    ):
        bare.meta_data_health_watch(name, env)
        bare.meta_data_health_quiet(name, env, "volume", 1)
    bare.meta_data_health_watch("silver.customers", "dev", watch=False)
    state = bare._dh_read()
    assert sorted(state["quiet"]) == [
        "dev|silver.customers2|volume|1",
        "prod|silver.customers|volume|1",
    ]
    assert [(w["entity"], w["env"]) for w in state["watched"]] == [
        ("silver.customers2", "dev"),
        ("silver.customers", "prod"),
    ]


def test_watching_again_keeps_the_quiet_alerts(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    bare.meta_data_health_quiet("silver.customers", "dev", "volume", 1)
    bare.meta_data_health_watch("silver.customers", "dev")
    assert list(bare._dh_read()["quiet"]) == ["dev|silver.customers|volume|1"]


# ---- acknowledging and snoozing


def test_an_acknowledgement_records_who_when_and_no_end(bare, monkeypatch):
    monkeypatch.setattr(bare, "_wl_user", lambda: "avery@example.com")
    bare.meta_data_health_watch("silver.customers", "dev")
    before = dt.datetime.now(UTC).replace(microsecond=0)
    out = bare.meta_data_health_quiet("silver.customers", "dev", "volume", "4")
    assert (
        out["version"] == 4
        and out["by"] == "avery@example.com"
        and out["until"] is None
    )
    assert (
        out["entity"] == "silver.customers"
        and out["env"] == "dev"
        and out["kind"] == "volume"
    )
    assert "." not in out["at"] and before <= dt.datetime.fromisoformat(
        out["at"]
    ) <= dt.datetime.now(UTC)
    assert bare._dh_read()["quiet"] == {
        "dev|silver.customers|volume|4": {
            "at": out["at"],
            "by": "avery@example.com",
            "until": None,
        }
    }


def test_a_snooze_lasts_exactly_a_day(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    out = bare.meta_data_health_quiet("silver.customers", "dev", "schema", 2, 1)
    at, until = (
        dt.datetime.fromisoformat(out["at"]),
        dt.datetime.fromisoformat(out["until"]),
    )
    assert until - at == dt.timedelta(days=1) and "." not in out["until"]


def test_acknowledging_checks_each_argument_in_these_words(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    cases = [
        (("silver.customers", "staging", "volume", 1), "env is one of dev, test, prod"),
        (
            ("silver.customers", "dev", "cost", 1),
            "kind is one of freshness, volume, schema",
        ),
        (
            ("silver.customers", "dev", "volume", 1, 2),
            "snooze_days is 0 (acknowledge) or 1",
        ),
        (
            ("silver.customers", "dev", "volume", 1, -1),
            "snooze_days is 0 (acknowledge) or 1",
        ),
        (
            ("silver.customers", "prod", "volume", 1),
            "silver.customers isn't on the Data health list for prod",
        ),
    ]
    for args, said in cases:
        with pytest.raises(ValueError) as e:
            bare.meta_data_health_quiet(*args)
        assert str(e.value) == said
    for kind in ("freshness", "volume", "schema"):
        bare.meta_data_health_quiet("silver.customers", "dev", kind, 1)
    assert len(bare._dh_read()["quiet"]) == 3


# ---- the list


def test_the_list_checks_the_environment(bare):
    with pytest.raises(ValueError) as e:
        bare.meta_data_health_list("staging")
    assert str(e.value) == "env is one of dev, test, prod"


def test_the_list_without_a_project_says_so(tmp_path):
    s = Service(root=tmp_path / "app")
    out = s.meta_data_health_list("prod")
    assert (
        out["env"] == "prod"
        and out["tables"] == []
        and out["entities"] == []
        and out["no_project"] is True
    )
    assert out["rule"]["minimum"] == 10


def test_the_list_shows_only_that_environment_s_tables_and_every_entity_sorted(bare):
    bare.meta_data_health_watch("silver.customers", "prod")
    bare.meta_data_health_watch("gold.dim_customer", "dev")
    out = bare.meta_data_health_list()
    assert [t["entity"] for t in out["tables"]] == ["gold.dim_customer"]
    assert out["entities"] == [
        "gold.dim_customer",
        "silver.customers",
        "silver.customers2",
    ]
    assert (
        "reset" not in out and "no_project" not in out and out["rule"]["minimum"] == 10
    )


def test_a_table_gone_from_the_project_says_how_to_take_it_off(bare):
    bare._dh_write({"watched": [{"entity": "silver.gone", "env": "dev"}], "quiet": {}})
    [t] = bare.meta_data_health_list()["tables"]
    assert t == {
        "entity": "silver.gone",
        "env": "dev",
        "state": "not_in_project",
        "why": "silver.gone isn't in the metadata project any more. Stop watching it to take it off the list.",
    }


def test_a_check_that_fails_says_why(bare):
    def broken(name, env):
        raise OSError("OneLake said no")

    bare.meta_data_health = broken
    bare.meta_data_health_watch("silver.customers", "dev")
    [t] = bare.meta_data_health_list()["tables"]
    assert (
        t["entity"] == "silver.customers"
        and t["env"] == "dev"
        and t["state"] == "error"
    )
    assert "OneLake said no" in t["why"]


def _alerting(bare, name, version=5, kind="volume"):
    bare.checks[name] = {
        "entity": name,
        "env": "dev",
        "alerts": [{"kind": kind, "version": version}],
    }


def test_an_acknowledged_alert_is_marked_and_a_snooze_until_later_too(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    _alerting(bare, "silver.customers")
    record = bare.meta_data_health_quiet("silver.customers", "dev", "volume", 5)
    [alert] = bare.meta_data_health_list()["tables"][0]["alerts"]
    assert alert["quiet"] == {k: record[k] for k in ("at", "by", "until")}
    state = bare._dh_read()
    soon = (dt.datetime.now(UTC) + dt.timedelta(minutes=5)).isoformat()
    state["quiet"]["dev|silver.customers|volume|5"]["until"] = soon
    bare._dh_write(state)
    assert (
        bare.meta_data_health_list()["tables"][0]["alerts"][0]["quiet"]["until"] == soon
    )
    state["quiet"]["dev|silver.customers|volume|5"]["until"] = (
        ""  # an empty end is an acknowledgement
    )
    bare._dh_write(state)
    assert "quiet" in bare.meta_data_health_list()["tables"][0]["alerts"][0]
    state["quiet"]["dev|silver.customers|volume|5"]["until"] = (
        dt.datetime.now(UTC) - dt.timedelta(seconds=1)
    ).isoformat()
    bare._dh_write(state)
    assert "quiet" not in bare.meta_data_health_list()["tables"][0]["alerts"][0]


def test_an_alert_in_another_environment_or_of_another_kind_isnt_marked(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    bare.meta_data_health_watch("silver.customers", "prod")
    _alerting(bare, "silver.customers")
    bare.meta_data_health_quiet("silver.customers", "prod", "volume", 5)
    bare.meta_data_health_quiet("silver.customers", "dev", "schema", 5)
    assert "quiet" not in bare.meta_data_health_list()["tables"][0]["alerts"][0]


def test_an_empty_quiet_record_doesnt_mark_an_alert(bare):
    bare._dh_write(
        {
            "watched": [{"entity": "silver.customers", "env": "dev"}],
            "quiet": {"dev|silver.customers|volume|5": {}},
        }
    )
    _alerting(bare, "silver.customers")
    assert "quiet" not in bare.meta_data_health_list()["tables"][0]["alerts"][0]


def test_the_list_says_it_was_started_again_with_the_copy(bare):
    now = dt.datetime.now(UTC).isoformat()
    bare._dh_write({"reset": {"at": now, "copy": "c"}})
    assert bare.meta_data_health_list()["reset"] == {"at": now, "copy": "c"}


# ---- forgetting quiet alerts no alert matches


def test_quiet_alerts_of_a_table_that_reads_are_forgotten_and_others_kept(bare):
    for name in ("silver.customers", "silver.customers2", "gold.dim_customer"):
        bare.meta_data_health_watch(name, "dev")
    bare.checks["silver.customers2"] = {
        "entity": "silver.customers2",
        "env": "dev",
        "state": "no_table",
        "why": "x",
    }
    bare.checks["gold.dim_customer"] = {
        "entity": "gold.dim_customer",
        "env": "dev",
        "state": "fresh",
    }
    _alerting(bare, "silver.customers", version=6)
    for name, version in (
        ("silver.customers", 5),
        ("silver.customers", 6),
        ("silver.customers2", 1),
        ("gold.dim_customer", 2),
    ):
        bare.meta_data_health_quiet(name, "dev", "volume", version)
    bare.meta_data_health_list()
    assert sorted(bare._dh_read()["quiet"]) == [
        "dev|silver.customers2|volume|1",
        "dev|silver.customers|volume|6",
    ]


def test_a_test_environment_s_quiet_alerts_are_left_by_a_dev_refresh(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    bare._dh_write(
        bare._dh_read()
        | {
            "quiet": {
                "test|silver.customers|volume|1": {"at": "x"},
                "devx|silver.customers|volume|1": {"at": "x"},
            }
        }
    )
    bare.meta_data_health_list()
    assert sorted(bare._dh_read()["quiet"]) == [
        "devx|silver.customers|volume|1",
        "test|silver.customers|volume|1",
    ]


def test_nothing_is_saved_when_nothing_is_forgotten_or_nothing_was_read(
    bare, monkeypatch
):
    bare.meta_data_health_watch("silver.customers", "dev")
    _alerting(bare, "silver.customers")
    bare.meta_data_health_quiet("silver.customers", "dev", "volume", 5)
    writes = []
    monkeypatch.setattr(bare, "_dh_write", lambda state: writes.append(state))
    bare.meta_data_health_list()
    assert writes == []
    locks = []
    real = bare._dh_locked
    monkeypatch.setattr(bare, "_dh_locked", lambda: locks.append(1) or real())
    bare._dh_prune("dev", [{"entity": "silver.customers", "state": "error"}])
    assert locks == [] and writes == []
    bare._dh_prune("dev", [{"entity": "silver.customers", "alerts": []}])
    assert locks == [1] and len(writes) == 1 and writes[0]["quiet"] == {}


def test_a_check_still_running_isnt_started_again_until_it_finishes(bare):
    bare.meta_data_health_watch("silver.customers", "dev")
    calls = []
    bare.meta_data_health = lambda name, env: (
        calls.append(name) or {"entity": name, "env": env, "alerts": []}
    )
    bare.meta_data_health_list()
    bare.meta_data_health_list()
    assert calls == [
        "silver.customers",
        "silver.customers",
    ]  # a finished check runs again on the next refresh
    assert list(bare._dh_running) == [("dev", "silver.customers")]


def test_checks_still_waiting_for_a_thread_when_the_time_is_up_are_never_started(
    bare, monkeypatch
):
    names = [f"silver.t{i}" for i in range(5)]
    monkeypatch.setattr(type(bare), "_project", lambda self: _Project(names))
    monkeypatch.setattr(ds, "DATA_HEALTH_LIST_SECONDS", 0.3)
    bare._dh_write(
        {"watched": [{"entity": n, "env": "dev"} for n in names], "quiet": {}}
    )
    release, started = threading.Event(), []

    def check(name, env):
        started.append(name)
        release.wait(5)
        return {"entity": name, "env": env, "alerts": []}

    bare.meta_data_health = check
    try:
        tables = bare.meta_data_health_list()["tables"]
    finally:
        release.set()
    assert [t["state"] for t in tables] == ["error"] * 5
    assert "didn't finish within 0.3 s" in tables[4]["why"]
    futures = [bare._dh_running[("dev", n)] for n in names]
    for f in futures[:4]:
        f.result(timeout=5)
    assert (
        futures[4].cancelled() and sorted(started) == names[:4]
    )  # four at a time: the fifth was never run


# ---- one table's health


def test_one_table_s_health_checks_the_environment_and_entity_first(bare):
    s = type(bare).meta_data_health
    with pytest.raises(ValueError) as e:
        s(bare, "silver.customers", "staging")
    assert str(e.value) == "env is one of dev, test, prod"
    with pytest.raises(ValueError) as e:
        s(bare, "silver.nope", "dev")
    assert str(e.value) == "silver.nope isn't an entity in the metadata project"


# ---- Teams


def test_a_teams_card_has_the_title_bold_and_each_line_small():
    card = ds.teams_card("Late", ["one", "two"])
    [att] = card["attachments"]
    assert (
        card["type"] == "message"
        and att["contentType"] == "application/vnd.microsoft.card.adaptive"
    )
    assert att["contentUrl"] is None and att["content"]["version"] == "1.4"
    assert att["content"]["type"] == "AdaptiveCard"
    assert (
        att["content"]["$schema"]
        == "http://adaptivecards.io/schemas/adaptive-card.json"
    )
    title, one, two = att["content"]["body"]
    assert title == {
        "type": "TextBlock",
        "text": "Late",
        "weight": "Bolder",
        "size": "Medium",
        "wrap": True,
    }
    assert (
        one == {"type": "TextBlock", "text": "one", "wrap": True, "spacing": "Small"}
        and two["text"] == "two"
    )


def test_a_card_of_exactly_twenty_lines_says_nothing_more_and_twenty_one_say_one_more():
    assert (
        len(ds.teams_card("t", ["x"] * 20)["attachments"][0]["content"]["body"]) == 21
    )
    body = ds.teams_card("t", ["x"] * 21)["attachments"][0]["content"]["body"]
    assert len(body) == 22 and body[-1] == {
        "type": "TextBlock",
        "text": "and 1 more in KratosForge",
        "wrap": True,
    }


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.mark.parametrize(
    "status, raises", [(200, False), (299, False), (300, True), (500, True)]
)
def test_a_teams_answer_of_three_hundred_or_more_is_an_error(
    monkeypatch, status, raises
):
    seen = {}

    def send(call, method, sleep, attempts, wait_cap):
        seen.update(method=method, attempts=attempts, wait_cap=wait_cap)
        return _Resp(status)

    monkeypatch.setattr(ds, "send_with_retry", send)
    if raises:
        with pytest.raises(RuntimeError) as e:
            ds.post_teams("https://example.invalid/x", "t", [])
        assert str(e.value) == f"Teams answered {status}"
    else:
        ds.post_teams("https://example.invalid/x", "t", [])
    assert seen == {"method": "POST", "attempts": 3, "wait_cap": 10.0}


def test_teams_posts_wait_five_seconds_to_connect_and_fifteen_to_read(monkeypatch):
    posted = {}

    class Http:
        def post(self, url, json, timeout):
            posted.update(
                url=url,
                timeout=timeout,
                title=json["attachments"][0]["content"]["body"][0]["text"],
            )
            return _Resp(200)

    monkeypatch.setattr(ds.http_session, "session", lambda: Http())
    monkeypatch.setattr(ds, "send_with_retry", lambda call, *a, **k: call())
    ds.post_teams("https://example.invalid/x", "Hello", ["a"])
    assert posted == {
        "url": "https://example.invalid/x",
        "timeout": (5, 15),
        "title": "Hello",
    }
    assert (
        ds.TEAMS_TIMEOUT == (5, 15)
        and ds.TEAMS_ATTEMPTS == 3
        and ds.TEAMS_WAIT_CAP == 10.0
    )


@pytest.mark.parametrize(
    "body, said",
    [
        ({"error": {"message": "bad card"}}, ": bad card"),
        ({"error": "throttled"}, ": throttled"),
        ({"error": {"code": 1}}, ""),
        ({"message": "top level"}, ": top level"),
        ({"error": None, "message": "top level"}, ": top level"),
        ({}, ""),
        (["a list"], ""),
        ("text", ""),
    ],
)
def test_teams_own_error_comes_from_the_error_or_the_message(body, said):
    assert ds._teams_error(_Resp(400, body)) == said


def test_a_long_teams_error_is_cut_to_two_hundred_characters():
    out = ds._teams_error(_Resp(400, {"message": "x" * 500}))
    assert out == ": " + "x" * 199 + "…" and len(out) == 2 + 200


# ---- meaningful diffs (these need a real project)


def test_a_diff_against_the_last_commit_says_when_there_isnt_one_and_is_empty_right_after(
    svc,
):
    import subprocess

    out = svc.meta_diff("head")
    assert out == {
        "against": "head",
        "entities": [],
        "problem": "The project isn't in Git, or nothing is committed yet.",
    }
    root = svc._project().root
    who = ["-c", "user.email=t@e.st", "-c", "user.name=T"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", *who, "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", *who, "commit", "-q", "-m", "start"], cwd=root, check=True)
    assert svc.meta_diff("head") == {"against": "head", "entities": [], "problem": None}


def test_a_diff_against_an_environment_says_nothing_was_promoted_until_it_was(svc):
    svc.meta_set_environment("test", target="local")
    assert svc.meta_diff("test") == {
        "against": "test",
        "entities": [],
        "problem": "Nothing has been promoted to Test yet.",
    }
    svc.meta_promote("dev", "test")
    assert svc.meta_diff("test") == {"against": "test", "entities": [], "problem": None}
    with pytest.raises(ValueError) as e:
        svc.meta_diff("elsewhere")
    assert str(e.value) == "Compare against head or one of dev, test, prod"


def test_a_committed_entity_file_without_a_name_is_named_after_the_file(svc):
    import subprocess

    root = svc._project().root
    extra = root / "metadata" / "entities" / "zzz.toml"
    extra.write_text('owner = "me"\n', encoding="utf-8")
    who = ["-c", "user.email=t@e.st", "-c", "user.name=T"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", *who, "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", *who, "commit", "-q", "-m", "start"], cwd=root, check=True)
    extra.unlink()
    assert [(d["entity"], d["state"]) for d in svc.meta_diff("head")["entities"]] == [
        ("zzz", "removed")
    ]


def test_the_promote_impact_lists_only_the_tables_removed_as_removed(svc):
    svc.meta_set_environment("test", target="local")
    svc.meta_promote("dev", "test")
    text = svc.meta_entity("silver.orders")["text"]
    svc.meta_save_entity(
        "silver.orders", text.replace('owner = "me"', 'owner = "sales"', 1)
    )
    assert svc.meta_promote_impact("dev", "test")["removed"] == []
    project = svc._project()
    project.entities["gold.fact_sales"].path.unlink()
    project.reload()
    out = svc.meta_promote_impact("dev", "test")
    assert (
        out["removed"] == ["gold.fact_sales"]
        and out["problem"] is None
        and out["source"] == "dev"
    )


def test_drift_on_an_entity_source_reads_no_rows_at_all(svc, monkeypatch):
    from fabric_desktop.meta.store import LocalStore

    limits, real = [], LocalStore.scan

    class Lazy:
        def __init__(self, lf):
            self.lf = lf

        def limit(self, n):
            limits.append(n)
            return self.lf.limit(n)

    monkeypatch.setattr(
        LocalStore, "scan", lambda self, *a, **k: Lazy(real(self, *a, **k))
    )
    assert svc.meta_drift("silver.orders")["problem"] is None
    assert limits == [0]


# ---- versions and one table's health in a real project


def test_versions_say_when_in_utc_and_nothing_when_the_log_has_no_time(
    svc, monkeypatch
):
    [latest, *_] = svc.meta_versions("silver.customers")
    at = dt.datetime.fromisoformat(latest["at"])
    assert at.utcoffset() == dt.timedelta(0) and abs(
        dt.datetime.now(UTC) - at
    ) < dt.timedelta(hours=1)
    assert latest["operation"] and latest["version"] >= 0
    project = svc._project()
    store = svc._store(project, "dev")
    monkeypatch.setattr(
        type(store),
        "history",
        lambda self, *a: [
            {"version": 4, "timestamp": 0, "operation": "WRITE"},
            {"version": 3, "timestamp": 1000, "fabric_desktop_run_id": "r1"},
        ],
    )
    monkeypatch.setattr(svc, "_store", lambda p, env: store)
    assert svc.meta_versions("silver.customers") == [
        {"version": 4, "at": None, "operation": "WRITE", "run_id": None},
        {
            "version": 3,
            "at": "1970-01-01T00:00:01+00:00",
            "operation": None,
            "run_id": "r1",
        },
    ]


def test_one_table_s_health_uses_the_promise_only_in_the_environments_it_names(svc):
    svc.meta_set_freshness("silver.customers", within_hours=24, envs=["dev"])
    fresh = svc.meta_data_health("silver.customers")["freshness"]
    assert fresh["within_hours"] == 24 and fresh["state"] == "fresh"
    svc.meta_set_freshness("silver.customers", within_hours=24, envs=["prod"])
    other = svc.meta_data_health("silver.customers")["freshness"]
    assert other["within_hours"] is None and other["state"] == "no_promise"


def test_a_table_not_loaded_yet_says_so_in_these_words(svc, monkeypatch):
    from deltalake.exceptions import TableNotFoundError

    def missing(*a, **k):
        raise TableNotFoundError("no log")

    monkeypatch.setattr(ds, "DeltaTable", missing)
    assert svc.meta_data_health("silver.customers") == {
        "entity": "silver.customers",
        "env": "dev",
        "state": "no_table",
        "why": "There's no Delta table for silver.customers in dev yet.",
    }


def test_a_table_of_exactly_the_row_limit_is_still_compared_in_full(svc, monkeypatch):
    from fabric_desktop.meta import compare as compare_mod

    rows = svc.meta_compare("silver.customers", "dev", "dev")["rows"]["left"]
    assert rows > 1
    monkeypatch.setattr(compare_mod, "LIMIT_ROWS", rows)
    assert svc.meta_compare("silver.customers", "dev", "dev")["method"] == "full"
    monkeypatch.setattr(compare_mod, "LIMIT_ROWS", rows - 1)
    assert svc.meta_compare("silver.customers", "dev", "dev")["method"] == "hashed"


# ---- quarantine


def test_quarantined_rows_come_newest_first_with_undated_ones_last_and_checks_by_count(
    svc,
):
    import polars as pl

    store = svc._store(svc._project(), "dev")
    q = store.read("LH_Silver", "quarantine", "dbo_orders")
    rows = pl.concat(
        [
            q.with_columns(
                pl.lit(qid).alias("_qid"),
                pl.lit(day).cast(pl.Date).alias("_loaded_at"),
                pl.lit(check).alias("_check"),
            )
            for qid, day, check in (
                ("old", dt.date(2026, 1, 1), "a"),
                ("none", None, "b"),
                ("new", dt.date(2026, 3, 1), "b"),
                ("mid", dt.date(2026, 2, 1), "b"),
            )
        ]
    )
    store.write("LH_Silver", "quarantine", "dbo_orders", rows, mode="overwrite")
    out = svc.meta_quarantine("silver.orders")
    assert [r["_qid"] for r in out["rows"]] == ["new", "mid", "old", "none"]
    assert list(out["checks"].items()) == [("b", 3), ("a", 1)] and out["total"] == 4
    assert list(out["rows"][0])[:4] == ["_qid", "_check", "_run_id", "_loaded_at"]
    assert not {"_qid", "_check", "_run_id", "_loaded_at"} & set(out["columns"])
    assert [
        r["_qid"] for r in svc.meta_quarantine("silver.orders", limit=2)["rows"]
    ] == ["new", "mid"]


def test_an_emptied_value_is_replayed_as_no_value_and_a_number_as_text(svc):
    [row] = svc.meta_quarantine("silver.orders")["rows"]
    out = svc.meta_replay(
        "silver.orders",
        "dev",
        [row["_qid"]],
        {row["_qid"]: {"amount": "", "discount": 3}},
    )
    assert (
        out["held"] == 1
    )  # a return with no amount still fails its check, so it is held again, edited
    [held] = svc.meta_quarantine("silver.orders")["rows"]
    assert (
        row["amount"] is not None
        and held["amount"] is None
        and held["discount"] == "3.0"
    )


# ---- row counts, alerts settings


def test_rows_from_the_statistics_leave_out_the_rows_deletion_vectors_remove(
    monkeypatch,
):
    import types

    import pyarrow as pa

    from fabric_desktop import delta_read

    monkeypatch.setattr(delta_read, "deleted_rows", lambda table: 3)
    table = types.SimpleNamespace(
        get_add_actions=lambda flatten: pa.table(
            {"path": ["a", "b"], "num_records": pa.array([10, 5], pa.int64())}
        )
    )
    assert ds._rows_in(table) == 12


def test_alerts_not_set_say_so_with_nothing_to_show(tmp_path):
    s = Service(root=tmp_path / "app")
    assert s.meta_alerts() == {"set": False, "kind": "", "label": ""}


def test_setting_alerts_returns_what_they_now_are(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    url = "https://example.webhook.office.com/x"
    out = s.meta_set_alerts(url)
    assert out == s.meta_alerts() and out["set"] is True and out["kind"] == "teams"
    assert out["label"] == "example.webhook.office.com" and "unreadable" not in out
    assert s.meta_set_alerts("") == {"set": False, "kind": "", "label": ""}
    monkeypatch.setattr(ds.sealed_settings, "unreadable", lambda settings, key: True)
    assert s.meta_alerts()["unreadable"] is True
