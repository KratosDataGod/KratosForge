"""Change templates for a failed control (FEA-402): text proposals, never run.

:func:`for_outcome` takes one control outcome and returns zero or more proposals::

    {"control_id", "kind": "sql" | "python" | "notebook" | "tmdl" | "json", "title", "text",
     "placeholders": [{"token", "meaning"}], "caveats": [str]}

Rules every template keeps:

- Text only. Nothing here runs a template, sends it anywhere or writes a file; a person copies it,
  reads it and applies it where they choose.
- Filled only from names already in the outcome's ``detail`` (relationship, column and activity
  names). Everything else is a ``<placeholder>`` the person fills in. Never a secret, a connection
  string, a token or an id.
- Every inserted name is quoted for its language: TMDL names in single quotes with ``'`` doubled,
  JSON strings through :func:`json.dumps`. A name that can't be quoted safely (empty, a line break or
  any other control or invisible character, a name cut short by the inspection, or one whose parts
  can't be told apart) is left out, and the proposal says how many were; with none left there is no
  proposal.
- No destructive defaults: no statement that removes data or objects, and VACUUM keeps the table's
  retention (7 days by default).
- The same outcome always gives the same text.

Only a ``fail`` gets templates; a pass, an advisory, not applicable, not evaluated or an error gets none.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

KINDS = ("sql", "python", "notebook", "tmdl", "json")
MAX_NAME = 256  # inspect_models cuts names at this length; a name that long may be cut, so it is left out
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PREVIEW = "Proposal only: KratosForge doesn't apply it. Review it, then make the change yourself."


def _safe(name: Any) -> bool:
    """A name that can be quoted without changing what it means: non-empty, not cut short, no line
    break, control, format (bidirectional, zero-width) or separator character other than a plain space."""
    return (isinstance(name, str) and name.strip() == name and 0 < len(name) < MAX_NAME
            and not name.endswith("…") and name.isprintable())


def tmdl_name(name: str) -> str | None:
    """``name`` as a TMDL object name: as is when it is a plain identifier, otherwise in single quotes
    with each ``'`` doubled. None when it can't be quoted safely."""
    if not _safe(name):
        return None
    return name if _PLAIN.fullmatch(name) else "'" + name.replace("'", "''") + "'"


def json_string(name: str) -> str | None:
    """``name`` as a JSON string literal, or None when it can't be quoted safely."""
    return json.dumps(name, ensure_ascii=False) if _safe(name) else None


def _split_once(text: str, sep: str) -> tuple[str, str] | None:
    parts = text.split(sep)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _column(text: str) -> tuple[str, str] | None:
    """``Table.Column`` as inspect_models writes it, or None when a part itself holds a dot."""
    return _split_once(text, ".")


def _relationship(text: str) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """``A.a to B.b`` as inspect_models writes it, or None when it can't be read back unambiguously."""
    if not _safe(text):
        return None
    ends = _split_once(text, " to ")
    if ends is None:
        return None
    a, b = _column(ends[0]), _column(ends[1])
    return (a, b) if a and b else None


def _qualified(col: tuple[str, str]) -> str | None:
    t, c = tmdl_name(col[0]), tmdl_name(col[1])
    return f"{t}.{c}" if t and c else None


def _names(detail: dict[str, Any], key: str) -> list[str]:
    got = detail.get(key)
    return sorted({n for n in got if isinstance(n, str)}) if isinstance(got, list) else []


def _count(detail: dict[str, Any]) -> int:
    """The finding's ``count`` as a whole number, 0 when it isn't one (None, NaN, inf, text): an odd
    count only drops the "N of M" caveat, never the proposal."""
    got = detail.get("count")
    if isinstance(got, bool) or not isinstance(got, (int, float)) or got != got or got in (float("inf"), float("-inf")):
        return 0
    return max(0, int(got))


def _left_out(skipped: int, total: int, shown: int, what: str) -> list[str]:
    out = []
    if skipped:
        out.append(f"{skipped} {what} left out: the name can't be written safely here. Change those by hand.")
    if total > shown + skipped:
        out.append(f"The finding lists {shown + skipped} of {total} {what}; change the others the same way.")
    return out


def _tpl(control_id: str, kind: str, title: str, text: str, placeholders: list[tuple[str, str]],
         caveats: list[str]) -> dict[str, Any]:
    return {"control_id": control_id, "kind": kind, "title": title, "text": text.rstrip("\n") + "\n",
            "placeholders": [{"token": t, "meaning": m} for t, m in placeholders],
            "caveats": [_PREVIEW, *caveats]}


# ---- semantic models (TMDL)


def _sm_bidirectional(o: dict[str, Any]) -> list[dict[str, Any]]:
    d = o["detail"]
    names = _names(d, "relationships")
    blocks, skipped = [], 0
    for n in names:
        rel = _relationship(n)
        a, b = (_qualified(rel[0]), _qualified(rel[1])) if rel else (None, None)
        if not (a and b):
            skipped += 1
            continue
        blocks.append(f"relationship <relationship id>\n\tfromColumn: {a}\n\ttoColumn: {b}\n"
                      "\tcrossFilteringBehavior: oneDirection\n")
    if not blocks:
        return []
    return [_tpl(o["control_id"], "tmdl", "Filter each relationship in one direction (TMDL)", "\n".join(blocks),
                 [("<relationship id>", ("The relationship's name in the model's relationships.tmdl, usually a "
                                        "GUID. Match it by fromColumn and toColumn."))],
                 [("Change only the crossFilteringBehavior line of the existing relationship; leave its other "
                  "properties as they are. Removing the line has the same effect: one direction is the default."),
                  ("A visual or measure that relied on filtering both ways can show different numbers. Use "
                  "CROSSFILTER in that measure instead, and compare report totals before and after."),
                  *_left_out(skipped, _count(d), len(blocks), "relationships")])]


def _sm_visible_keys(o: dict[str, Any]) -> list[dict[str, Any]]:
    d = o["detail"]
    by_table: dict[str, list[str]] = {}
    skipped = shown = 0
    for n in _names(d, "objects"):
        col = _column(n) if _safe(n) else None
        t, c = (tmdl_name(col[0]), tmdl_name(col[1])) if col else (None, None)
        if not (t and c):
            skipped += 1
            continue
        by_table.setdefault(t, []).append(c)
        shown += 1
    if not by_table:
        return []
    text = "\n".join(f"table {t}\n" + "".join(f"\n\tcolumn {c}\n\t\tisHidden\n" for c in cols)
                     for t, cols in sorted(by_table.items()))
    return [_tpl(o["control_id"], "tmdl", "Hide the relationship keys in the fact tables (TMDL)", text, [],
                 ["Add the isHidden line to each existing column; leave the column's other properties as they are.",
                  ("A report that shows the key column directly loses that field; point it at the dimension's "
                  "columns instead."),
                  *_left_out(skipped, _count(d), shown, "columns")])]


def _sm_fallback(o: dict[str, Any]) -> list[dict[str, Any]]:
    if str(o["detail"].get("behavior") or "").lower() != "automatic":
        return []  # DirectQuery only is a choice about the model's design, not a one-line change
    return [_tpl(o["control_id"], "tmdl", "Make the model Direct Lake only (TMDL)",
                 "model Model\n\tdirectLakeBehavior: directLakeOnly\n", [],
                 ["Add the line to the model object in model.tmdl; leave its other properties as they are.",
                  ("A query that used to fall back to DirectQuery now fails with an error instead. Open the "
                  "model's reports once after the change; if one fails, fix its cause (a SQL view, a table past "
                  "the guardrails, row-level security in the SQL endpoint) or set Automatic again.")])]


# ---- pipelines (JSON)


def _json_names(detail: dict[str, Any], key: str) -> tuple[list[str], int]:
    names = _names(detail, key)
    ok = [n for n in names if json_string(n) is not None]
    return ok, len(names) - len(ok)


def _pl_deactivated(o: dict[str, Any]) -> list[dict[str, Any]]:
    d = o["detail"]
    names, skipped = _json_names(d, "inactive")
    if not names:
        return []
    patch = [{"name": n, "state": "Active"} for n in names]
    return [_tpl(o["control_id"], "json", "Activate the deactivated activities (pipeline JSON)",
                 json.dumps(patch, ensure_ascii=False, indent=2), [],
                 [("In the pipeline's JSON (View > Edit JSON code), find each activity by name, set \"state\" to "
                  "\"Active\" and take out its \"onInactiveMarkAs\" property. Leave everything else as it is."),
                  ("An activity that should stay off can instead keep \"state\": \"Inactive\" with "
                  "\"onInactiveMarkAs\": \"Skipped\", so a run no longer reports it as Succeeded."),
                  ("Run the pipeline once in a test workspace first: an activity that was off may not have run "
                  "for a while."),
                  *_left_out(skipped, _count(d), len(names), "activities")])]


def _pl_fixed_workspace(o: dict[str, Any]) -> list[dict[str, Any]]:
    d = o["detail"]
    names, skipped = _json_names(d, "activities")
    if not names:
        return []
    expr = {"value": "@pipeline().parameters.target_workspace_id", "type": "Expression"}
    patch = {"parameters": {"target_workspace_id": {"type": "string", "defaultValue": "<workspace id>"}},
             "activities": [{"name": n, "typeProperties": {"workspaceId": expr}} for n in names]}
    return [_tpl(o["control_id"], "json", "Take the workspace from a pipeline parameter (pipeline JSON)",
                 json.dumps(patch, ensure_ascii=False, indent=2),
                 [("<workspace id>", ("The workspace this stage's runs should use. Set it per stage (deployment "
                                     "rule, variable library or the caller), not only here."))],
                 [("Add the parameter to the pipeline, then replace each fixed workspaceId value in the named "
                  "activities with the expression. The value can sit deeper than typeProperties for some "
                  "activity types; change it where it is."),
                  "A variable library value works the same way: @pipeline().libraryVariables.<name>.",
                  ("When the activity should use the pipeline's own workspace, the system variable "
                  "@pipeline().DataFactory gives that workspace's id without a parameter."),
                  *_left_out(skipped, _count(d), len(names), "activities")])]


# ---- notebooks (Python, notebook cells, Spark SQL)


def _nb_secret(o: dict[str, Any]) -> list[dict[str, Any]]:
    text = ("# Read the secret from Azure Key Vault when the notebook runs; don't write it in the code.\n"
            "# notebookutils is built into Fabric notebooks. The person or identity running the notebook\n"
            "# needs permission to read secrets in the vault.\n"
            "secret_value = notebookutils.credentials.getSecret(\"<key vault URL>\", \"<secret name>\")\n"
            "# Pass secret_value to the client that needs it. Don't print or display it.\n")
    return [_tpl(o["control_id"], "python", "Read the secret from Key Vault (notebook cell)", text,
                 [("<key vault URL>", "Your vault's URL, like https://<vault name>.vault.azure.net/."),
                  ("<secret name>", "The name of the secret in that vault.")],
                 [("Rotate the secret at its source: the old value is in the notebook's versions, exports and "
                  "Git history."),
                  "Clear any cell output that showed the secret."])]


def _nb_fixed_paths(o: dict[str, Any]) -> list[dict[str, Any]]:
    text = ("# Parameters cell: mark this cell as a parameter cell, so a pipeline or each stage can set it.\n"
            "workspace_name = \"<workspace name>\"\n"
            "lakehouse_name = \"<lakehouse name>\"\n\n"
            "# Preferred: with a default lakehouse set per stage (deployment rule), use a relative path.\n"
            "relative_path = \"Tables/<table name>\"\n\n"
            "# Otherwise: a OneLake path built from names, not ids.\n"
            "onelake_path = (f\"abfss://{workspace_name}@onelake.dfs.fabric.microsoft.com/\"\n"
            "                f\"{lakehouse_name}.Lakehouse/Tables/<table name>\")\n")
    return [_tpl(o["control_id"], "notebook", "Parameterize the OneLake path (notebook cells)", text,
                 [("<workspace name>", "The workspace for this stage; a pipeline or deployment sets it per stage."),
                  ("<lakehouse name>", "The lakehouse the notebook reads or writes."),
                  ("<table name>", "The table (or Files/... folder) the path pointed at.")],
                 ["Replace each id path named in the finding with relative_path or onelake_path.",
                  "Run the notebook in each stage once and check that it reads and writes only that stage's data."])]


def _nb_swallowed(o: dict[str, Any]) -> list[dict[str, Any]]:
    text = ("import logging\n\n"
            "log = logging.getLogger(\"<notebook name>\")\n\n"
            "try:\n"
            "    <the step that can fail>\n"
            "except <ExpectedError> as exc:\n"
            "    # Handle only the error you expect, and say so in the run's log.\n"
            "    log.warning(\"<step name>: %s\", exc)\n"
            "    <what to do instead>\n"
            "except Exception:\n"
            "    # Anything else: log it and let the run fail, so it can be alerted on.\n"
            "    log.exception(\"<step name> failed\")\n"
            "    raise\n")
    return [_tpl(o["control_id"], "python", "Handle the expected error and let others fail the run", text,
                 [("<notebook name>", "A logger name, such as the notebook's name."),
                  ("<the step that can fail>", "The code the handler wraps today."),
                  ("<ExpectedError>", "The exception type you expect, such as FileNotFoundError."),
                  ("<step name>", "A short name for the step, for the log."),
                  ("<what to do instead>", "The fallback for the expected case, or remove that branch.")],
                 ["Replace the except ...: pass handler at the cell and line named in the finding.",
                  ("Runs that used to succeed quietly may now fail: that is the point. Tell whoever watches the "
                  "pipeline or schedule before you deploy it.")])]


def _nb_unpinned(o: dict[str, Any]) -> list[dict[str, Any]]:
    text = ("# 1. Find the version that works today (run once, then keep the line below).\n"
            "%pip show <package>\n\n"
            "# 2. Pin it in the install line.\n"
            "%pip install <package>==<version>\n")
    return [_tpl(o["control_id"], "notebook", "Pin the package version (notebook cell)", text,
                 [("<package>", "The package the install line names."),
                  ("<version>", "The version %pip show reports, like 1.2.3.")],
                 [("Better still, add the pinned package to a Fabric environment and attach it to the notebook, so "
                  "each run doesn't install it again.")])]


def _nb_short_vacuum(o: dict[str, Any]) -> list[dict[str, Any]]:
    sql = ("-- No RETAIN clause: VACUUM keeps the table's own retention,\n"
           "-- delta.deletedFileRetentionDuration, which is 7 days (168 hours) by default.\n"
           "VACUUM <table>;\n")
    py = ("from delta.tables import DeltaTable\n\n"
          "# No argument: vacuum() keeps the table's own retention, 7 days (168 hours) by default.\n"
          "DeltaTable.forName(spark, \"<table>\").vacuum()\n")
    places = [("<table>", ("The table the VACUUM at the cell and line named in the finding runs on, as "
                          "schema.table (or `schema`.`table` in Spark SQL when a name needs quoting)."))]
    caveats = [("This keeps the table's own retention, delta.deletedFileRetentionDuration: 7 days unless the "
                "table sets it. Check it first with SHOW TBLPROPERTIES <table>; if it is under 7 days, set it "
                "back to at least 7 days, or this VACUUM still removes history that recent."),
               ("Leave spark.databricks.delta.retentionDurationCheck.enabled on: it stops a RETAIN shorter than "
                "the table's retention.")]
    return [_tpl(o["control_id"], "sql", "VACUUM with the default retention (Spark SQL)", sql, places, caveats),
            _tpl(o["control_id"], "python", "VACUUM with the default retention (PySpark)", py, places, caveats)]


_BUILDERS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "FEA-SEC-005": _nb_secret,
    "FEA-DEVOPS-001": _nb_fixed_paths,
    "FEA-REL-001": _nb_swallowed,
    "FEA-REL-002": _nb_unpinned,
    "FEA-DE-003": _nb_short_vacuum,
    "FEA-REL-003": _pl_deactivated,
    "FEA-DEVOPS-002": _pl_fixed_workspace,
    "FEA-SM-001": _sm_bidirectional,
    "FEA-SM-003": _sm_fallback,
    "FEA-SM-006": _sm_visible_keys,
}
CONTROLS = tuple(sorted(_BUILDERS))


def for_outcome(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    """The proposals for one control outcome: empty unless it is a scored ``fail`` of a control with
    templates. Never raises on odd detail: a template it can't fill safely is left out."""
    if not isinstance(outcome, dict) or outcome.get("outcome") != "fail":
        return []
    build = _BUILDERS.get(str(outcome.get("control_id")))
    if build is None:
        return []
    detail = outcome.get("detail") if isinstance(outcome.get("detail"), dict) else {}
    try:
        return build({"control_id": outcome["control_id"], "detail": detail})
    except (TypeError, ValueError, AttributeError):
        return []
