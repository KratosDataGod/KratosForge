"""Compare platforms for one workload (Preview), mixed into ``Service``: Optimize > Cost and savings.

- ``compare_platforms`` returns the comparison for one workload (savings/compare.py) from what is
  already on this PC: the metadata project's runs (this PC's runs, and Fabric's with their estimated
  CU), the last Databricks per-run billing read (databricks_billing) and the last Snowflake
  query-credits read (snowflake_query_credits), and the pricing scenario chosen. It reads nothing in
  Databricks or Snowflake and starts no warehouse: reading those runs only through
  ``databricks_billing`` and ``snowflake_query_credits``, which ask first.
- ``compare_link`` and ``compare_unlink`` say which Databricks job, or which Snowflake query tag,
  is the same workload (compare_store.py).
- ``compare_pricing_save`` keeps a pricing scenario with a rate per CU hour, per DBU (by SKU) and
  per credit, so every money figure in one comparison is priced under one version.

A workload is a table of the metadata project (``meta:<table>``), the same key Cost and savings
uses. Its definition version is the table's logic fingerprint, and its input scope is a regular load
or a backfill, from the run log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import costs as costs_mod
from . import redact
from .compare_store import CompareStore
from .savings import compare as compare_mod
from .savings import pricing

METHODS = {"compare_platforms", "compare_link", "compare_unlink", "compare_pricing_save"}
META_SOURCE = "Metadata run log on this PC"


def _scope(run: dict[str, Any]) -> str:
    backfill = str(run.get("backfill") or "").strip()
    return f"backfill {backfill}" if backfill else "regular load"


class CompareMixin:
    def _compare_store(self) -> CompareStore:
        from .service import data_dir
        return CompareStore(Path(data_dir()) / "platform-compare" / "links.json")

    def _compare_scenarios(self) -> list[dict[str, Any]]:
        """Today's price (Optimize > Cost and savings > Prices) first, then the scenarios saved here."""
        default = pricing.from_settings(self._cost_settings())  # type: ignore[attr-defined]
        return [default] + [s for s in self._compare_store().scenarios() if s["pricing_version"] != default["pricing_version"]]

    def _compare_connections(self) -> dict[str, list[dict[str, Any]]]:
        """Databricks and Snowflake connections on this PC: references only, read from local files."""
        dbx = [{"key": c["key"], "label": c["label"], "host": c.get("host", ""), "warehouse_id": c.get("warehouse_id", "")}
               for c in self.engine_connections().get("connections") or [] if c.get("host")]  # type: ignore[attr-defined]
        sf = []
        for p in self.engine_profiles().get("profiles") or []:  # type: ignore[attr-defined]
            for c in p.get("connections") or []:
                if c.get("platform") == "snowflake" and c.get("account"):
                    sf.append({"key": f"profile:{p['id']}:{c['id']}", "label": f"{p.get('name') or p['id']} · {c.get('label') or c['id']}",
                               "account": c["account"], "warehouse": c.get("warehouse_id", "")})
        return {"databricks": dbx, "snowflake": sf}

    @staticmethod
    def _compare_workloads(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names: dict[str, dict[str, Any]] = {}
        for r in runs:   # newest first
            if r.get("entity") and r["entity"] not in names:
                names[r["entity"]] = {"key": f"meta:{r['entity']}", "name": r["entity"]}
        return sorted(names.values(), key=lambda w: w["name"].lower())

    def _compare_inputs(self, workload: str, runs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]],
                                                                                   list[dict[str, Any]]]:
        """The workload as the comparison matches it, this PC's runs and the Fabric runs."""
        entity = workload.removeprefix("meta:")
        mine = [r for r in runs if r.get("entity") == entity]
        local = [r for r in mine if r.get("compute") != "fabric"]
        latest = local[0] if local else (mine[0] if mine else {})
        work = {"key": workload, "name": entity, "definition_version": latest.get("logic") or None,
                "input_scope": _scope(latest) if latest else None}
        # Only this PC's runs of the same definition and scope: an older definition is another workload.
        pc = [{"run_id": r.get("run_id"), "at": r.get("started_at"), "seconds": r.get("seconds"), "rows": r.get("rows_read"),
               "bytes": None, "cpu_seconds": None, "peak_bytes": None, "source": META_SOURCE}
              for r in local if r.get("logic") == work["definition_version"] and _scope(r) == work["input_scope"]]
        fabric_meta = [r for r in mine if r.get("compute") == "fabric"]
        by_id = {r.get("run_id"): r for r in fabric_meta}
        rows = costs_mod.activities(fabric_meta, [], [], self._cost_settings())  # type: ignore[attr-defined]
        fabric = []
        for row in rows:
            cu = row["fabric_job_cu"]
            meta = by_id.get(cu.get("run_id")) or {}
            if not cu.get("run_id"):
                continue
            fabric.append({"run_id": cu["run_id"], "at": row["at"], "workload_key": workload,
                           "definition_version": row.get("version"), "input_scope": _scope(meta),
                           "rows": row.get("rows_read"), "cu": cu, "ran": "fabric"})
        return work, pc, fabric

    def compare_platforms(self, workload: str = "", days: int = 30, pricing_version: str = "",
                          databricks_connection: str = "", snowflake_connection: str = "") -> dict[str, Any]:
        """The comparison for ``workload`` (the first one when empty) over the last ``days`` days,
        priced under ``pricing_version`` when one is chosen. Reads only what is on this PC."""
        from .engines import databricks_billing, snowflake_query_credits
        meta = self.meta_runs_all(limit=100000)  # type: ignore[attr-defined]
        runs = meta.get("runs") or []
        workloads = self._compare_workloads(runs)
        scenarios = self._compare_scenarios()
        conns = self._compare_connections()
        base = {"workloads": workloads, "scenarios": scenarios, "connections": conns, "days": int(days),
                "unavailable": meta.get("unavailable") or {}}
        if not workloads:
            return base | {"workload": None, "comparison": None}
        key = workload or workloads[0]["key"]
        if key not in {w["key"] for w in workloads}:
            raise ValueError("That workload has no runs on this PC or in Fabric. Pick one from the list.")
        scenario = next((s for s in scenarios if s["pricing_version"] == pricing_version), None)
        if pricing_version and scenario is None:
            raise ValueError("That pricing scenario isn't on this PC any more. Pick another one.")
        work, pc, fabric = self._compare_inputs(key, runs)
        links = self._compare_store().links(key)
        dbx_conn = next((c for c in conns["databricks"] if c["key"] == databricks_connection), None) or \
            (conns["databricks"][0] if conns["databricks"] else None)
        sf_conn = next((c for c in conns["snowflake"] if c["key"] == snowflake_connection), None) or \
            (conns["snowflake"][0] if conns["snowflake"] else None)
        dbx_read = databricks_billing.last_runs(self._billing_root(), dbx_conn["host"]) if dbx_conn else None  # type: ignore[attr-defined]
        sf_read = snowflake_query_credits.last(self._snowflake_credits_root(), sf_conn["account"]) if sf_conn else None  # type: ignore[attr-defined]
        result = compare_mod.compare(
            work, pc_runs=pc, fabric_runs=fabric,
            databricks={"connected": dbx_conn is not None, "jobs": links["databricks"], "read": dbx_read},
            snowflake={"connected": sf_conn is not None, "tags": links["snowflake"], "read": sf_read},
            scenario=scenario, days=int(days))
        return redact.walk(base | {"workload": key, "links": links,
                                   "databricks_connection": dbx_conn["key"] if dbx_conn else "",
                                   "snowflake_connection": sf_conn["key"] if sf_conn else "",
                                   "databricks_read": {k: (dbx_read or {}).get(k) for k in ("read_at", "days", "warehouse_id", "runs_error")},
                                   "snowflake_read": {k: (sf_read or {}).get(k) for k in ("read_at", "days", "warehouse")},
                                   "comparison": result})

    def compare_link(self, workload: str, platform: str, ref: str, name: str = "") -> dict[str, Any]:
        """Say that Databricks job ``ref`` (platform databricks) or Snowflake query tag ``ref``
        (platform snowflake) is ``workload``. Kept on this PC; nothing is read or changed elsewhere."""
        return {"links": self._compare_store().link(workload, platform, ref, name)}

    def compare_unlink(self, workload: str, platform: str, ref: str) -> dict[str, Any]:
        """Take a job or a query tag off a workload. Kept on this PC; nothing else changes."""
        return {"links": self._compare_store().unlink(workload, platform, ref)}

    def compare_pricing_save(self, scenario: str, rate_per_cu_hour: float, currency: str = "USD",
                             rate_per_dbu: dict[str, Any] | None = None, rate_per_credit: float | None = None,
                             source: str = "", confidence: str = "assumption", pricing_model: str = "assumption",
                             effective_from: str = "2000-01-01T00:00:00+00:00") -> dict[str, Any]:
        """Keep a pricing scenario on this PC: a rate per CU hour, per DBU by SKU and per Snowflake
        credit, as one version. Saving the same name again replaces it."""
        return self._compare_store().save_scenario({
            "scenario": scenario, "rate_per_cu_hour": rate_per_cu_hour, "currency": currency,
            "rate_per_dbu": rate_per_dbu or None, "rate_per_credit": rate_per_credit, "source": source,
            "confidence": confidence, "pricing_model": pricing_model, "effective_from": effective_from})
