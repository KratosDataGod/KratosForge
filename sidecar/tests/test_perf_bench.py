"""The start-up, notebook-start and SQL benchmark (examples/benchmark/startup_and_sql.py) still works.

Default CI runs only its quick parts: the summary maths, the table and the SQL timings over a tiny
table. The engine and notebook starts spawn processes and are measured only when someone runs it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "examples" / "benchmark" / "startup_and_sql.py"


def load():
    spec = importlib.util.spec_from_file_location("startup_and_sql", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_takes_the_nearest_rank_p95():
    bench = load()
    assert bench.summary([5.0, 1.0, 3.0]) == {"median": 3.0, "p95": 5.0, "min": 1.0, "runs": 3}
    many = bench.summary([float(n) for n in range(1, 101)])
    assert (many["median"], many["p95"], many["min"]) == (50.5, 95.0, 1.0)


def test_sql_times_every_query_over_a_small_table(tmp_path):
    bench = load()
    result = bench.sql(tmp_path, rows=200, runs=2)
    assert result["rows"] == 200
    for name in ["first query on a new project", *(q[0] for q in bench.QUERIES)]:
        assert result[name]["runs"] == 2 and result[name]["min"] >= 0


def test_the_table_lists_each_measurement():
    bench = load()
    s = {"median": 1.0, "p95": 2.0, "min": 0.5, "runs": 1}
    result = {"runs": 1, "warmup": 0, "machine": {"python": "3.12", "system": "Windows", "cpus": 8},
              "startup": s, "notebook first cell": s, "notebook first spark cell": s,
              "sql": {"rows": 1000, "count": s}}
    text = bench.table(result)
    assert "| startup | 1.0 | 2.0 | 0.5 |" in text
    assert "| SQL count (1,000 rows) |" in text
