"""What counts as "the same workload" on Databricks and Snowflake, and the pricing scenarios that price
CU, DBUs and credits together, kept on this PC for Compare platforms for one workload (Preview).

One JSON file under the app's data folder (``platform-compare/links.json``), written whole with
``jsonfile.write_json`` so a crash never leaves half of it:

- ``links``: per workload key, the Databricks jobs a person linked to it (``databricks``: job id and
  an optional name) and the Snowflake query tags that name it (``snowflake``). A job or a tag means
  one workload: linking it to a second one is refused until it is unlinked from the first.
- ``scenarios``: pricing versions saved here (savings/pricing.py rows, with the optional rate per DBU
  by SKU and per credit), one per scenario name; saving a name again replaces it.

Nothing here reads Databricks or Snowflake, and nothing is shared: the file stays on this PC.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .engines import databricks_billing, snowflake_query_credits
from .jsonfile import read_json, write_json
from .savings import pricing

SCHEMA = "platform-compare@1"
PLATFORMS = ("databricks", "snowflake")
MAX_SCENARIOS = 50
_KEY = re.compile(r"^[^\x00-\x1f\x7f]{1,300}$")
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{0,200}$")


def _workload(key: Any) -> str:
    text = str(key or "").strip() if isinstance(key, str) else ""
    if not _KEY.fullmatch(text):
        raise ValueError("Pick a workload to link to.")
    return text


def _ref(platform: str, ref: Any) -> str:
    if platform == "databricks":
        found = databricks_billing.job_ids([ref])
    elif platform == "snowflake":
        found = snowflake_query_credits.tags([ref])
    else:
        raise ValueError("Link a Databricks job or a Snowflake query tag.")
    if not found:
        raise ValueError("Say which job or query tag to link.")
    return found[0]


class CompareStore:
    def __init__(self, path: Path):
        self.path = path

    def all(self) -> dict[str, Any]:
        data = read_json(self.path, None)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return {"schema": SCHEMA, "links": {}, "scenarios": []}
        data.setdefault("links", {})
        data.setdefault("scenarios", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, data)

    def links(self, workload: str) -> dict[str, list[Any]]:
        found = self.all()["links"].get(workload) or {}
        return {"databricks": list(found.get("databricks") or []), "snowflake": list(found.get("snowflake") or [])}

    def link(self, workload: str, platform: str, ref: Any, name: str = "") -> dict[str, list[Any]]:
        key, value = _workload(workload), _ref(platform, ref)
        label = str(name or "").strip()
        if not _NAME.fullmatch(label):
            raise ValueError("A job's name is plain text, up to 200 characters.")
        data = self.all()
        for other, links in data["links"].items():
            refs = [j.get("job_id") for j in links.get("databricks") or []] if platform == "databricks" \
                else list(links.get("snowflake") or [])
            if other != key and value in refs:
                what = f"Job {value}" if platform == "databricks" else f"The query tag {value}"
                raise ValueError(f"{what} is already linked to another workload ({other}). Unlink it there first.")
        mine = data["links"].setdefault(key, {"databricks": [], "snowflake": []})
        limit = databricks_billing.MAX_JOBS if platform == "databricks" else snowflake_query_credits.MAX_TAGS
        if platform == "databricks":
            jobs = [j for j in mine.get("databricks") or [] if j.get("job_id") != value]
            mine["databricks"] = jobs + [{"job_id": value, "name": label}]
            count = len(mine["databricks"])
        else:
            mine["snowflake"] = [t for t in mine.get("snowflake") or [] if t != value] + [value]
            count = len(mine["snowflake"])
        if count > limit:
            raise ValueError(f"A workload can have at most {limit} linked {'jobs' if platform == 'databricks' else 'tags'}.")
        self._write(data)
        return self.links(key)

    def unlink(self, workload: str, platform: str, ref: Any) -> dict[str, list[Any]]:
        key, value = _workload(workload), _ref(platform, ref)
        data = self.all()
        mine = data["links"].get(key)
        if mine:
            if platform == "databricks":
                mine["databricks"] = [j for j in mine.get("databricks") or [] if j.get("job_id") != value]
            else:
                mine["snowflake"] = [t for t in mine.get("snowflake") or [] if t != value]
            if not mine["databricks"] and not mine["snowflake"]:
                data["links"].pop(key)
            self._write(data)
        return self.links(key)

    def scenarios(self) -> list[dict[str, Any]]:
        return [s for s in self.all()["scenarios"] if isinstance(s, dict) and s.get("pricing_version")]

    def save_scenario(self, row: dict[str, Any]) -> dict[str, Any]:
        """Keep one checked pricing version per scenario name; a new one replaces the old."""
        given: dict[str, Any] = {k: row.get(k) for k in (
            "scenario", "rate_per_cu_hour", "currency", "effective_from", "effective_to", "sku", "region",
            "pricing_model", "source", "confidence", "rate_per_dbu", "rate_per_credit") if row.get(k) is not None}
        checked = pricing.scenario(**given)
        data = self.all()
        kept = [s for s in data["scenarios"] if s.get("scenario") != checked["scenario"]]
        if len(kept) >= MAX_SCENARIOS:
            raise ValueError(f"Keep at most {MAX_SCENARIOS} pricing scenarios on this PC.")
        data["scenarios"] = kept + [checked]
        self._write(data)
        return checked
