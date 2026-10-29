"""KUDP proof of concept: the same Spark SQL on DuckDB (this PC) and Fabric Spark, answers compared.

Chris's go: decision card "Run the live DuckDB vs Fabric Spark comparison on the Trial capacity?",
"Yes, run it on Trial", 2026-10-29 17:45:56Z. Scope of that yes, and so of this script:

- workspace kf_tenant_test only (Trial capacity; never eagle or Prod);
- one Spark session, about 10 minutes, closed at the end whatever happens;
- about 10 small queries whose data is inline (VALUES, literals); nothing is written anywhere in Fabric.

Run from the repo on Chris's PC, with the app's sidecar environment and his existing sign-in:

    cd C:\\Users\\chris\\fabric-desktop\\sidecar
    python ..\\..\\kudp_poc.py      (or wherever this file is saved)

It prints a summary and writes kudp_poc_results.json next to itself. It stops before opening a
session if kf_tenant_test or a lakehouse in it isn't found (it never creates one).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fabric_desktop.auth import Authenticator
from fabric_desktop import app_settings
from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import compare as cmp
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.engines.fabric_spark import FabricSparkAdapter
from fabric_desktop.fabric_api import FabricClient

WORKSPACE = "kf_tenant_test"
APPROVAL = "Chris, decision card 2026-10-29T17:45:56Z"
ROWS = 1000
# Spark SQL, run as written on Fabric Spark and translated by sqlglot on DuckDB (dialect "spark").
QUERIES = {
    "literals": "SELECT 1 AS id, 'a' AS name, 2.5 AS amount",
    "group_by": "SELECT k, count(*) AS n, sum(v) AS total FROM VALUES ('a', 1), ('a', 2), ('b', 5) AS t(k, v) GROUP BY k",
    "decimals": "SELECT CAST(10.25 AS DECIMAL(10, 2)) * 3 AS d, CAST(1 AS DECIMAL(10, 2)) / 3 AS q",
    "integer_division": "SELECT 7 / 2 AS ratio, 7 DIV 2 AS whole, 7 % 3 AS remainder",
    "dates": "SELECT date_add(DATE'2024-01-31', 1) AS next_day, datediff(DATE'2024-03-01', DATE'2024-02-01') AS days, "
             "last_day(DATE'2024-02-10') AS month_end",
    "strings": "SELECT upper('kratos') AS u, concat_ws('-', 'a', 'b', 'c') AS joined, substring('forge', 2, 3) AS part, "
               "length('ümlaut') AS chars",
    "nulls": "SELECT coalesce(NULL, 3) AS c, nullif(1, 1) AS n, CAST(NULL AS INT) + 1 AS plus_null",
    "window": "SELECT k, v, row_number() OVER (PARTITION BY k ORDER BY v DESC) AS rn "
              "FROM VALUES ('a', 1), ('a', 2), ('b', 5) AS t(k, v)",
    "timestamps": "SELECT to_timestamp('2024-03-10 02:30:00') AS ts, date_trunc('HOUR', TIMESTAMP'2024-03-10 02:45:10') AS hr",
    "sequence": "SELECT explode(sequence(1, 5)) AS i",
}


def main() -> int:
    if not app_settings.value("allow_fabric_runs"):
        print("Fabric runs from KratosForge are turned off by policy (allow_fabric_runs). Nothing ran.")
        return 2
    signin = Authenticator(app_settings.value("client_id") or "", interactive=False)
    fabric = FabricClient(signin.fabric_token)
    ws = next((w for w in fabric.list_workspaces() if w.get("displayName") == WORKSPACE), None)
    if not ws:
        print(f"Workspace {WORKSPACE} isn't visible to {signin.signed_in_user()}. Nothing ran.")
        return 2
    lakehouses = fabric.list_lakehouses(ws["id"])
    if not lakehouses:
        print(f"{WORKSPACE} has no lakehouse for the Spark session, and this script doesn't create one. Nothing ran.")
        return 2
    lh = lakehouses[0]
    target = f"{ws['id']}/{lh['id']}"
    print(f"Workspace {WORKSPACE} ({ws['id']}), lakehouse {lh.get('displayName')} ({lh['id']}), "
          f"capacity {ws.get('capacityId')}")

    duck = registry.adapter("local-duckdb").open_session(SessionConfig("kudp-poc"))
    started = time.monotonic()
    spark = FabricSparkAdapter(token=signin.fabric_token).open_session(
        SessionConfig("KUDP proof of concept", options={"workspace_id": ws["id"], "lakehouse_id": lh["id"]}),
        auth.confirmed("fabric-spark", target, APPROVAL))
    results: dict[str, object] = {}
    try:
        print(f"Spark session {spark.id} started in {time.monotonic() - started:.0f} s")
        for name, query in QUERIES.items():
            report = cmp.compare(duck, spark, CellRequest(query, "sql", "spark"),
                                 right_request=CellRequest(query, "sql"), rows=ROWS,
                                 copy_confirmed=True)   # the card covers copying these small results here
            results[name] = report
            comparison = report.get("comparison") or {}
            print(f"{name:18} {report['result']:13} DuckDB {report['left'].get('seconds')} s, "
                  f"Spark {report['right'].get('seconds')} s"
                  + (f"  ({report.get('problem')})" if report.get("problem") else "")
                  + (f"  columns: {comparison.get('columns')}" if report["result"] == "differs" else ""))
    finally:
        spark.close()
        duck.close()
        print(f"Spark session closed after {time.monotonic() - started:.0f} s")
    out = Path(__file__).with_name("kudp_poc_results.json")
    out.write_text(json.dumps({"workspace": WORKSPACE, "workspace_id": ws["id"], "lakehouse_id": lh["id"],
                               "approval": APPROVAL, "session_seconds": round(time.monotonic() - started),
                               "results": results}, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
