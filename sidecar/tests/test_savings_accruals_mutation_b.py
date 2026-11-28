"""savings/accruals at its edges, from the mutation run (batch 470): every check on an attribution
result refuses with its own reason, the gross and net bounds at their tolerance, the layer order,
and what an event and the totals keep."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.optimize.shared import record_id
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings.corrections import TOLERANCE as TOL
from test_savings_accruals import measured, pc_run


def layer(name, order, cu, cls=ms.ESTIMATED, **extra):
    return {"baseline_version": "b1", "classification": cls, "cu_hours": cu, "event_type": at.ACCRUAL,
            "execution_id": "e1", "layer": name, "layer_order": order,
            "ledger_event_id": record_id("savings", at.ACCRUAL, "e1", "nb:sales", name, "b1", None, None),
            "optimization_id": None, "optimization_ids": None, "reason": "", "workload_key": "nb:sales"} | extra


def result(layers, gross=None, net=None, overhead=0.0, baseline=10.0, b_cls=ms.MEASURED, complete=True):
    known = [x["cu_hours"] for x in layers if x["cu_hours"] is not None]
    gross = (sum(known) if known else None) if gross is None else gross
    net = (None if gross is None else gross - (overhead or 0)) if net is None else net
    return {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1", "at": "2026-11-10T00:00:00Z",
            "layers": layers, "baseline_cu_hours": baseline, "baseline_classification": b_cls,
            "gross_cu_hours": gross, "overhead_cu_hours": overhead, "net_cu_hours": net, "complete": complete,
            "ledger_ready": b_cls == ms.MEASURED}


def test_the_helper_makes_a_result_accrue_takes():
    got = ac.accrue(result([layer("local", 0, 7.0)]))
    assert got["gross_cu_hours"] == 7.0 and got["net_cu_hours"] == 7.0 and got["writable"] is True


def test_a_result_that_isnt_a_dict_is_refused():
    with pytest.raises(ValueError, match="one attribution result, a dict"):
        ac.accrue([pc_run()])


@pytest.mark.parametrize("b_cls,b", [("Guessed", 10.0), (ms.MEASURED, None), (ms.UNKNOWN, 10.0)])
def test_a_baseline_must_be_a_known_kind_with_a_figure_or_unknown_without(b_cls, b):
    r = result([layer("local", 0, None, ms.UNKNOWN)], baseline=b, b_cls=b_cls, overhead=None, complete=False)
    r["ledger_ready"] = False
    with pytest.raises(ValueError, match="a baseline is Measured or Estimated with a figure"):
        ac.accrue(r)


def test_cu_avoided_without_a_baseline_is_refused():
    r = result([layer("local", 0, 7.0)], baseline=None, b_cls=ms.UNKNOWN)
    with pytest.raises(ValueError, match="without a baseline to cap it"):
        ac.accrue(r)
    gross_only = result([layer("local", 0, None, ms.UNKNOWN)], gross=1.0, baseline=None, b_cls=ms.UNKNOWN)
    with pytest.raises(ValueError, match="without a baseline to cap it"):
        ac.accrue(gross_only)


def test_a_gross_that_doesnt_fit_the_layers_is_refused():
    r = result([layer("local", 0, 7.0)])
    r["gross_cu_hours"] = r["net_cu_hours"] = None
    with pytest.raises(ValueError, match="gross doesn't fit its layers"):
        ac.accrue(r)


def test_a_gross_without_a_net_is_refused():
    r = result([layer("local", 0, 7.0)])
    r["net_cu_hours"] = None
    with pytest.raises(ValueError, match="a gross or a net, but not both"):
        ac.accrue(r)


def test_a_layer_for_another_workload_or_baseline_is_refused():
    with pytest.raises(ValueError, match="another workload or baseline version"):
        ac.accrue(result([layer("local", 0, 7.0, workload_key="nb:other")]))
    with pytest.raises(ValueError, match="another workload or baseline version"):
        ac.accrue(result([layer("local", 0, 7.0, baseline_version="b9")]))
    assert ac.accrue(result([layer("local", 0, 7.0, workload_key=None, baseline_version=None)]))["events"]


def test_net_is_complete_only_when_the_result_says_so():
    assert ac.accrue(result([layer("local", 0, 7.0)], complete=False))["net_complete"] is False
    assert ac.accrue(result([layer("local", 0, 7.0)], complete=True))["net_complete"] is True


def test_gross_at_its_bounds():
    ac.accrue(result([layer("local", 0, 0.0)], gross=-TOL, net=-TOL))  # rounding below 0 is still 0
    ac.accrue(result([layer("local", 0, 0.0)], gross=10.0 + TOL, net=0.0))  # and just over the baseline
    with pytest.raises(ValueError, match="outside 0 to its baseline"):
        ac.accrue(result([layer("local", 0, 0.0)], gross=-2 * TOL, net=0.0))
    with pytest.raises(ValueError, match="outside 0 to its baseline"):
        ac.accrue(result([layer("local", 0, 0.0)], gross=10.0 + 2 * TOL, net=0.0))


def test_layers_may_add_up_to_the_baseline_plus_their_rounding():
    edge = 10.0 + TOL
    ac.accrue(result([layer("local", 0, edge)], gross=edge, net=0.0))
    with pytest.raises(ValueError, match="layers add up to"):
        ac.accrue(result([layer("local", 0, 10.0 + 2 * TOL)], gross=10.0, net=0.0))


def test_net_at_its_bounds():
    ac.accrue(result([layer("local", 0, 5.0)], gross=5.0, net=5.0 + TOL))
    ac.accrue(result([layer("local", 0, 5.0)], gross=5.0, net=-TOL))
    with pytest.raises(ValueError, match="its net"):
        ac.accrue(result([layer("local", 0, 5.0)], gross=5.0, net=5.0 + 2 * TOL))
    with pytest.raises(ValueError, match="its net"):
        ac.accrue(result([layer("local", 0, 5.0)], gross=5.0, net=-2 * TOL))


def test_layers_go_in_their_order_then_by_name_and_unordered_ones_last():
    got = ac.accrue(result([layer("a", 1, 1.0), layer("z", 0, 1.0), layer("m", None, 1.0), layer("b", 2, 1.0)]))
    assert [e["layer"] for e in got["events"]] == ["z", "a", "b", "m"]
    assert [e["layer_order"] for e in got["events"]] == [0, 1, 2, None]


def test_an_event_keeps_its_layers_reason():
    r = result([layer("local", 0, 7.0, reason="ran on this PC")])
    assert ac.accrue(r)["events"][0]["reason"] == "ran on this PC"
    assert ac.accrue(result([layer("local", 0, 7.0, reason=None)]))["events"][0]["reason"] == ""


def test_the_helper_round_trips_a_real_attribution():
    real = pc_run(overhead=measured(0.5))
    mine = result(copy.deepcopy(real["layers"]), overhead=0.5)
    assert ac.accrue(mine)["events"] == ac.accrue(real)["events"]
