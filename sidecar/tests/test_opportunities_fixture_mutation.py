"""The demonstration fixture in the shapes Fabric returns: what mutation testing found the share tests
didn't pin (docs/quality/mutation-testing.md, batch 78). Nothing reaches a tenant."""

import base64
import datetime as dt
import json

import pytest

from fabric_desktop.opportunities import fixture as fx


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


AS_OF = dt.datetime(2026, 11, 1, 13, 45, 30, tzinfo=dt.timezone.utc)


def test_text_is_base64_of_its_utf8_bytes():
    assert fx._b64("Café ☕") == base64.b64encode("Café ☕".encode()).decode("ascii")
    assert base64.b64decode(fx._b64("Café ☕")).decode("utf-8") == "Café ☕"


def test_a_notebook_becomes_an_nbformat_4_5_ipynb_definition():
    out = fx._ipynb({"cells": ["a = 1\n", "print(a)\n"], "default_lakehouse": "lh-1"})
    assert out["definition"]["format"] == "ipynb"
    (part,) = out["definition"]["parts"]
    assert part["path"] == "notebook-content.ipynb" and part["payloadType"] == "InlineBase64"
    doc = json.loads(base64.b64decode(part["payload"]))
    assert doc["nbformat"] == 4 and doc["nbformat_minor"] == 5
    assert doc["cells"] == [{"cell_type": "code", "source": c, "metadata": {}, "outputs": [], "execution_count": None}
                            for c in ["a = 1\n", "print(a)\n"]]
    assert doc["metadata"] == {"dependencies": {"lakehouse": {"default_lakehouse": "lh-1"}}}


def test_a_pipeline_becomes_one_inline_part():
    out = fx._pipeline({"properties": {"activities": []}})
    (part,) = out["definition"]["parts"]
    assert part["path"] == "pipeline-content.json" and part["payloadType"] == "InlineBase64"
    assert json.loads(base64.b64decode(part["payload"])) == {"properties": {"activities": []}}


def test_daily_runs_go_back_one_day_at_a_time_from_the_day_before_at_six():
    jobs = fx._jobs({"daily_scheduled": 3, "seconds": 90}, AS_OF)
    assert [j["id"] for j in jobs] == ["fixture-1", "fixture-2", "fixture-3"]
    starts = [dt.datetime.fromisoformat(j["startTimeUtc"]) for j in jobs]
    assert starts == [dt.datetime(2026, 10, d, 6, 0, 0, tzinfo=dt.timezone.utc) for d in (31, 30, 29)]
    for j, start in zip(jobs, starts, strict=True):
        assert dt.datetime.fromisoformat(j["endTimeUtc"]) == start + dt.timedelta(seconds=90)
        assert j["invokeType"] == "Scheduled" and j["status"] == "Completed"


def test_one_daily_run_and_no_schedule():
    assert len(fx._jobs({"daily_scheduled": 1, "seconds": 1}, AS_OF)) == 1
    assert fx._jobs({}, AS_OF) == []


def test_the_loaded_demo_has_every_scheduled_run_and_only_the_price_it_uses():
    out = fx.load()
    doc = json.loads(fx.PATH.read_text(encoding="utf-8"))
    as_of = dt.datetime.fromisoformat(doc["as_of"])
    assert out["now"] == as_of
    assert out["price"] == {k: doc["price"][k] for k in ("price_per_cu_hour", "currency")}
    for item in doc["items"]:
        spec = doc["jobs"].get(item["id"])
        assert len(out["source"]["jobs"][item["id"]]) == (spec["daily_scheduled"] if spec else 0)
