"""HD-054 (finding TS-2): the T-SQL contract harness.

``tsql_contract/cases.jsonl`` holds table-driven cases. Each one pins what Fabric Desktop does with a
piece of SQL on this PC, and states what Fabric is expected to do with it:

- ``local.check``: the static compatibility check's outcome and rules (sqlcompat).
- ``local.format``: whether Format SQL formats it or leaves it alone; formatting must never change
  the check's findings.
- running it here: T-SQL targets are always refused (T-SQL never runs on this PC); Spark SQL cases
  pin the rows, or the error, that the local DuckDB run gives (``local.run``).
- ``local.equivalent``: the Spark SQL spelling of the same query, run on this PC, and its rows.
- ``fabric``: what Fabric is expected to do (accepts, rejects or unknown) and, when known, its rows.
  Unless ``known_difference`` says why not, Fabric's expected rows are the local equivalent's rows.

``verification`` is ``local_pinned`` for a case only checked here and ``verified_against_fabric``
once a live run confirmed its ``fabric`` half (with a ``verified`` block saying when and where).
Passing offline proves nothing about Fabric.

The live half (``test_live_*``) runs the same cases against a real Fabric SQL endpoint. It is skipped
unless ``FABRIC_DESKTOP_TSQL_LIVE=1`` and only ever connects to a warehouse or SQL analytics endpoint
in a workspace marked for tests (livetest.MARKER), never one that looks like production. It only
runs cases it can prove read-only, inside a transaction it rolls back. See docs/live-testing.md.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from fabric_desktop import livetest, sql, sql_format, sql_policy
from fabric_desktop.sqlcompat import PROFILES, analyze

CASES_FILE = Path(__file__).parent / "tsql_contract" / "cases.jsonl"
_LINES = [json.loads(line) for line in CASES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
META, CASES = _LINES[0]["meta"], _LINES[1:]

TSQL_TARGETS = ("warehouse", "endpoint", "sqldb")
AREAS = ("top", "cast_convert", "date", "string", "null_handling", "cte", "window", "merge", "unsupported")
VERIFICATIONS = ("local_pinned", "verified_against_fabric")
EXPECTS = ("accepts", "rejects", "unknown")
# Parts a case can mark as a known local bug: {"xfail": {part: reason}}.
PARTS = ("check", "format_outcome", "format_keeps_findings", "run", "equivalent")


def cases_for(part: str, keep=lambda c: True) -> list[Any]:
    """The cases as pytest params; a case whose ``xfail`` names this part is a strict xfail."""
    out = []
    for c in CASES:
        if not keep(c):
            continue
        reason = (c.get("xfail") or {}).get(part)
        marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
        out.append(pytest.param(c, id=c["id"], marks=marks))
    return out


def value(v: Any) -> Any:
    """One result value as JSON: integral decimals become ints, dates ISO text."""
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def rows(raw: list[Any]) -> list[list[Any]]:
    return [[value(v) for v in (r.values() if isinstance(r, dict) else r)] for r in raw]


def run_here(query: str, dialect: str) -> list[list[Any]]:
    return rows(sql.run(query, dialect).to_pylist())


def rules(report: dict[str, Any]) -> list[str]:
    return sorted({d["rule"] for d in report["diagnostics"]})


# ---------------------------------------------------------------- the data file


def test_cases_are_well_formed():
    assert META["schema"] == "tsql-contract@1"
    assert len({c["id"] for c in CASES}) == len(CASES), "case ids must be unique"
    allowed = {"id", "area", "target", "sql", "verification", "local", "fabric", "known_difference", "xfail", "live",
               "note", "verified"}
    for c in CASES:
        assert set(c) <= allowed, (c["id"], set(c) - allowed)
        assert c["area"] in AREAS, c["id"]
        assert c["target"] in PROFILES, c["id"]
        assert c["sql"].strip(), c["id"]
        assert c["local"]["format"] in ("formats", "left_alone"), c["id"]
        assert c["local"]["check"]["status"] in ("known_problems", "review", "unverified", "no_known_problems"), c["id"]
        assert set(c.get("xfail") or {}) <= set(PARTS), c["id"]
        assert all(isinstance(r, str) and r for r in (c.get("xfail") or {}).values()), c["id"]
        if c["target"] in TSQL_TARGETS:
            assert "run" not in c["local"], f"{c['id']}: T-SQL never runs here; don't pin a local run"
            assert c.get("fabric", {}).get("expect") in EXPECTS, f"{c['id']}: a T-SQL case says what Fabric should do"
        elif "fabric" in c:
            assert c["fabric"].get("engine") == "spark" and c["fabric"]["expect"] in EXPECTS, c["id"]
        if "equivalent" in c["local"]:
            assert c["local"]["equivalent"]["dialect"] in ("spark", "duckdb"), c["id"]
            assert "rows" in c["local"]["equivalent"], c["id"]


def test_every_claimed_area_is_covered():
    count = Counter(c["area"] for c in CASES)
    assert set(count) == set(AREAS)
    assert all(n >= 2 for n in count.values()), count
    assert {c["target"] for c in CASES} >= set(TSQL_TARGETS) | {"spark"}


def test_no_case_claims_fabric_without_evidence():
    # Local and Fabric are different engines; a case says it was checked in Fabric only with a
    # record of the live run that checked it.
    for c in CASES:
        assert c["verification"] in VERIFICATIONS, c["id"]
        if c["verification"] == "verified_against_fabric":
            v = c.get("verified") or {}
            assert c["target"] in TSQL_TARGETS, c["id"]
            assert {"on", "engine", "run"} <= set(v), f"{c['id']}: verified needs on (date), engine and run (job URL)"
            dt.date.fromisoformat(v["on"])
        else:
            assert "verified" not in c, c["id"]


def test_fabric_expectations_and_the_local_check_agree():
    # A case Fabric is expected to reject should be an error in the check, and one Fabric accepts
    # shouldn't be. Where the check misses it, the case says so in xfail["check"].
    for c in CASES:
        expect = c.get("fabric", {}).get("expect")
        status = c["local"]["check"]["status"]
        if c["target"] not in TSQL_TARGETS or expect == "unknown":
            continue
        assert (status == "known_problems") == (expect == "rejects"), c["id"]


def test_local_and_fabric_rows_match_unless_a_difference_is_named():
    for c in CASES:
        local = c["local"].get("equivalent", {}).get("rows")
        fabric = c.get("fabric", {}).get("rows")
        if local is None or fabric is None:
            assert "known_difference" not in c or c["target"] == "spark", c["id"]
            continue
        if "known_difference" in c:
            assert local != fabric, f"{c['id']}: known_difference is set, but the rows are the same"
        else:
            assert local == fabric, f"{c['id']}: local and Fabric rows differ; name the difference or fix the case"


# ---------------------------------------------------------------- local behaviour, pinned


@pytest.mark.parametrize("case", cases_for("check"))
def test_check(case):
    report = analyze(case["sql"], case["target"])
    want = case["local"]["check"]
    assert report["outcome"]["status"] == want["status"]
    if "rules" in want:
        assert rules(report) == want["rules"]


@pytest.mark.parametrize("case", cases_for("format_outcome"))
def test_format_outcome(case):
    result = sql_format.format_sql(case["sql"], case["target"])
    outcome = "left_alone" if result["warning"] else "formats"
    assert outcome == case["local"]["format"], result["warning"]


@pytest.mark.parametrize("case", cases_for("format_keeps_findings"))
def test_format_keeps_findings(case):
    # Format SQL changes layout only, so the check finds the same things after it.
    formatted = sql_format.format_sql(case["sql"], case["target"])["sql"]
    assert rules(analyze(formatted, case["target"])) == rules(analyze(case["sql"], case["target"]))


@pytest.mark.parametrize("case", cases_for("run", lambda c: c["target"] in TSQL_TARGETS))
def test_tsql_never_runs_here(case):
    with pytest.raises(ValueError, match="can't run on this PC"):
        sql.parse(case["sql"], case["target"])


@pytest.mark.parametrize("case", cases_for("run", lambda c: "run" in c["local"]))
def test_local_run(case):
    want = case["local"]["run"]
    if "error" in want:
        with pytest.raises(Exception, match=want["error"]):
            run_here(case["sql"], case["target"])
    else:
        assert run_here(case["sql"], case["target"]) == want["rows"]


@pytest.mark.parametrize("case", cases_for("equivalent", lambda c: "equivalent" in c["local"]))
def test_local_equivalent(case):
    eq = case["local"]["equivalent"]
    assert run_here(eq["sql"], eq["dialect"]) == eq["rows"]


# ---------------------------------------------------------------- the live half: safety rules, tested offline


def live_refusal(case: dict[str, Any], target: str) -> str:
    """Why the live job won't run this case against ``target``; empty when it will.

    Only T-SQL cases for that target, and only SQL proven read-only: every statement reads
    (sql_policy.classify) and none is SELECT ... INTO, which creates a table."""
    if case["target"] != target:
        return f"written for {case['target']}, not {target}"
    if case.get("live") is False:
        return "the case opts out of the live run"
    try:
        statements = [s for s in sqlglot.parse(case["sql"], read="tsql") if s is not None]
    except ParseError:
        return "couldn't be read, so it can't be proven read-only"
    if not statements:
        return "empty"
    if any(v.kind != "read_only" for v in sql_policy.classify(statements)):
        return "doesn't only read"
    if any(s.find(exp.Into) is not None for s in statements):
        return "SELECT ... INTO creates a table"
    return ""


HOSTS = {"warehouse": ".datawarehouse.fabric.microsoft.com", "endpoint": ".datawarehouse.fabric.microsoft.com"}


def resolve_live_target(fabric: Any, workspace_id: str, item: str, target: str,
                        protected_ids: set[str]) -> tuple[str, str]:
    """The (server, database) of ``item`` in ``workspace_id``, only when the workspace is marked for
    tests and nothing about it looks like production. The server is read from Fabric for that item,
    never taken from settings, so the job can't be pointed at a server outside the test workspace."""
    if target not in HOSTS:
        raise livetest.Refused(f"The live job runs warehouse or endpoint cases, not {target!r}.")
    workspace = fabric.get_workspace(workspace_id)
    reasons = livetest.refusals(workspace, protected_ids)
    if livetest._PROD_WORD.search(item):
        reasons.append(f"The item name {item!r} looks like production.")
    if reasons:
        raise livetest.Refused(" ".join(reasons))
    if target == "warehouse":
        found = [w for w in fabric._paged(f"/workspaces/{workspace_id}/warehouses") if w.get("displayName") == item]
        server = (found[0].get("properties") or {}).get("connectionString", "") if found else ""
    else:
        lakehouse = fabric.find_lakehouse(workspace_id, item)
        props = (fabric.get_lakehouse(workspace_id, lakehouse["id"]).get("properties") or {}) if lakehouse else {}
        server = (props.get("sqlEndpointProperties") or {}).get("connectionString", "")
    if not server:
        raise livetest.Refused(f"No {target} called {item!r} in the test workspace.")
    if not server.lower().endswith(HOSTS[target]):
        raise livetest.Refused(f"{server!r} isn't a Fabric SQL endpoint.")
    return server, item


def test_the_live_job_only_runs_read_only_cases():
    runnable = {t: [c["id"] for c in CASES if not live_refusal(c, t)] for t in TSQL_TARGETS}
    assert len(runnable["warehouse"]) >= 30
    for c in CASES:
        if c["target"] not in TSQL_TARGETS:
            assert live_refusal(c, "warehouse")
            continue
        if re.search(r"\b(MERGE|INSERT|CREATE|UPDATE|DELETE|INTO|SET|BEGIN|EXEC|EXECUTE)\b", c["sql"], re.IGNORECASE):
            assert live_refusal(c, c["target"]), f"{c['id']} changes something and must never run live"
    assert live_refusal({"target": "warehouse", "sql": "SELECT * INTO dbo.x FROM dbo.t"}, "warehouse")
    assert live_refusal({"target": "warehouse", "sql": "SELECT 1; DROP TABLE dbo.t"}, "warehouse")
    assert live_refusal({"target": "warehouse", "sql": "EXEC sp_who"}, "warehouse")
    assert live_refusal({"target": "warehouse", "sql": "SELECT 1", "live": False}, "warehouse")


class _FakeFabric:
    def __init__(self, workspace, warehouses=(), lakehouse=None):
        self.workspace, self.warehouses, self.lakehouse = workspace, list(warehouses), lakehouse

    def get_workspace(self, workspace_id):
        return self.workspace

    def _paged(self, url):
        assert url.endswith("/warehouses")
        return self.warehouses

    def find_lakehouse(self, workspace_id, name):
        return self.lakehouse if self.lakehouse and self.lakehouse["displayName"] == name else None

    def get_lakehouse(self, workspace_id, lakehouse_id):
        return self.lakehouse


MARKED = {"id": "ws1", "displayName": "Fabric Desktop tests", "description": f"tests {livetest.MARKER}"}
WAREHOUSE = {"displayName": "contract_wh", "properties": {"connectionString": "abc.datawarehouse.fabric.microsoft.com"}}


def test_the_live_job_refuses_anything_but_a_marked_test_workspace():
    unmarked = dict(MARKED, description="team data")
    with pytest.raises(livetest.Refused, match=livetest.MARKER):
        resolve_live_target(_FakeFabric(unmarked, [WAREHOUSE]), "ws1", "contract_wh", "warehouse", set())
    prod = dict(MARKED, displayName="Sales prod")
    with pytest.raises(livetest.Refused, match="production"):
        resolve_live_target(_FakeFabric(prod, [WAREHOUSE]), "ws1", "contract_wh", "warehouse", set())
    with pytest.raises(livetest.Refused, match="Platform workspace"):
        resolve_live_target(_FakeFabric(MARKED, [WAREHOUSE]), "ws1", "contract_wh", "warehouse", {"ws1"})
    with pytest.raises(livetest.Refused, match="production"):
        resolve_live_target(_FakeFabric(MARKED, [dict(WAREHOUSE, displayName="wh_prd")]), "ws1", "wh_prd",
                            "warehouse", set())
    with pytest.raises(livetest.Refused, match="No warehouse"):
        resolve_live_target(_FakeFabric(MARKED, [WAREHOUSE]), "ws1", "other", "warehouse", set())
    odd = dict(WAREHOUSE, properties={"connectionString": "sql.example.com"})
    with pytest.raises(livetest.Refused, match="isn't a Fabric SQL endpoint"):
        resolve_live_target(_FakeFabric(MARKED, [odd]), "ws1", "contract_wh", "warehouse", set())
    with pytest.raises(livetest.Refused, match="not 'sqldb'"):
        resolve_live_target(_FakeFabric(MARKED, [WAREHOUSE]), "ws1", "contract_wh", "sqldb", set())
    assert resolve_live_target(_FakeFabric(MARKED, [WAREHOUSE]), "ws1", "contract_wh", "warehouse", set()) == (
        "abc.datawarehouse.fabric.microsoft.com", "contract_wh")
    lakehouse = {"id": "lh1", "displayName": "contract_lh",
                 "properties": {"sqlEndpointProperties": {"connectionString": "x.datawarehouse.fabric.microsoft.com"}}}
    assert resolve_live_target(_FakeFabric(MARKED, lakehouse=lakehouse), "ws1", "contract_lh", "endpoint", set()) == (
        "x.datawarehouse.fabric.microsoft.com", "contract_lh")


# ---------------------------------------------------------------- the live half (skipped unless asked for)

LIVE = os.environ.get("FABRIC_DESKTOP_TSQL_LIVE") == "1"
LIVE_SETTINGS = ("FABRIC_DESKTOP_TSQL_WORKSPACE_ID", "FABRIC_DESKTOP_TSQL_ITEM", "FABRIC_DESKTOP_TSQL_TARGET",
                 "FABRIC_DESKTOP_TSQL_TENANT_ID", "FABRIC_DESKTOP_TSQL_CLIENT_ID", "FABRIC_DESKTOP_TSQL_CLIENT_SECRET")
SQL_SCOPE = "https://database.windows.net/user_impersonation"  # asked for as https://database.windows.net/.default


@pytest.fixture(scope="module")
def live():
    if not LIVE:
        pytest.skip("live T-SQL contract run: set FABRIC_DESKTOP_TSQL_LIVE=1 and the settings in docs/live-testing.md")
    missing = [k for k in LIVE_SETTINGS if not os.environ.get(k)]
    if missing:
        pytest.fail(f"FABRIC_DESKTOP_TSQL_LIVE=1 but these aren't set: {', '.join(missing)}")
    import pyodbc  # only the live job installs it

    from fabric_desktop import auth, credentials
    from fabric_desktop.fabric_api import FabricClient
    env = os.environ
    target = env["FABRIC_DESKTOP_TSQL_TARGET"]
    credential = credentials.ServicePrincipal(env["FABRIC_DESKTOP_TSQL_TENANT_ID"], env["FABRIC_DESKTOP_TSQL_CLIENT_ID"],
                                              client_secret_env="FABRIC_DESKTOP_TSQL_CLIENT_SECRET")
    fabric = FabricClient(lambda: credential._token(auth.FABRIC_SCOPES))
    server, database = resolve_live_target(fabric, env["FABRIC_DESKTOP_TSQL_WORKSPACE_ID"], env["FABRIC_DESKTOP_TSQL_ITEM"],
                                           target, livetest.policy_workspace_ids())
    token = credential._token([SQL_SCOPE]).encode("utf-16-le")
    connection = pyodbc.connect(
        f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60",
        attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)},  # SQL_COPT_SS_ACCESS_TOKEN
        autocommit=False)
    results: list[dict[str, Any]] = []
    yield {"connection": connection, "target": target, "error": pyodbc.Error, "results": results}
    connection.rollback()
    connection.close()
    out = Path(env.get("FABRIC_DESKTOP_TSQL_RESULTS", "tsql-contract-results.json"))
    out.write_text(json.dumps({"schema": "tsql-contract-result@1", "target": target, "cases": results}, indent=1),
                   encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_live_fabric_matches_the_contract(case, live):
    why_not = live_refusal(case, live["target"])
    if why_not:
        pytest.skip(why_not)
    cursor = live["connection"].cursor()
    record: dict[str, Any] = {"id": case["id"], "expect": case["fabric"]["expect"]}
    try:
        cursor.execute(case["sql"])
        got = rows(cursor.fetchall()) if cursor.description else []
        record.update(outcome="accepted", rows=got)
    except live["error"] as exc:
        got = None
        record.update(outcome="rejected", error=livetest.scrub(str(exc)))
    finally:
        live["connection"].rollback()  # read-only by live_refusal; rolled back all the same
        live["results"].append(record)
    expect = case["fabric"]["expect"]
    if expect == "unknown":
        return  # recorded for a person to decide; nothing to compare yet
    if expect == "rejects":
        assert got is None, f"Fabric accepted SQL the contract says it rejects: {case['fabric'].get('why', '')}"
        return
    assert got is not None, f"Fabric rejected SQL the contract says it accepts: {record.get('error')}"
    if "rows" in case["fabric"]:
        assert got == case["fabric"]["rows"], "Fabric's rows differ from the contract (and the local result)"
