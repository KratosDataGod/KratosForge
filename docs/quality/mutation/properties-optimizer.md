# Property tests: the optimizer never invents a saving

Before batches 198 to 201, `tests/test_optimizer_properties.py` checks four properties of the figures that feed Optimize and the `fdopt_` tables, over generated inputs: calibration (`calibration.py`), what an opportunity could avoid and its value (`opportunities/impact.py`), totals, units and rounding (`measurement.py`), and the duplicate notebook analyzer (`opportunities/analyzers.py`). hypothesis isn't in the CI lock, so each property draws 200 cases from `random.Random` with seeds 1, 7 and 42, and puts the inputs of a failure in its message. The file runs in about 4 s.

- **No saving is negative, and none is more than what it avoids.**
  - A run on this PC avoids at least 0, and never more or less than the baselines it comes from (scaled by rows read only when both sides have rows).
  - An opportunity's monthly CU is between 0 and the imported usage of the workloads it removes, scaled to 30 days.
  - A duplicate never counts the kept notebook: however much the keeper used, the figure is the same.
  - A value is CU hours times the price. A negative price is refused, never turned into a negative value.
  - A total is never less than 0 and never more than its parts, and scenario figures are never added to measured or estimated ones.
- **The same inputs always give the same output.** Calibration, impact, measurement ids and the kept notebook are the same every time, and the same when baselines, parts or notebooks come in another order.
- **Rounding never makes a figure bigger than half a unit in its sixth decimal.** Converting CU-seconds to CU-hours and back never grows a figure, and a 0 stays 0. One measured part is rounded before it is scaled up to 30 days (a period of a day or less), so a monthly figure can be up to 30 × 0.0000005 above the unrounded figure for each part. The tests state that tolerance. It's 0.000015 CU-seconds at most, not a saving.
- **With no evidence the figure is Unknown with a reason, never 0.** That covers no usable baseline, no imported usage, no run history, no price, a partial reduction, an all-unknown total, and copies with no known lakehouse, which claim nothing.

All four held. No bug was found, so there is no xfail.

