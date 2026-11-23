"""Check a Databricks Asset Bundle on this PC (Preview): databricks.yml and the files it includes are
read with yaml (never run), and every problem found is reported with the field's path, such as
``resources.jobs.nightly.tasks[2].notebook_task.notebook_path``, its file and its line.

Nothing here calls the network, the Databricks CLI or a workspace. It is not ``databricks bundle
validate``: that command also resolves targets, variables and the workspace, and knows fields added
after this list was written. This checks the structure the configuration reference documents.

Sources (docs.databricks.com, read 2026-11):
- /dev-tools/bundles/settings and /dev-tools/bundles/reference: top-level mappings, ``bundle``,
  ``targets`` (``mode`` is ``development`` or ``production``; "One and only one of these targets can be
  set to default: true"), ``variables`` (``type``'s only valid value is ``complex``), ``include``
  ("path globs ... relative to the location of the bundle configuration file in which the path globs
  are specified") and the resource types.
- /dev-tools/bundles/job-task-types and the Jobs API (/api/workspace/jobs/create): job and task fields,
  the task types, and "The path for the notebook to deploy is relative to the configuration file in
  which this task is declared" (the same for a Python file). Workspace paths begin with a slash and
  paths into a remote repository (``source: GIT`` or a job's ``git_source``) are relative to the repo,
  so neither is looked for on this PC.
- /api/workspace/pipelines/create: pipeline fields and ``libraries`` (notebook, file, glob, whl, jar, maven).
- /dev-tools/bundles/variables: ``${var.<name>}``, ``${resources.<type>.<name>.<field>}``, and "bundle
  validation fails if type is set to complex and the default defined for the variable is a single value".
  Its substitutions are ``${bundle.name}``, ``${bundle.target}`` ("Use this substitution instead of
  ${bundle.environment}"), ``${workspace.host}``, ``${workspace.current_user.domain_friendly_name}``,
  ``.short_name``, ``.userName``, ``${workspace.file_path}`` and ``${workspace.root_path}``; "To determine
  valid substitutions, use the bundle configuration reference", whose ``bundle`` and ``workspace`` keys are
  BUNDLE and WORKSPACE below. The page doesn't say whether a variable's default may use ``${var.<name>}``,
  so that isn't checked either way.
- /dev-tools/bundles/reference (read 2026-11-23): ``sync.include`` and ``sync.exclude`` are sequences of
  file or path globs; ``artifacts.<name>.path`` (String) is "The local path of the directory for the
  artifact. Paths are relative to the location of the bundle configuration file."
- /dev-tools/bundles/resources (read 2026-11-23): "The REST API object's supported create request fields,
  expressed as YAML, are the resource's supported keys", beside bundle keys such as ``lifecycle``. Its own
  key tables for alert, app and catalog were read whole; the page as fetched ends in the catalog section,
  so the other types' bundle tables weren't read. experiments, models and sql_warehouses are keyed from
  their create pages (/api/workspace/experiments/createexperiment, /modelregistry/createmodel,
  /warehouses/create) plus ``lifecycle`` and ``permissions``. Left unchecked: clusters and
  model_serving_endpoints (their create pages were cut off before the request body ended), schemas,
  volumes, registered_models and quality_monitors (Unity Catalog types: the catalog table shows bundle-only
  ``grants`` that a create page doesn't list, and their own tables weren't read), dashboards (an alert has a
  bundle-only ``file_path``; the dashboard table wasn't read), secret_scopes (the create request names
  ``scope`` and ``scope_backend_type``, and how a bundle names them wasn't read), and the rest.
- Types (Jobs API create): a job's ``max_concurrent_runs`` and ``timeout_seconds`` are int32, ``tasks`` an
  array, ``email_notifications`` an object; a task's ``depends_on`` and ``libraries`` are arrays,
  ``timeout_seconds`` and ``max_retries`` int32, ``email_notifications`` an object. Pipelines API create:
  ``libraries``, ``clusters`` and ``notifications`` are arrays, ``configuration`` an object. A value that is
  a ``${...}`` substitution isn't type-checked (a complex variable can stand for a list or a mapping).
- A key given twice in one mapping is an error: YAML keys in a mapping are unique, and PyYAML would keep
  the last one silently.

The Databricks CLI's JSON schema (``databricks bundle schema``) is not vendored: github.com/databricks/cli
is under the Databricks License, which allows use "solely for the purposes of using the Licensed
Materials within or connecting to the Databricks Services". The key lists below are written from the
documentation above instead.

Unknown keys are warnings, not errors: Databricks adds fields, and a bundle that uses a newer one is not
wrong. ``${bundle.environment}`` is a warning too (the docs say to use ``${bundle.target}``). Every other
check is an error.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

ERROR, WARNING = "error", "warning"

TOP_LEVEL = frozenset({"artifacts", "bundle", "experimental", "include", "permissions", "presets", "python",
                       "resources", "run_as", "scripts", "sync", "targets", "variables", "workspace"})
BUNDLE = frozenset({"cluster_id", "compute_id", "databricks_cli_version", "deployment", "engine", "git", "name",
                    "uuid"})
TARGET = frozenset({"artifacts", "bundle", "cluster_id", "compute_id", "default", "git", "mode", "permissions",
                    "presets", "resources", "run_as", "sync", "variables", "workspace"})
MODES = ("development", "production")
VARIABLE = frozenset({"default", "description", "lookup", "type"})
RESOURCE_TYPES = frozenset({
    "alerts", "apps", "catalogs", "clusters", "dashboards", "database_catalogs", "database_instances",
    "experiments", "external_locations", "features", "genie_spaces", "instance_pools", "job_runs", "jobs",
    "mcp_services", "model_provider_services", "model_services", "model_serving_endpoints", "models",
    "pipelines", "postgres_branches", "postgres_catalogs", "postgres_databases", "postgres_endpoints",
    "postgres_projects", "postgres_roles", "postgres_snapshot_schedules", "postgres_synced_tables",
    "quality_monitors", "registered_models", "schemas", "secret_scopes", "sql_warehouses",
    "synced_database_tables", "vector_search_endpoints", "vector_search_indexes", "volumes"})
# Keys every resource takes in a bundle, beside the API's own fields.
_BUNDLE_RESOURCE = frozenset({"lifecycle", "permissions"})
JOB = _BUNDLE_RESOURCE | frozenset({
    "budget_policy_id", "continuous", "deployment", "description", "edit_mode", "email_notifications",
    "environments", "format", "git_source", "health", "job_clusters", "max_concurrent_runs", "name",
    "notification_settings", "parameters", "performance_target", "queue", "run_as", "schedule", "tags", "tasks",
    "timeout_seconds", "trigger", "usage_policy_id", "webhook_notifications"})
PIPELINE = _BUNDLE_RESOURCE | frozenset({
    "allow_duplicate_names", "budget_policy_id", "catalog", "channel", "clusters", "configuration", "continuous",
    "deployment", "development", "edition", "environment", "event_log", "filters", "gateway_definition", "id",
    "ingestion_definition", "libraries", "name", "notifications", "parent_path", "photon", "restart_window",
    "root_path", "run_as", "schema", "serverless", "storage", "tags", "target", "trigger", "usage_policy_id"})
# From the resources page's own key tables.
ALERT = frozenset({"custom_description", "custom_summary", "custom_template_format", "display_name", "evaluation",
                   "file_path", "lifecycle", "parent_path", "permissions", "query_text", "run_as", "schedule",
                   "warehouse_id"})
APP = frozenset({"budget_policy_id", "compute_max_instances", "compute_min_instances", "compute_size", "config",
                 "description", "git_repository", "git_source", "lifecycle", "name", "permissions", "resources",
                 "source_code_path", "space", "telemetry_export_destinations", "usage_policy_id", "user_api_scopes"})
CATALOG = frozenset({"comment", "connection_name", "custom_max_retention_hours", "grants", "lifecycle",
                     "managed_encryption_settings", "name", "options", "properties", "provider_name", "share_name",
                     "storage_root"})
# From the REST API create pages, beside the bundle's own keys.
EXPERIMENT = _BUNDLE_RESOURCE | frozenset({"artifact_location", "name", "tags"})
MODEL = _BUNDLE_RESOURCE | frozenset({"description", "name", "tags"})
SQL_WAREHOUSE = _BUNDLE_RESOURCE | frozenset({
    "auto_stop_mins", "channel", "cluster_size", "creator_name", "enable_photon", "enable_serverless_compute",
    "instance_profile_arn", "max_num_clusters", "min_num_clusters", "name", "spot_instance_policy",
    "statement_timeout", "tags", "warehouse_type"})
# Resource types whose keys are checked; the others' fields couldn't all be read (see the module docstring).
RESOURCE_KEYS = {"alerts": ALERT, "apps": APP, "catalogs": CATALOG, "experiments": EXPERIMENT, "jobs": JOB,
                 "models": MODEL, "pipelines": PIPELINE, "sql_warehouses": SQL_WAREHOUSE}
# ${bundle.<key>} and ${workspace.<key>}: the reference's keys and the variables page's substitutions.
WORKSPACE = frozenset({
    "account_id", "artifact_path", "auth_type", "azure_client_id", "azure_environment", "azure_login_app_id",
    "azure_tenant_id", "azure_use_msi", "azure_workspace_resource_id", "client_id", "file_path",
    "google_service_account", "host", "profile", "resource_path", "root_path", "state_path", "sync_mode",
    "workspace_id"})
BUNDLE_REFS = BUNDLE | {"target"}
WORKSPACE_REFS = WORKSPACE | {"current_user"}
# Expected types: "int", "list" or "mapping".
JOB_TYPES = {"max_concurrent_runs": "int", "timeout_seconds": "int", "tasks": "list", "email_notifications": "mapping"}
TASK_TYPES_OF = {"depends_on": "list", "libraries": "list", "timeout_seconds": "int", "max_retries": "int",
                 "email_notifications": "mapping"}
PIPELINE_TYPES = {"libraries": "list", "clusters": "list", "notifications": "list", "configuration": "mapping"}
_TYPE_NAMES = {"int": "an integer", "list": "a list", "mapping": "a mapping"}
TASK_TYPES = frozenset({
    "ai_function_task", "ai_runtime_task", "alert_task", "clean_rooms_notebook_task", "condition_task",
    "dashboard_task", "dbt_task", "for_each_task", "gen_ai_compute_task", "notebook_task", "pipeline_task",
    "power_bi_task", "python_wheel_task", "run_job_task", "spark_jar_task", "spark_python_task",
    "spark_submit_task", "sql_task"})
TASK = TASK_TYPES | frozenset({
    "compute", "depends_on", "description", "disable_auto_optimization", "disabled", "email_notifications",
    "environment_key", "environment_variables_key", "existing_cluster_id", "health", "job_cluster_key",
    "libraries", "max_retries", "min_retry_interval_millis", "new_cluster", "notification_settings",
    "retry_on_timeout", "run_if", "task_key", "timeout_seconds", "webhook_notifications"})
# A notebook path may leave out its extension; these are the files it can name.
NOTEBOOK_EXTENSIONS = ("", ".py", ".ipynb", ".sql", ".scala", ".r", ".R")

_REF = re.compile(r"\$\{([^}]*)\}")
_VAR = re.compile(r"^var\.([A-Za-z0-9_-]+)")
_RESOURCE_REF = re.compile(r"^resources\.([A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)(\.|$)")
_SCOPE_REF = re.compile(r"^(bundle|workspace)\.([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class Finding:
    severity: str          # "error" or "warning"
    path: str              # the field, e.g. resources.jobs.nightly.tasks[2].notebook_task.notebook_path
    message: str
    file: str              # relative to the bundle folder, with forward slashes
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Doc:
    """One YAML file: its parsed data and the line of each path in it."""
    name: str
    path: Path
    data: Any
    lines: dict[str, int]


class _Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.findings: list[Finding] = []

    def add(self, doc: _Doc | str, severity: str, path: str, message: str, line: int | None = None,
            column: int | None = None) -> None:
        if isinstance(doc, _Doc):
            name = doc.name
            if line is None:
                line = _line_of(doc.lines, path)
        else:
            name = doc
        self.findings.append(Finding(severity, path, message, name, line, column))

    # ------------------------------------------------------------ reading the files

    def load(self, path: Path) -> _Doc | None:
        name = _rel(path, self.root)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.add(name, ERROR, "", f"The file can't be read ({type(exc).__name__}).")
            return None
        loader = yaml.SafeLoader(text)
        try:
            node = loader.get_single_node()
            data = loader.construct_document(node) if node is not None else None
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            problem = " ".join(str(exc.problem or exc.context or "invalid YAML").split())
            self.add(name, ERROR, "", f"The YAML can't be read: {problem}.",
                     mark.line + 1 if mark else None, mark.column + 1 if mark else None)
            return None
        except yaml.YAMLError as exc:
            self.add(name, ERROR, "", f"The YAML can't be read ({type(exc).__name__}).")
            return None
        finally:
            loader.dispose()
        lines: dict[str, int] = {}
        if node is not None:
            _record_lines(node, "", lines)
            for key_path, first, again in _duplicate_keys(node, ""):
                key = key_path.rsplit(".", 1)[-1]
                self.add(name, ERROR, key_path, f"The key {key!r} is given twice in one mapping, on lines {first} "
                                                f"and {again}; only the one on line {again} would be read.", again)
        doc = _Doc(name, path, data if data is not None else {}, lines)
        if not isinstance(doc.data, dict):
            self.add(doc, ERROR, "", "A bundle configuration file is a mapping of keys, such as bundle: and resources:.",
                     1)
            return None
        return doc

    def includes(self, main: _Doc) -> list[_Doc]:
        found: list[_Doc] = []
        raw = main.data.get("include")
        if raw is None:
            return found
        if not isinstance(raw, list):
            self.add(main, ERROR, "include", "include is a list of path globs.")
            return found
        seen = {main.path.resolve()}
        base = main.path.parent
        for i, pattern in enumerate(raw):
            where = f"include[{i}]"
            if not isinstance(pattern, str) or not pattern.strip():
                self.add(main, ERROR, where, "Each include is a path glob, such as resources/*.yml.")
                continue
            matches = sorted(Path(p) for p in glob.glob(os.path.join(str(base), pattern), recursive=True)
                             if os.path.isfile(p))
            if not matches:
                self.add(main, ERROR, where, f"The include {pattern!r} matches no file.")
                continue
            for path in matches:
                real = path.resolve()
                if real in seen:
                    continue
                seen.add(real)
                doc = self.load(path)
                if doc is not None:
                    found.append(doc)
        return found

    # ------------------------------------------------------------ the checks

    def run(self, docs: list[_Doc]) -> None:
        variables: set[str] = set()
        resources: dict[str, set[str]] = {}
        jobs: dict[str, set[str]] = {}       # top-level jobs and their task keys, for target overrides
        for doc in docs:
            if isinstance(doc.data.get("variables"), dict):
                variables.update(str(k) for k in doc.data["variables"])
            for _, where in _scopes(doc.data):
                declared = where.get("resources")
                for kind, items in declared.items() if isinstance(declared, dict) else []:
                    if isinstance(items, dict):
                        resources.setdefault(str(kind), set()).update(str(k) for k in items)
            top = doc.data.get("resources")
            top_jobs = top.get("jobs") if isinstance(top, dict) else None
            for name, job in top_jobs.items() if isinstance(top_jobs, dict) else []:
                keys = jobs.setdefault(str(name), set())
                tasks = job.get("tasks") if isinstance(job, dict) else None
                for task in tasks if isinstance(tasks, list) else []:
                    if isinstance(task, dict) and isinstance(task.get("task_key"), str):
                        keys.add(task["task_key"])

        if not any(isinstance(d.data.get("bundle"), dict) and d.data["bundle"].get("name") for d in docs):
            main = docs[0]
            has_bundle = isinstance(main.data.get("bundle"), dict)
            self.add(main, ERROR, "bundle.name" if has_bundle else "bundle",
                     "bundle.name is required: the bundle's name." if has_bundle
                     else "The bundle mapping with its name is required.", None if has_bundle else 1)

        defaults: dict[str, tuple[_Doc, str]] = {}
        for doc in docs:
            self.top_level(doc)
            self.targets(doc, defaults, variables)
            self.variables(doc)
            for prefix, scope in _scopes(doc.data):
                self.resources(doc, prefix, scope, jobs, in_target=bool(prefix))
                self.sync(doc, prefix, scope)
                self.artifacts(doc, prefix, scope)
            self.references(doc, variables, resources)
        if len(defaults) > 1:
            names = sorted(defaults)
            for name in names:
                doc, path = defaults[name]
                self.add(doc, ERROR, path, f"At most one target can be the default; {', '.join(names)} all set "
                                           "default: true.")

    def top_level(self, doc: _Doc) -> None:
        for key in doc.data:
            if str(key) not in TOP_LEVEL:
                self.add(doc, WARNING, str(key), f"Unknown top-level key {key!r}.")
        bundle = doc.data.get("bundle")
        if bundle is not None:
            self.mapping_keys(doc, "bundle", bundle, BUNDLE)
            if isinstance(bundle, dict) and "name" in bundle and not (isinstance(bundle["name"], str) and bundle["name"].strip()):
                self.add(doc, ERROR, "bundle.name", "bundle.name is the bundle's name, a string.")

    def targets(self, doc: _Doc, defaults: dict[str, tuple[_Doc, str]], variables: set[str]) -> None:
        targets = doc.data.get("targets")
        if targets is None:
            return
        if not isinstance(targets, dict):
            self.add(doc, ERROR, "targets", "targets is a mapping of target names to their settings.")
            return
        for name, spec in targets.items():
            where = f"targets.{name}"
            if spec is None:
                continue
            if not self.mapping_keys(doc, where, spec, TARGET):
                continue
            if "mode" in spec and spec["mode"] not in MODES:
                self.add(doc, ERROR, f"{where}.mode", f"mode is development or production, not {spec['mode']!r}.")
            if "default" in spec and not isinstance(spec["default"], bool):
                self.add(doc, ERROR, f"{where}.default", "default is true or false.")
            elif spec.get("default") is True:
                defaults.setdefault(str(name), (doc, f"{where}.default"))
            if isinstance(spec.get("bundle"), dict):
                self.mapping_keys(doc, f"{where}.bundle", spec["bundle"], BUNDLE)
            overrides = spec.get("variables")
            for var in overrides if isinstance(overrides, dict) else []:
                if str(var) not in variables:
                    self.add(doc, ERROR, f"{where}.variables.{var}",
                             f"The target sets variable {var!r}, which isn't declared under variables.")

    def variables(self, doc: _Doc) -> None:
        variables = doc.data.get("variables")
        if variables is None:
            return
        if not isinstance(variables, dict):
            self.add(doc, ERROR, "variables", "variables is a mapping of variable names to their settings.")
            return
        for name, spec in variables.items():
            where = f"variables.{name}"
            if spec is None:
                continue
            if not self.mapping_keys(doc, where, spec, VARIABLE):
                continue
            if "type" in spec and spec["type"] != "complex":
                self.add(doc, ERROR, f"{where}.type", "type's only valid value is complex; leave it out for a "
                                                      "string variable.")
            elif spec.get("type") == "complex" and "default" in spec and not isinstance(spec["default"], (dict, list)):
                self.add(doc, ERROR, f"{where}.default", "A complex variable's default is a mapping or a list, "
                                                         "not a single value.")

    def resources(self, doc: _Doc, prefix: str, scope: dict[str, Any], jobs: dict[str, set[str]],
                  in_target: bool) -> None:
        resources = scope.get("resources")
        if resources is None:
            return
        where = f"{prefix}resources"
        if not isinstance(resources, dict):
            self.add(doc, ERROR, where, "resources is a mapping of resource types, such as jobs: and pipelines:.")
            return
        for kind, items in resources.items():
            kpath = f"{where}.{kind}"
            if str(kind) not in RESOURCE_TYPES:
                self.add(doc, WARNING, kpath, f"Unknown resource type {kind!r}.")
                continue
            if items is None:
                continue
            if not isinstance(items, dict):
                self.add(doc, ERROR, kpath, f"{kind} is a mapping of resource names to their settings.")
                continue
            for name, spec in items.items():
                rpath = f"{kpath}.{name}"
                if spec is None:
                    continue
                keys = RESOURCE_KEYS.get(str(kind))
                if keys is None:
                    if not isinstance(spec, dict):
                        self.add(doc, ERROR, rpath, "A resource is a mapping of its settings.")
                    continue
                if not self.mapping_keys(doc, rpath, spec, keys):
                    continue
                if kind == "jobs":
                    top_keys = jobs.get(str(name)) if in_target else None
                    self.job(doc, rpath, spec, top_keys)
                elif kind == "pipelines":
                    self.pipeline(doc, rpath, spec)

    def sync(self, doc: _Doc, prefix: str, scope: dict[str, Any]) -> None:
        sync = scope.get("sync")
        if not isinstance(sync, dict):
            return
        for key in ("include", "exclude"):
            value = sync.get(key)
            where = f"{prefix}sync.{key}"
            if value is None:
                continue
            if not isinstance(value, list):
                self.add(doc, ERROR, where, f"sync.{key} is a list of file or path globs (strings).")
                continue
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    self.add(doc, ERROR, f"{where}[{i}]", f"Each sync.{key} entry is a file or path glob, a string.")

    def artifacts(self, doc: _Doc, prefix: str, scope: dict[str, Any]) -> None:
        """A relative artifacts.<name>.path is a folder relative to the YAML file that names it."""
        artifacts = scope.get("artifacts")
        for name, spec in artifacts.items() if isinstance(artifacts, dict) else []:
            if not isinstance(spec, dict) or spec.get("path") is None:
                continue
            where = f"{prefix}artifacts.{name}.path"
            value = spec["path"]
            if not isinstance(value, str):
                self.add(doc, ERROR, where, "path is the artifact's local folder, a string.")
                continue
            if not value.strip() or _not_local(value):
                continue
            folder = doc.path.parent / value
            shown = _rel(folder, self.root)
            if not folder.exists():
                self.add(doc, ERROR, where, f"{value!r} doesn't exist: no folder at {shown}, relative to {doc.name}.")
            elif not folder.is_dir():
                self.add(doc, ERROR, where, f"{value!r} is a file; path is the artifact's folder ({shown}, relative "
                                            f"to {doc.name}).")

    def types(self, doc: _Doc, where: str, spec: dict[str, Any], expected: dict[str, str]) -> None:
        """An error for each field in ``expected`` whose value isn't its type. ``${...}`` values are skipped."""
        for key, kind in expected.items():
            value = spec.get(key)
            if value is None or (isinstance(value, str) and "${" in value):
                continue
            ok = (isinstance(value, int) and not isinstance(value, bool) if kind == "int"
                  else isinstance(value, list) if kind == "list" else isinstance(value, dict))
            if not ok:
                self.add(doc, ERROR, f"{where}.{key}", f"{key} is {_TYPE_NAMES[kind]}, not {_describe(value)}.")

    def job(self, doc: _Doc, jpath: str, job: dict[str, Any], top_keys: set[str] | None) -> None:
        self.types(doc, jpath, job, JOB_TYPES)
        tasks = job.get("tasks")
        if not isinstance(tasks, list):
            return
        repo = bool(job.get("git_source"))
        keys: dict[str, int] = {}
        for i, task in enumerate(tasks):
            key = task.get("task_key") if isinstance(task, dict) else None
            if isinstance(key, str) and key:
                if key in keys:
                    self.add(doc, ERROR, f"{jpath}.tasks[{i}].task_key",
                             f"task_key {key!r} is used by tasks[{keys[key]}] too; each task_key in a job is unique.")
                else:
                    keys[key] = i
        known = set(keys) | (top_keys or set())
        for i, task in enumerate(tasks):
            # A target's task overrides the job's task with the same task_key, so it need not name a type.
            overrides = top_keys is not None and isinstance(task, dict) and task.get("task_key") in top_keys
            self.task(doc, f"{jpath}.tasks[{i}]", task, known, repo, needs_type=not overrides)

    def task(self, doc: _Doc, tpath: str, task: Any, known: set[str], repo: bool, needs_type: bool) -> None:
        if not self.mapping_keys(doc, tpath, task, TASK, what="task key"):
            return
        self.types(doc, tpath, task, TASK_TYPES_OF)
        key = task.get("task_key")
        if not (isinstance(key, str) and key.strip()):
            self.add(doc, ERROR, f"{tpath}.task_key" if "task_key" in task else tpath,
                     "task_key is required: each task has a unique task_key.")
        types = [t for t in task if t in TASK_TYPES]
        if len(types) > 1:
            self.add(doc, ERROR, tpath, f"A task has one task type; this one has {len(types)}: {', '.join(types)}.")
        elif not types and needs_type:
            self.add(doc, ERROR, tpath, "A task needs a task type, such as notebook_task or spark_python_task.")
        deps = task.get("depends_on")
        for j, dep in enumerate(deps) if isinstance(deps, list) else []:
            dpath = f"{tpath}.depends_on[{j}]"
            name = dep.get("task_key") if isinstance(dep, dict) else None
            if not isinstance(name, str) or not name:
                self.add(doc, ERROR, f"{dpath}.task_key" if isinstance(dep, dict) else dpath,
                         "Each depends_on entry names a task_key.")
            elif name not in known and "${" not in name:
                self.add(doc, ERROR, f"{dpath}.task_key", f"depends_on names task_key {name!r}, which no task "
                                                          "in this job has.")
        in_repo = repo
        nb = task.get("notebook_task")
        if isinstance(nb, dict):
            in_repo = repo or str(nb.get("source", "")).upper() == "GIT"
            if not in_repo:
                self.local_path(doc, f"{tpath}.notebook_task.notebook_path", nb.get("notebook_path"), notebook=True)
        spy = task.get("spark_python_task")
        if isinstance(spy, dict) and not (repo or str(spy.get("source", "")).upper() == "GIT"):
            self.local_path(doc, f"{tpath}.spark_python_task.python_file", spy.get("python_file"))
        sql = task.get("sql_task")
        if isinstance(sql, dict) and isinstance(sql.get("file"), dict):
            f = sql["file"]
            if not (repo or str(f.get("source", "")).upper() == "GIT"):
                self.local_path(doc, f"{tpath}.sql_task.file.path", f.get("path"))
        each = task.get("for_each_task")
        if isinstance(each, dict) and "task" in each:
            self.task(doc, f"{tpath}.for_each_task.task", each["task"], known, repo, needs_type=True)

    def pipeline(self, doc: _Doc, ppath: str, pipeline: dict[str, Any]) -> None:
        self.types(doc, ppath, pipeline, PIPELINE_TYPES)
        libraries = pipeline.get("libraries")
        if not isinstance(libraries, list):
            return
        for i, lib in enumerate(libraries):
            if not isinstance(lib, dict):
                continue
            for kind in ("notebook", "file"):
                if isinstance(lib.get(kind), dict):
                    self.local_path(doc, f"{ppath}.libraries[{i}].{kind}.path", lib[kind].get("path"),
                                    notebook=kind == "notebook")

    def local_path(self, doc: _Doc, path: str, value: Any, notebook: bool = False) -> None:
        """A path relative to the YAML file that names it must exist on this PC. Workspace paths (a leading
        slash), URIs (dbfs:/, s3://), and paths with a ${...} substitution aren't checked."""
        if not isinstance(value, str) or not value.strip():
            return
        if _not_local(value):
            return
        base = doc.path.parent / value
        tries = NOTEBOOK_EXTENSIONS if notebook else ("",)
        if not any(Path(f"{base}{ext}").is_file() for ext in tries):
            shown = _rel(base, self.root)
            self.add(doc, ERROR, path, f"{value!r} doesn't exist: no file at {shown}"
                     + (" (with or without a notebook extension)" if notebook else "")
                     + f", relative to {doc.name}.")

    def references(self, doc: _Doc, variables: set[str], resources: dict[str, set[str]]) -> None:
        for path, text in _strings(doc.data, ""):
            for ref in _REF.findall(text):
                ref = ref.strip()
                var = _VAR.match(ref)
                if var and var.group(1) not in variables:
                    self.add(doc, ERROR, path, f"${{{ref}}} refers to variable {var.group(1)!r}, which isn't "
                                               "declared under variables.")
                    continue
                scope = _SCOPE_REF.match(ref)
                if scope:
                    kind, field = scope.groups()
                    if kind == "bundle" and field == "environment":
                        self.add(doc, WARNING, path, f"${{{ref}}}: the variables page says to use ${{bundle.target}} "
                                                     "instead of ${bundle.environment}.")
                    elif field not in (BUNDLE_REFS if kind == "bundle" else WORKSPACE_REFS):
                        examples = "name or target" if kind == "bundle" else "file_path or current_user.userName"
                        self.add(doc, ERROR, path, f"${{{ref}}} refers to {kind}.{field}, which isn't a documented "
                                                   f"field of {kind} (such as {examples}).")
                    continue
                res = _RESOURCE_REF.match(ref)
                if res and res.group(2) not in resources.get(res.group(1), set()):
                    self.add(doc, ERROR, path, f"${{{ref}}} refers to resources.{res.group(1)}.{res.group(2)}, "
                                               "which the bundle doesn't define.")

    def mapping_keys(self, doc: _Doc, where: str, value: Any, allowed: frozenset[str], what: str = "key") -> bool:
        """Warn about keys outside ``allowed``. False when ``value`` isn't a mapping (an error)."""
        if not isinstance(value, dict):
            self.add(doc, ERROR, where, f"{where.rsplit('.', 1)[-1]} is a mapping of settings.")
            return False
        for key in value:
            if str(key) not in allowed:
                self.add(doc, WARNING, f"{where}.{key}", f"Unknown {what} {key!r}.")
        return True


def validate(folder: str | os.PathLike[str]) -> list[Finding]:
    """Every problem found in the bundle in ``folder`` (databricks.yml and its includes), on this PC
    only. An empty list means none of these checks found anything."""
    root = Path(folder)
    checker = _Checker(root)
    main_path = next((root / n for n in ("databricks.yml", "databricks.yaml") if (root / n).is_file()), None)
    if main_path is None:
        checker.add("databricks.yml", ERROR, "", "The folder has no databricks.yml.")
        return checker.findings
    main = checker.load(main_path)
    if main is None:
        return checker.findings
    docs = [main, *checker.includes(main)]
    checker.run(docs)
    checker.findings.sort(key=lambda f: (f.file != main.name, f.file, f.line or 0, f.path))
    return checker.findings


# ---------------------------------------------------------------- helpers


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:      # another drive on Windows
        return path.as_posix()


def _join(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _record_lines(node: yaml.Node, path: str, lines: dict[str, int]) -> None:
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
                continue
            child = _join(path, key.value)
            lines.setdefault(child, key.start_mark.line + 1)
            _record_lines(value, child, lines)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            child = f"{path}[{i}]"
            lines.setdefault(child, item.start_mark.line + 1)
            _record_lines(item, child, lines)


def _duplicate_keys(node: yaml.Node, path: str) -> Iterator[tuple[str, int, int]]:
    """(path, first line, repeated line) for each key given twice in one mapping."""
    if isinstance(node, yaml.MappingNode):
        seen: dict[str, int] = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
                continue
            child = _join(path, key.value)
            line = key.start_mark.line + 1
            if key.value in seen:
                yield child, seen[key.value], line
            else:
                seen[key.value] = line
            yield from _duplicate_keys(value, child)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            yield from _duplicate_keys(item, f"{path}[{i}]")


def _not_local(value: str) -> bool:
    """Workspace paths (a leading slash), URIs (dbfs:/, s3://) and ${...} substitutions aren't on this PC."""
    return value.startswith(("/", "\\")) or "${" in value or bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value))


def _describe(value: Any) -> str:
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number with a fraction"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__


def _line_of(lines: dict[str, int], path: str) -> int | None:
    """The line of ``path``, or of its nearest parent that has one."""
    while path:
        if path in lines:
            return lines[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return None


def _scopes(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """The top level, then each target, as (path prefix, mapping)."""
    yield "", data
    targets = data.get("targets")
    if isinstance(targets, dict):
        for name, spec in targets.items():
            if isinstance(spec, dict):
                yield f"targets.{name}.", spec


def _strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, _join(path, str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
