"""Best Practice Analyzer for a semantic model, run in Fabric with semantic-link-labs.

semantic-link-labs (MIT) needs Microsoft's semantic-link (sempy), which only runs inside Fabric, so the
check runs in ``nb_fabric_desktop_bpa``: a Fabric Python notebook in the model's workspace whose default
lakehouse is the environment's LH_Meta. It installs the pinned semantic-link-labs into its own session,
calls ``run_model_bpa`` read-only with ``extended=True`` (VertiPaq statistics, which some rules need),
and saves the rows to ``Files/_fabric_desktop/results/<request>.json``. The model is read through its
XMLA endpoint; nothing in it changes.

Pure: the notebook, its parameters, and the findings read back from the result file. The service
(``model_bpa_service``) publishes the notebook, starts the job and reads the file.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .fabric_compute import MOUNT, RESULTS_DIR

NOTEBOOK = "nb_fabric_desktop_bpa"
PACKAGE = "semantic-link-labs"
VERSION = "0.17.1"
SEVERITIES = ("Error", "Warning", "Info")
# semantic-link-labs returns the severity as an icon.
_ICONS = {"❌": "Error", "⚠️": "Warning", "⚠": "Warning", "ℹ️": "Info", "ℹ": "Info"}

# One cell, so whatever goes wrong is saved for the app: Fabric's REST API doesn't return a Python
# notebook's cell output, and a failed cell cancels the cells after it. The cell installs the package,
# runs the check, and saves either the findings or the error (with pip's output) before failing the run.
_CHECK = '''import datetime as _dt, json, pathlib, subprocess, sys, traceback

started = _dt.datetime.now(_dt.timezone.utc)
name = f"%(results)s/{request_id}.json"
pip = ""

import re as _re
_SECRET = [(_re.compile(p, _re.I), r) for p, r in (
    (r"(bearer\\s+)[\\w.~+/=-]+", r"\\1[redacted]"), (r"eyJ[\\w-]+\\.[\\w-]+\\.[\\w-]*", "[redacted]"),
    (r"([?&](?:sig|se|sv|sp|skoid|sktid|skt|ske|sks|skv)=)[^&\\s]+", r"\\1[redacted]"), (r"(://)[^/\\s:@]+:[^/\\s@]+@", r"\\1[redacted]@"))]

def _scrub(text):
    for pattern, repl in _SECRET:
        text = pattern.sub(repl, text)
    return text

def _save(record):
    text = json.dumps({"request_id": request_id, "kind": "model_bpa", "dataset": dataset, "workspace": workspace,
                       "package": "%(package)s", "started_at": started.isoformat(timespec="seconds"),
                       "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")} | record)
    try:
        path = pathlib.Path("%(mount)s") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:  # no default lakehouse mounted: write to it by its OneLake path
        import notebookutils
        notebookutils.fs.put(f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}/{name}", text, True)

try:
    out = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "%(package)s==%(version)s"],
                         capture_output=True, text=True)
    pip = (out.stdout + out.stderr)[-4000:]
    if out.returncode:
        raise RuntimeError(f"pip install %(package)s==%(version)s exited with {out.returncode}")
    from importlib.metadata import version as _version
    import sempy_labs as labs
    df = labs.run_model_bpa(dataset=dataset, workspace=workspace, extended=True, return_dataframe=True)
    rows = [] if df is None else json.loads(df.to_json(orient="records", force_ascii=False))
    installed = _version("%(package)s")
except BaseException as exc:
    _save({"error": {"type": type(exc).__name__, "message": _scrub(str(exc))[:2000],
                     "traceback": _scrub(traceback.format_exc())[-8000:], "pip": _scrub(pip)}})
    raise
_save({"version": installed, "tables": df is not None, "rows": rows})
print(f"{len(rows)} findings")
'''


def bpa_ipynb(workspace_id: str, meta_lakehouse_id: str, meta_lakehouse: str = "LH_Meta") -> dict[str, Any]:
    from .fabric_run import PYTHON_NOTEBOOK_METADATA

    def code(src: str, tags: list[str] | None = None) -> dict[str, Any]:
        return {"cell_type": "code", "metadata": {"tags": tags} if tags else {}, "execution_count": None,
                "outputs": [], "source": src.splitlines(keepends=True)}

    params = ('dataset = ""     # the semantic model\'s id\n'
              'workspace = ""   # its workspace\'s id\n'
              'lakehouse = ""   # the lakehouse the findings are saved in\n'
              'request_id = ""  # set by KratosForge\n')
    return {"nbformat": 4, "nbformat_minor": 5,
            "metadata": PYTHON_NOTEBOOK_METADATA | {"dependencies": {"lakehouse": {
                "default_lakehouse": meta_lakehouse_id, "default_lakehouse_name": meta_lakehouse,
                "default_lakehouse_workspace_id": workspace_id}}},
            "cells": [{"cell_type": "markdown", "metadata": {}, "source": [
                "# KratosForge best practices check\n",
                "Runs Best Practice Analyzer from semantic-link-labs on one semantic model, read only, and saves "
                "the findings for KratosForge. Start checks from **Data mesh > Deploy > Semantic model** in the app; "
                "changes here are replaced on the next check.\n"]},
                      code(params, ["parameters"]),
                      code(_CHECK % {"mount": MOUNT, "results": RESULTS_DIR, "package": PACKAGE, "version": VERSION})]}


def notebook_hash(ipynb: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(ipynb, sort_keys=True).encode()).hexdigest()[:12]


def run_parameters(dataset_id: str, workspace_id: str, request_id: str, lakehouse_id: str = "") -> dict[str, Any]:
    return {"parameters": {
        "dataset": {"value": dataset_id, "type": "string"},
        "workspace": {"value": workspace_id, "type": "string"},
        "lakehouse": {"value": lakehouse_id, "type": "string"},
        "request_id": {"value": request_id, "type": "string"},
        # A notebook run as a job ignores %pip unless the run turns inline installation on. The check
        # installs with pip itself, so this only matters to an older copy of the notebook.
        "_inlineInstallationEnabled": {"value": True, "type": "bool"},
    }}


def severity(value: Any) -> str:
    text = str(value or "").strip()
    return _ICONS.get(text) or next((s for s in SEVERITIES if s.lower() == text.lower()), "Info")


# Any workspace contributor can write the results file, so its lengths aren't trusted.
_ERROR_LIMITS = {"type": 200, "message": 2000, "traceback": 8000, "pip": 4000}


def error(record: dict[str, Any]) -> dict[str, str] | None:
    """The error a check saved instead of findings, or None: redacted and capped again here."""
    from . import redact

    err = record.get("error")
    if not isinstance(err, dict):
        return None
    return {k: redact.secrets(str(err.get(k) or ""))[-n:] for k, n in _ERROR_LIMITS.items()}


def findings(record: dict[str, Any]) -> dict[str, Any]:
    """The result file as the app shows it: findings, worst first, and a count per severity."""
    rows = [{"severity": severity(r.get("Severity")), "rule": str(r.get("Rule Name") or ""),
             "category": str(r.get("Category") or ""), "object_type": str(r.get("Object Type") or ""),
             "object": str(r.get("Object Name") or ""), "description": str(r.get("Description") or ""),
             "url": str(r.get("URL") or "")} for r in record.get("rows") or []]
    rows.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["category"], f["rule"], f["object"]))
    return {"request_id": record.get("request_id", ""), "checked_at": record.get("finished_at", ""),
            "package": record.get("package", PACKAGE), "version": record.get("version", ""),
            "no_tables": record.get("tables") is False,
            "counts": {s: sum(1 for f in rows if f["severity"] == s) for s in SEVERITIES}, "findings": rows}
