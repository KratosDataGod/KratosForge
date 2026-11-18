"""Workload ids, times, observations and read errors: edges the main tests don't pin down (mutation batch 141,
docs/quality/mutation-testing.md)."""

import datetime as dt

import pytest

from fabric_desktop.workloads import model

UTC = dt.timezone.utc


def test_every_kind_of_id_reads_back():
    assert model.parse_id(model.pipeline_id("C:/r/A.DataPipeline")) == {"kind": "pipeline", "path": "C:/r/A.DataPipeline"}
    assert model.parse_id(model.notebook_id("Load")) == {"kind": "notebook", "name": "Load"}
    assert model.parse_id("meta:sales:orders") == {"kind": "meta_entity", "project": "sales", "entity": "orders"}
    assert model.parse_id("fabric:w:i") == {"kind": "fabric_item", "workspace_id": "w", "item_id": "i"}


@pytest.mark.parametrize("wid", ["meta:sales:", "meta::orders", "fabric:w:", "fabric::i", "notebook:", "pipeline:", "x:y"])
def test_an_id_missing_a_part_isnt_one(wid):
    with pytest.raises(ValueError, match="isn't a workload id"):
        model.parse_id(wid)


def test_a_time_as_seconds_is_utc_and_a_naive_one_is_read_as_utc():
    assert model.parse_time(60) == dt.datetime(1970, 1, 1, 0, 1, tzinfo=UTC)
    assert model.parse_time("2026-11-18T10:00:00") == dt.datetime(2026, 11, 18, 10, tzinfo=UTC)
    aware = model.parse_time("2026-11-18T10:00:00+02:00")
    assert aware.utcoffset() == dt.timedelta(hours=2)


def test_an_unknown_observation_state_is_refused():
    with pytest.raises(ValueError, match="unknown observation state"):
        model.observation("s", "fine")


def test_a_cached_read_exactly_its_max_age_old_is_still_cached():
    at = dt.datetime(2026, 11, 18, 12, tzinfo=UTC)
    read = at - dt.timedelta(seconds=60)
    assert model.observation("s", "cached", read, max_age_s=60, at=at)["state"] == "cached"
    assert model.observation("s", "cached", read, max_age_s=59, at=at)["state"] == "stale"


class Http(Exception):
    def __init__(self, status):
        super().__init__("the service said no")
        self.status = status


def test_a_401_or_429_status_is_read_from_the_error_not_its_words():
    assert model.classify_error(Http(401))[0] == "unauthorized"
    assert model.classify_error(Http(403))[0] == "unauthorized"
    assert model.classify_error(Http(429))[0] == "throttled"
    assert model.classify_error(Http(503))[0] == "offline"
