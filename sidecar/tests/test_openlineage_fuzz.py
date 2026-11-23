"""Fuzz the OpenLineage export's inputs. KratosForge writes OpenLineage events and reads none: what it reads is the
run log (run_id, entity, status, started_at, seconds), from this PC's store or a lakehouse anyone in the
workspace can write, and the project's sources and expressions, which safe_origin and safe_text clean before an
event leaves the PC.

The property: run_events turns any run log rows into events or ``left_out`` rows, one per entity (none lost, none
made up), every event is strict JSON with a readable eventTime, and the cleaners never raise. Fixed seeds, so a
failure names its seed."""

from __future__ import annotations

import datetime as dt
import json
import random

import pytest

from fabric_desktop.meta import openlineage
from fabric_desktop.service import Service

# What a run log column can hold: text (any), a number for seconds, or null.
ODD = [None, True, False, 0, -1, 1.5, 10 ** 30, 1e308, float("nan"), float("inf"), float("-inf"), "", "x" * 10_000,
       "\x00", "é", "\ufeff", "0001-01-01T00:00:00+01:00", "0001-01-01T00:00:00-23:59", "9999-12-31T23:59:59-01:00",
       "9999-12-31T23:59:59+01:00", "9999-12-31T23:59:59", "2026-11-14", "2026-11-14T10:00:00Z", "2026-W01",
       "not a date", "2026-11-14T10:00:00+24:00", "ok", "failed", "running", "OK", "silver.orders ",
       "00000000-0000-0000-0000-000000000007", "{00000000-0000-0000-0000-000000000007}", "../../etc", "C:\\Users\\x"]


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("ol")
    s = Service(root=root / "app")
    s.meta_init(str(root / "crm"), "3")
    return s._project()


def test_random_run_logs_give_one_event_or_reason_per_entity(project):
    entities = sorted(project.entities)
    for seed in range(1500):
        r = random.Random(seed)
        runs = []
        for _ in range(r.randint(0, 8)):
            row = {"run_id": f"r{r.randint(0, 3)}", "entity": r.choice(entities), "status": r.choice(["ok", "failed"]),
                   "started_at": "2026-11-14T10:00:00+00:00", "seconds": 1}
            for key in list(row):
                if r.random() < .35:
                    row[key] = r.choice(ODD)
                elif r.random() < .05:
                    del row[key]
            runs.append(row)
        out = openlineage.run_events(project, runs, "dev", r.choice([None, "ws-1"]))
        case = f"seed {seed}"
        named = [e["job"]["name"] for e in out["events"]] + [x["entity"] for x in out["left_out"]]
        assert sorted(named) == entities, case
        json.dumps(out, allow_nan=False)  # strict JSON: no NaN or Infinity reaches a consumer
        for e in out["events"]:
            assert e["eventType"] in ("COMPLETE", "FAIL"), case
            dt.datetime.fromisoformat(e["eventTime"])
            assert e["run"]["runId"] and e["eventTime"], case


PIECES = ["abfss://", "c@acct.dfs.core.windows.net", "/", "\\", "C:", "..", "~", "file:", "jdbc:", "oracle:thin:", "@",
          ":", "[", "]", "::1", "?", "#", ";", "user=", "pwd=", "{", "}", "'", '"', "http://", "%", "%zz", "\x00",
          "\ufeff", "é", "a", "65536", "9" * 30, "[::1", " ", "\n", "\r", "=", "AccountKey=", "a@b.com", "*", "(", "$"]


def test_random_sources_and_expressions_are_cleaned_never_a_crash():
    for seed in range(8000):
        r = random.Random(seed)
        text = "".join(r.choice(PIECES) for _ in range(r.randint(0, 14)))
        for clean in (openlineage.safe_origin, openlineage.safe_text):
            assert isinstance(clean(text), str), f"seed {seed}: {text!r}"
        assert isinstance(openlineage.safe_origin([text, None, 5]), str), f"seed {seed}"
