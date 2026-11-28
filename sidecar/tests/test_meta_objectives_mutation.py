"""Mutation-testing gaps in meta/objectives.py (docs/quality/mutation-testing.md, batch 49)."""

from fabric_desktop.meta import objectives

ENVS = ("dev", "test", "prod")


def spec(**o):
    return {"objectives": o}


def runs(*statuses):
    return [{"status": s} for s in statuses]


def test_five_runs_are_the_fewest_that_say_anything():
    assert objectives.MIN_RUNS == 5
    obj = objectives.objective(spec(success_rate=80, runs=20))
    assert objectives.success(obj, runs(*["ok"] * 4))["status"] == "not_enough"
    assert objectives.success(obj, runs(*["ok"] * 5))["status"] == "met"
    assert objectives.problems("t", spec(runs=5), ENVS) == []
    assert objectives.problems("t", spec(runs=4), ENVS) == ["t: objectives runs must be a whole number of at least 5"]


def test_objectives_that_are_not_a_table_get_one_message():
    assert objectives.problems("t", {"objectives": "high"}, ENVS) == [
        't: [objectives] must be a table, like criticality = "high"']


def test_the_rate_is_rounded_to_one_place():
    obj = objectives.objective(spec(success_rate=60, runs=20))
    out = objectives.success(obj, runs("ok", "ok", "failed", "ok", "ok", "failed"))
    assert out["rate"] == 66.7
    assert out["status"] == "met" and out["counted"] == 6 and out["ok"] == 4


def test_a_success_rate_is_a_percent_above_0_up_to_100():
    message = ["t: objectives success_rate is a percent above 0, up to 100"]
    assert objectives.problems("t", spec(success_rate=100), ENVS) == []
    assert objectives.problems("t", spec(success_rate=0.5), ENVS) == []
    for bad in (0, 101, 100.5, -1, True, "95"):
        assert objectives.problems("t", spec(success_rate=bad), ENVS) == message, bad
