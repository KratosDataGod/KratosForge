"""The demonstration fixture (fixtures/demo_workspace.json), in the shapes Fabric returns.

The fixture is kept readable (code as text, runs as "daily for N days"); this turns it into what
getDefinition and the job instances API send, so the demonstration goes through the same code as a
live read. Everything it produces is labelled as a fixture by assess.assess(fixture=True).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("fixtures") / "demo_workspace.json"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _ipynb(nb: dict[str, Any]) -> dict[str, Any]:
    doc = {"nbformat": 4, "nbformat_minor": 5, "cells": [{"cell_type": "code", "source": c, "metadata": {},
                                                           "outputs": [], "execution_count": None} for c in nb["cells"]],
           "metadata": {"dependencies": {"lakehouse": {"default_lakehouse": nb.get("default_lakehouse")}}}}
    return {"definition": {"format": "ipynb", "parts": [{"path": "notebook-content.ipynb", "payload": _b64(json.dumps(doc)),
                                                         "payloadType": "InlineBase64"}]}}


def _pipeline(content: dict[str, Any]) -> dict[str, Any]:
    return {"definition": {"parts": [{"path": "pipeline-content.json", "payload": _b64(json.dumps(content)),
                                      "payloadType": "InlineBase64"}]}}


def _jobs(spec: dict[str, Any], as_of: dt.datetime) -> list[dict[str, Any]]:
    out = []
    for day in range(1, int(spec.get("daily_scheduled", 0)) + 1):
        start = (as_of - dt.timedelta(days=day)).replace(hour=6, minute=0, second=0)
        end = start + dt.timedelta(seconds=float(spec["seconds"]))
        out.append({"id": f"fixture-{day}", "invokeType": "Scheduled", "status": "Completed",
                    "startTimeUtc": start.isoformat(), "endTimeUtc": end.isoformat()})
    return out


def load() -> dict[str, Any]:
    """``source`` (for assess), ``usage_imports``, ``price`` and ``now`` of the demonstration."""
    doc = json.loads(PATH.read_text(encoding="utf-8"))
    as_of = dt.datetime.fromisoformat(doc["as_of"])
    definitions = {i: _ipynb(nb) for i, nb in doc["notebooks"].items()}
    definitions.update({i: _pipeline(p) for i, p in doc["pipelines"].items()})
    jobs = {it["id"]: _jobs(doc["jobs"].get(it["id"], {}), as_of) for it in doc["items"]}
    usage = doc["usage_import"]
    rows = [r | {"cu": {"value": r["cu"]["value"], "period": r["cu"]["period"]}} for r in usage["rows"]]
    return {"source": {"workspace": doc["workspace"], "items": doc["items"], "definitions": definitions, "jobs": jobs},
            "usage_imports": [{"id": usage["id"], "file_name": usage["file_name"], "rows": rows}],
            "price": {k: doc["price"][k] for k in ("price_per_cu_hour", "currency")}, "now": as_of}
