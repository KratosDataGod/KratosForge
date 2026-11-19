"""Notebook and pipeline controls (FEA-301, FEA-302), decided from the definitions the optimization
engine already reads (evidence ``item.inspection``, excellence.inspect_items).

The notebook controls are notebook best-practice rules (notebook_practices, docs/notebook-best-practices.md)
turned into controls: no new detection logic, the same rule that underlines a cell in the editor
decides here. The pipeline controls read only what the engine's pipeline reader exposes for each
activity (its type, whether it is deactivated, and its typeProperties). Retry, timeout and failure
paths live in an activity's ``policy`` and ``dependsOn``, which that reader doesn't expose yet, so
there are no controls for them: a control that can't see the setting would only ever be not
evaluated, and it must never pass.

Evidence keeps rule ids with cell and line, and activity names and counts. Never the code, and
never what a secret looked like. A definition that wasn't read leaves these controls not evaluated.

References are to be re-checked against Microsoft Learn under FEA-106 before these controls leave
Preview.
"""

from __future__ import annotations

from typing import Any

from . import controls as ct

KIND = "item.inspection"
MAX_SAFE_RETENTION_DAYS = 7

_NB_DOCS = "https://kratosdatagod.github.io/kratosforge/notebook-best-practices/"
_FEA_DOC = {"title": "KratosForge Fabric Excellence Assessment",
            "url": "https://kratosdatagod.github.io/kratosforge/optimize/excellence-assessment/"}
_NOTEBOOKUTILS_DOC = {"title": "NotebookUtils (former MSSparkUtils) for Fabric",
                      "url": "https://learn.microsoft.com/fabric/data-engineering/notebook-utilities"}
_KEYVAULT_DOC = {"title": "Best practices for using Azure Key Vault",
                 "url": "https://learn.microsoft.com/azure/key-vault/general/best-practices"}
_NB_DEPLOY_DOC = {"title": "Notebook source control and deployment",
                  "url": "https://learn.microsoft.com/fabric/data-engineering/notebook-source-control-deployment"}
_LIBRARY_DOC = {"title": "Manage Apache Spark libraries in Microsoft Fabric",
                "url": "https://learn.microsoft.com/fabric/data-engineering/library-management"}
_NOTEBOOK_DOC = {"title": "How to use Microsoft Fabric notebooks",
                 "url": "https://learn.microsoft.com/fabric/data-engineering/how-to-use-notebook"}
_MAINTENANCE_DOC = {"title": "Use table maintenance feature to manage delta tables in Fabric",
                    "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-table-maintenance"}
_ACTIVITY_DOC = {"title": "Activity overview (Data Factory in Microsoft Fabric)",
                 "url": "https://learn.microsoft.com/fabric/data-factory/activity-overview"}
_ACTIVITY_STATE_DOC = {"title": "Pipelines and activities: activity state",
                       "url": "https://learn.microsoft.com/azure/data-factory/concepts-pipelines-activities"}
_PARAMETERS_DOC = {"title": "Parameters for Data Factory in Microsoft Fabric",
                   "url": "https://learn.microsoft.com/fabric/data-factory/parameters"}
_VARIABLES_DOC = {"title": "What is a variable library?",
                  "url": "https://learn.microsoft.com/fabric/cicd/variable-library/variable-library-overview"}


def _nb_doc(rule: str, title: str) -> dict[str, str]:
    return {"title": f"KratosForge notebook rule {rule}: {title}", "url": f"{_NB_DOCS}#{rule.lower()}"}


def _payload(ctx: ct.Context) -> tuple[str, dict[str, Any]]:
    r = ctx.one(KIND)
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], r["payload"] or {}


def _places(where: list[dict[str, Any]], count: int) -> str:
    shown = [f"cell {p['cell']}, line {p['line']}" for p in where[:5]]
    more = count - len(shown)
    return "; ".join(shown) + (f" and {more} more" if more > 0 else "")


def _nb_rule(ctx: ct.Context, rule: str, found: str, clean: str) -> dict[str, Any]:
    """Fail when ``rule`` matched the notebook, pass when it was checked and didn't."""
    rid, p = _payload(ctx)
    rules = p.get("rules")
    if not isinstance(rules, dict):
        return ct.not_evaluated("The notebook's code wasn't inspected.", needs="the notebook's definition")
    hit = rules.get(rule)
    if hit:
        count = int(hit.get("count") or 0)
        return ct.failed([rid], f"{found} ({rule}, {count} place{'s' if count != 1 else ''}: "
                                f"{_places(list(hit.get('where') or []), count)}).",
                         detail={"rule": rule, "count": count, "where": list(hit.get("where") or [])})
    return ct.passed([rid], f"{clean} ({rule}, {p.get('cells', 0)} code cells checked).")


# ---- notebooks (FEA-301)


def _secret(ctx: ct.Context) -> dict[str, Any]:
    """NB001. High: a key, password or connection string in a cell is in every copy of the notebook
    (Git, versions, exports, outputs) and gives anyone who can read the notebook what the secret
    opens, often outside Fabric. Not critical, because whether the value is live, and what it opens,
    can't be seen from the code; a profile can raise it."""
    return _nb_rule(ctx, "NB001", "A secret is written in the notebook's code",
                    "No key, password or connection string found in the code")


def _fixed_paths(ctx: ct.Context) -> dict[str, Any]:
    """NB002. Low: a OneLake path with a workspace or item id works where it was written and breaks
    (or, worse, reads and writes the original workspace) after the notebook is promoted. It is a
    lifecycle defect, not a data or security exposure, and the fix is mechanical."""
    return _nb_rule(ctx, "NB002", "A OneLake path names a workspace or item id",
                    "No OneLake path names a workspace or item id")


def _swallowed(ctx: ct.Context) -> dict[str, Any]:
    """NB012. Medium: ``except: pass`` turns a failed step into a run Fabric reports as succeeded, so
    downstream tables are stale or partial and nobody is told. That's a reliability failure with
    data consequences, but bounded to the step the handler wraps, so not high."""
    return _nb_rule(ctx, "NB012", "An error is caught and ignored",
                    "No error is caught and silently ignored")


def _unpinned(ctx: ct.Context) -> dict[str, Any]:
    """NB009. Low: an unpinned ``%pip install`` gets whatever release is newest at run time, so a
    run can change results or fail without a code change. Real but infrequent, and the fix is one
    version number."""
    return _nb_rule(ctx, "NB009", "A package is installed without a version",
                    "Every package installed in the code has a version")


def _short_vacuum(ctx: ct.Context) -> dict[str, Any]:
    """NB020. Medium: VACUUM with less than 7 days of retention can delete files a running reader or
    writer still needs (failed queries, failed concurrent writes) and ends time travel to those
    versions. Microsoft recommends at least 7 days. Not high: the current version of the table is
    kept, and only notebooks that call VACUUM with a short retention are affected."""
    return _nb_rule(ctx, "NB020", f"VACUUM keeps less than {MAX_SAFE_RETENTION_DAYS} days of history",
                    f"No VACUUM keeps less than {MAX_SAFE_RETENTION_DAYS} days of history")


# ---- pipelines (FEA-302)


def _pipeline(ctx: ct.Context, unknown_key: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    rid, p = _payload(ctx)
    if p.get("kind") != "pipeline" or not isinstance(p.get("activities"), int):
        return rid, p, ct.not_evaluated("The pipeline's activities weren't inspected.",
                                        needs="the pipeline's definition")
    unknown = list(p.get(unknown_key) or [])
    if unknown:
        return rid, p, ct.not_evaluated(f"What this checks couldn't be read for {len(unknown)} activit"
                                        f"{'ies' if len(unknown) != 1 else 'y'} ({', '.join(unknown[:5])}).",
                                        needs="the activities' definitions")
    return rid, p, None


def _names(names: list[str], count: int) -> str:
    more = count - len(names[:5])
    return ", ".join(names[:5]) + (f" and {more} more" if more > 0 else "")


def _deactivated(ctx: ct.Context) -> dict[str, Any]:
    """Low: a deactivated activity is skipped but, by default, reported as Succeeded, so the
    activities after it run and the pipeline succeeds while that step's work isn't done. Usually a
    leftover from debugging. Low because it is visible in the canvas, and deactivating a step on
    purpose is legitimate; a profile can turn this control off."""
    rid, p, unknown = _pipeline(ctx, "state_unknown")
    if unknown:
        return unknown
    count = int(p.get("inactive_count") or 0)
    if count:
        return ct.failed([rid], f"{count} activit{'ies are' if count != 1 else 'y is'} deactivated: "
                                f"{_names(list(p.get('inactive') or []), count)}.",
                         detail={"inactive": list(p.get("inactive") or []), "count": count})
    return ct.passed([rid], f"None of its {p['activities']} activities is deactivated.")


def _fixed_workspace(ctx: ct.Context) -> dict[str, Any]:
    """Low: an activity whose typeProperties name a workspace id as text (not a parameter, variable
    library value or expression) keeps pointing at that workspace after the pipeline is promoted, so
    a test pipeline can run production notebooks or write production tables. A lifecycle defect with
    a mechanical fix; low, like its notebook counterpart FEA-DEVOPS-001."""
    rid, p, unknown = _pipeline(ctx, "properties_unknown")
    if unknown:
        return unknown
    count = int(p.get("fixed_workspace_count") or 0)
    if count:
        return ct.failed([rid], f"{count} activit{'ies name' if count != 1 else 'y names'} a fixed workspace id "
                                f"rather than a parameter: {_names(list(p.get('fixed_workspace') or []), count)}.",
                         detail={"activities": list(p.get("fixed_workspace") or []), "count": count})
    return ct.passed([rid], f"None of its {p['activities']} activities names a fixed workspace id.")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


def _nb(**kw: Any) -> ct.Control:
    return ct.Control(version="1.0.0", scope="item", item_types=("Notebook",), evidence_kinds=(KIND,), **kw)


def _pl(**kw: Any) -> ct.Control:
    return ct.Control(version="1.0.0", scope="item", item_types=("DataPipeline",), evidence_kinds=(KIND,), **kw)


CONTROLS = (
    _nb(id="FEA-SEC-005", dimension="security", title="Secret written in notebook code", severity="high",
        requirement="microsoft_recommendation", evaluate=_secret,
        references=(_NOTEBOOKUTILS_DOC, _KEYVAULT_DOC, _nb_doc("NB001", "Secret written in a cell")),
        description="Notebook rule NB001: a key, password or connection string in a cell. Only where it is, "
                    "never what it is, is kept.",
        remediation=_t("A secret in code is in every copy of the notebook: Git, its versions, exports and "
                       "outputs. Anyone who can read the notebook can use it.",
                       "Keep the secret in Azure Key Vault and read it at run time with "
                       "notebookutils.credentials.getSecret; then rotate the secret, since it has been exposed.",
                       ["Open the cell and line named in the finding.", "Store the value in Key Vault.",
                        "Replace it with notebookutils.credentials.getSecret(vault, name).",
                        "Rotate the secret at its source.", "Remove it from Git history if the notebook is in Git.",
                        "Assess again."],
                       "The secret can't be read from the notebook, its versions or its Git history.", "low", "low",
                       "The notebook still runs, and the control passes.",
                       "Put the previous code back (don't put the old secret back: it is rotated).")),
    _nb(id="FEA-DEVOPS-001", dimension="devops", title="Notebook path names a workspace or item id", severity="low",
        requirement="kratosforge_recommendation", evaluate=_fixed_paths,
        references=(_NB_DEPLOY_DOC, _nb_doc("NB002", "Workspace or lakehouse id written in a path")),
        description="Notebook rule NB002: an abfss:// or OneLake URL with a workspace or item id.",
        remediation=_t("A path with a workspace id keeps reading and writing that workspace after the notebook "
                       "is deployed to another one.",
                       "Use the default lakehouse's relative paths (Tables/..., Files/...) or a parameter.",
                       ["Open the cell and line named in the finding.", "Replace the id path with a relative path or a parameter.", "Set the default lakehouse, with a deployment rule per stage.",
                        "Run it in each stage.", "Assess again."],
                       "The notebook reads and writes its own stage's data after deployment.", "low", "low",
                       "A run in another stage touches only that stage's lakehouse.", "Put the previous path back.")),
    _nb(id="FEA-REL-001", dimension="reliability", title="Notebook ignores errors", severity="medium",
        requirement="kratosforge_recommendation", evaluate=_swallowed,
        references=(_NOTEBOOK_DOC, _nb_doc("NB012", "Error caught and ignored")),
        description="Notebook rule NB012: except ...: pass.",
        remediation=_t("An ignored error makes a failed step look like a successful run, so tables are stale "
                       "or partial and nobody is alerted.",
                       "Catch only the error you expect and handle it; log anything else and let the run fail.",
                       ["Open the cell and line named in the finding.", "Name the exception you expect.",
                        "Log it, or re-raise it.", "Run once with a forced failure to see the run fail.",
                        "Assess again."],
                       "Failures show up as failed runs that can be alerted on.", "low", "medium",
                       "A forced failure fails the run; a normal run still succeeds.", "Put the previous handler back.")),
    _nb(id="FEA-REL-002", dimension="reliability", title="Package installed without a version", severity="low",
        requirement="kratosforge_recommendation", evaluate=_unpinned,
        references=(_LIBRARY_DOC, _nb_doc("NB009", "Package installed without a version")),
        description="Notebook rule NB009: %pip install without ==version.",
        remediation=_t("An unpinned install takes whatever release is newest when the notebook runs, so a run can "
                       "change results or fail without any change to the code.",
                       "Pin each package (package==1.2.3), or better, put it in a Fabric environment.",
                       ["Find the version that works today (pip show in the notebook).",
                        "Pin it in the install line, or add it to an environment attached to the notebook.",
                        "Run once.", "Assess again."],
                       "Runs use the same libraries every time.", "low", "low",
                       "The run succeeds with the pinned version.", "Remove the pin.")),
    _nb(id="FEA-DE-003", dimension="data_engineering", title="VACUUM keeps less than 7 days", severity="medium",
        requirement="microsoft_recommendation", evaluate=_short_vacuum,
        references=(_MAINTENANCE_DOC, _nb_doc("NB020", "VACUUM keeps less than 7 days of history")),
        description="Notebook rule NB020: VACUUM with a retention under 168 hours.",
        remediation=_t("Files a running query or write still needs can be deleted, and time travel to those "
                       "versions stops working.",
                       "Keep the default 7 days (168 hours) unless nothing else reads or writes the table.",
                       ["Open the cell and line named in the finding.", "Remove the RETAIN clause, or set 168 hours.",
                        "Assess again."],
                       "Concurrent readers and writers don't lose files, and time travel works for 7 days.", "low",
                       "low", "The next VACUUM keeps 7 days of history.",
                       "Put the shorter retention back (only if nothing else uses the table).")),
    _pl(id="FEA-REL-003", dimension="reliability", title="Pipeline has deactivated activities", severity="low",
        requirement="kratosforge_recommendation", evaluate=_deactivated,
        references=(_ACTIVITY_DOC, _ACTIVITY_STATE_DOC, _FEA_DOC),
        description="An activity is deactivated: skipped, and by default reported as Succeeded.",
        remediation=_t("A deactivated activity is skipped but reported as Succeeded by default, so the pipeline "
                       "succeeds while that step's work isn't done.",
                       "Activate it again, or remove it if it is no longer needed.",
                       ["Open the pipeline and find the activities named in the finding.",
                        "Decide whether each is still needed.", "Activate or delete it.", "Run once.",
                        "Assess again."],
                       "A pipeline that succeeds did every step it shows.", "low", "medium",
                       "The run includes the activity, or the canvas no longer shows it.",
                       "Deactivate it again.")),
    _pl(id="FEA-DEVOPS-002", dimension="devops", title="Pipeline activity names a fixed workspace", severity="low",
        requirement="kratosforge_recommendation", evaluate=_fixed_workspace,
        references=(_PARAMETERS_DOC, _VARIABLES_DOC, _FEA_DOC),
        description="An activity's settings name a workspace id as text, not a parameter or variable.",
        remediation=_t("An activity with a fixed workspace id keeps pointing at that workspace after the pipeline "
                       "is deployed, so a test run can touch production items.",
                       "Take the workspace from a pipeline parameter or a variable library value.",
                       ["Open the activities named in the finding.", "Add a parameter or a variable library value for the workspace.", "Use it in the activity's workspace setting.",
                        "Set the value per stage.", "Run it in each stage.", "Assess again."],
                       "The pipeline runs against its own stage after deployment.", "medium", "low",
                       "A run in another stage reads and writes only that stage.", "Put the fixed id back.")),
)
