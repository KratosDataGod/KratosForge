# Savings properties: the savings package never invents a saving

`savings/attribution.py`, `savings/recurring.py` and `savings/pricing.py` turn CU figures into avoided CU, period figures, a monthly projection and a priced value. `tests/test_savings_properties.py` checks four promises over random inputs. hypothesis isn't in the test lock, so each property draws its cases from a seeded `random.Random` (seeds 7, 11 and 2026, a few hundred cases each). The same cases come back on every run, a failing case is printed in its assert message, and the whole file runs in about 2 s.

The inputs are runs on a PC or in Fabric, with measured, estimated or no baselines. They include optimizations whose afters are lower, higher (a regression) or not known yet, and optimizations reverted, rejected, superseded or not yet in place when the run happened. This PC's figure and the overhead are each known or not. Values include ones next to a 6-decimal half point (`2.5e-7`, `1.5e-6`, `1.0000005`, `9.9999995`).

1. **No saving is negative, and none is more than what it saves from.** Gross is between 0 and the baseline, and net is between 0 and gross. Gross is the known layers' sum kept between 0 and the baseline, and only a regression layer is negative. This PC's layer is at most what the optimized workload needs, an unattributed layer is at most the baseline, and a Fabric run has neither. Period figures are the sums of their runs, with a run from a second device counted once. A projection is never negative and is the known runs' average times their rate. A price is CU times the rate in effect when the run happened.
2. **The same inputs give the same output.** The same attribution comes back every time, layer ids included, in any order of optimizations. Period figures, projections and priced totals don't depend on the order of runs or price versions, and a price version's id depends only on its price and dates.
3. **Rounding never creates a saving.** Rounded gross never exceeds the baseline, and the rounded layers never add up past it by more than their rounding. A half-point value isn't rounded into a saving or into a higher price.
4. **With no evidence, the figure is unknown, never 0.** No baseline gives unknown gross, net and layers. An unknown figure for this PC is an unknown layer and an incomplete run. A period of runs with no figure has no total and counts them as unknown. Too few known runs give no projection. No CU figure, or no price in effect, gives an unknown value.

Together, these mean a figure only appears when there is evidence for it, and it can't be bigger than the CU it came from, below 0, changed by input order, or pushed up by rounding.

Run against the mutants on their own, the properties kill 69 of 112 mutants of `attribution.py`, 38 of 88 of `recurring.py` and 76 of 111 of `pricing.py`. Batches 191 to 193 below give the full scores.

**Two gaps found, pinned with strict xfails, not fixed** (savings/ belongs to Enterprise Savings Intelligence):

- **Gross can be rounded above the baseline.** `attribute` caps gross at the baseline and then rounds it to 6 places. A baseline given in CU seconds isn't on the 6-place grid once converted, so it can round up. With a baseline and a PC figure of 1.9 CU seconds, the baseline is 0.000527777... CU hours and gross comes back as 0.000528, 2.2e-7 above it (`test_a_baseline_in_cu_seconds_doesnt_round_gross_above_it`). Baselines in CU hours are already rounded to 6 places by `measurement.make`, so the random properties, which use CU hours, hold.
- **A priced total can depend on input order.** `value_runs` keeps the first attribution it reads for each run. Given two different attributions of one run, the total depends on which comes first, while `recurring.by_period` refuses the same input (`test_two_different_attributions_of_one_run_dont_make_the_priced_total_depend_on_order`).

