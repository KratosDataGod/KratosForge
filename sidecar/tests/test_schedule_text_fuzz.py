"""Fuzz the schedule text a person, the app's form or another tool hands the engine: every N minutes, times of day,
days of the week (fabric_run.schedule_configuration, which local_schedules, schedule_service, cloud_service and
agent_config all go through), when such a schedule runs next on this PC (local_schedules.next_run) in time zones whose
clocks jump, and the slots a shared schedule names (shared_schedules).

The properties: a schedule is read or refused with a ValueError, never another exception, and never taken as a
different schedule (1.5 minutes as one minute, true as every minute, a fullwidth \uff10\uff16:\uff10\uff10). Its next run is the first
moment still to come that it names: never one already past, never one after a run it skipped. Every loop uses fixed
seeds, so a failure names its seed and reproduces exactly."""

from __future__ import annotations

import datetime as dt
import os
import random
import time

import pytest

from fabric_desktop import local_schedules, shared_schedules
from fabric_desktop.fabric_run import MAX_EVERY_MINUTES, WEEKDAYS, schedule_configuration
from fabric_desktop.local_schedules import next_run

# Zones whose clocks jump by an hour, half an hour (Lord Howe), two hours (Troll), at midnight (Santiago, Havana,
# Cairo), or back and forth in one year (Casablanca).
ZONES = ("America/New_York", "Australia/Lord_Howe", "Pacific/Chatham", "America/Santiago", "Antarctica/Troll",
         "America/St_Johns", "Africa/Casablanca", "America/Havana", "Africa/Cairo", "Europe/London")
ODD = [None, True, False, 0, -1, 1.5, 59.9, 0.5, 10 ** 30, 1e308, float("nan"), float("inf"), "", "x", "1e3", "\u0661\u0665",
       " 15 ", "0x10", [], {}, [None], [1], ["06:00"], "06:00", "Monday", ["Monday"], ["\uff10\uff16:\uff10\uff10"], ["06:0\uff10"],
       ["24:00"], ["6:00"], ["06:00\n"], ["monday "], ["Mon"], (("06:00",)), MAX_EVERY_MINUTES + 1]


@pytest.fixture
def zone():
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset, which Windows doesn't have")
    old = os.environ.get("TZ")

    def use(name: str) -> None:
        os.environ["TZ"] = name
        time.tzset()
    yield use
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


# ---- what a schedule says

@pytest.mark.parametrize("minutes", [1.5, 59.9, True, 0.5, float("nan"), float("inf"), "1e3", "\u0661\u0665", [15], {}])
def test_minutes_that_arent_a_whole_number_are_refused_not_rounded(minutes):
    # int() made 1.5 one minute, 59.9 fifty-nine, true every minute; nan, inf and a list were other exceptions
    with pytest.raises(ValueError, match="aren't a whole number") as caught:
        schedule_configuration("cron", every_minutes=minutes)
    assert "invalid literal" not in str(caught.value) and "convert" not in str(caught.value)


def test_whole_minutes_as_a_number_or_as_digits_are_kept():
    assert schedule_configuration("cron", every_minutes=15)["interval"] == 15
    assert schedule_configuration("cron", every_minutes=" 15 ")["interval"] == 15


@pytest.mark.parametrize(("times", "weekdays"), [([None], None), ([6], None), (6, None), ("06:00", None),
                                                 (["06:00"], 1), (["06:00"], True), (float("inf"), None)])
def test_times_and_days_that_arent_lists_of_text_are_refused_in_words(times, weekdays):
    with pytest.raises(ValueError, match="wasn't saved, because its (times|days) aren't a list"):
        schedule_configuration("weekly", times=times, weekdays=weekdays)


@pytest.mark.parametrize("text", ["\uff10\uff16:\uff10\uff10", "06:0\u0660", "\u0660\u0666:\u0660\u0660"])
def test_times_written_with_other_digits_are_refused(text):
    # \d took any digit after the first, so "06:0" and an Arabic-Indic zero was a valid time that Fabric and
    # Task Scheduler then refuse
    with pytest.raises(ValueError, match="HH:MM"):
        schedule_configuration("daily", times=[text])


@pytest.mark.parametrize("seed", range(4))
def test_any_schedule_from_the_form_is_read_or_refused_and_never_changed(seed):
    rnd = random.Random(seed)
    for i in range(500):
        raw = {"kind": rnd.choice(["cron", "daily", "weekly", "monthly", None, 1])}
        for key in ("times", "every_minutes", "weekdays"):
            if rnd.random() < .7:
                raw[key] = rnd.choice(ODD)
        try:
            config = local_schedules.task_configuration(raw)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001 - the property under test
            pytest.fail(f"seed {seed} case {i} {raw!r}: {type(exc).__name__}: {exc}")
        if config["type"] == "Cron":
            given = raw["every_minutes"]
            assert config["interval"] == int(given) and not isinstance(given, (bool, float)), (seed, i, raw)
        else:
            assert all(t.isascii() for t in config["times"]), (seed, i, raw)
        next_run(config)
        local_schedules._triggers(config)


# ---- when it runs next

def _transitions(year: int = 2026) -> list[float]:
    """The moments this zone's offset changes in ``year``, to a quarter of an hour."""
    start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    out, before = [], time.localtime(start).tm_gmtoff
    for q in range(0, 366 * 96):
        offset = time.localtime(start + q * 900).tm_gmtoff
        if offset != before:
            out.append(start + q * 900)
            before = offset
    return out


def _first_to_come(config: dict, now: dt.datetime) -> float:
    """The first moment after ``now`` that a daily or weekly schedule names, by brute force over the next days."""
    days = set(range(7)) if config["type"] == "Daily" else {WEEKDAYS.index(d) for d in config["weekdays"]}
    dates = [now.date() + dt.timedelta(days=k) for k in range(9)]
    moments = [dt.datetime.combine(d, dt.time(*map(int, t.split(":")))).timestamp()
               for d in dates if d.weekday() in days for t in config["times"]]
    return min(m for m in moments if m > now.timestamp())


def test_from_inside_the_repeated_hour_a_daily_time_already_past_isnt_named_again(zone):
    zone("America/New_York")  # 2026-11-01: 02:00 EDT becomes 01:00 EST
    now = dt.datetime(2026, 11, 1, 1, 15, fold=1)  # the second 01:15; 01:30 came round 45 minutes ago
    nxt = next_run({"type": "Daily", "times": ["01:30"]}, now)
    assert nxt.timestamp() > now.timestamp()  # was 01:30 the first time round: due at once, a second run that day
    assert nxt.date() == dt.date(2026, 11, 2)


def test_a_time_after_the_skipped_hour_isnt_lost_behind_one_in_it(zone):
    zone("America/New_York")  # 2026-03-08: 02:00 EST becomes 03:00 EDT, so 02:30 runs at 03:30
    config = {"type": "Daily", "times": ["02:30", "03:15"]}
    first = next_run(config, dt.datetime(2026, 3, 8, 0, 0))
    assert (first.hour, first.minute) == (3, 15)  # was 02:30 (03:30), and from there 03:15 was past: never run
    second = next_run(config, dt.datetime.fromtimestamp(first.timestamp()))
    assert dt.datetime.fromtimestamp(second.timestamp()) == dt.datetime(2026, 3, 8, 3, 30)


@pytest.mark.parametrize("name", ZONES)
def test_the_next_run_is_the_first_moment_still_to_come(zone, name):
    zone(name)
    rnd = random.Random(name)
    jumps = _transitions()
    for i in range(300):
        moment = (rnd.choice(jumps) if jumps else time.time()) + rnd.randrange(-26 * 60, 26 * 60) * 60
        now = dt.datetime.fromtimestamp(moment).replace(second=0, microsecond=0)
        kind = rnd.choice(["Daily", "Weekly", "Cron"])
        if kind == "Cron":
            step = rnd.choice([1, 5, 15, 45, 60, 90, 240, 1440])
            nxt = next_run({"type": "Cron", "interval": step, "startDateTime": "2025-12-01T00:00:00"}, now)
            gap = nxt.timestamp() - now.timestamp()
            assert 0 < gap <= step * 60 + 7200, (name, i, now, step, nxt)  # a jump waits at most its own length
            continue
        config = {"type": kind, "times": sorted({f"{rnd.randrange(24):02d}:{rnd.choice([0, 15, 30, 45, rnd.randrange(60)]):02d}"
                                                  for _ in range(rnd.randint(1, 4))})}
        if kind == "Weekly":
            config["weekdays"] = rnd.sample(WEEKDAYS, rnd.randint(1, 7))
        nxt = next_run(config, now)
        assert nxt is not None and nxt.timestamp() == _first_to_come(config, now), (name, i, config, now, nxt)


# ---- shared schedules

@pytest.mark.parametrize("seed", range(2))
def test_a_shared_schedule_names_the_same_slot_for_any_moment_in_it(seed):
    rnd = random.Random(seed)
    for _ in range(2000):
        minutes = rnd.choice([1, 7, 15, 60, 1440, 10_080, rnd.randint(1, MAX_EVERY_MINUTES)])
        when = {"kind": "cron", "every_minutes": minutes}
        now = rnd.uniform(0, 4e9)
        due = shared_schedules.next_aligned(when, now)
        assert now < due <= now + minutes * 60 and due % (minutes * 60) == 0, (seed, minutes, now)
        name, deadline = shared_schedules.slot(when, due)
        later = shared_schedules.slot(when, due + rnd.uniform(0, minutes * 60 - 1))
        assert later[0] == name and deadline > due  # agents noticing it seconds apart write one request
        assert len(shared_schedules.request_id("x" * shared_schedules.MAX_ID, name)) <= 64

