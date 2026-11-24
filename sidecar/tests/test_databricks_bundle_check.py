"""The local bundle check (engines/databricks_bundle_check.py): good bundles taken from the Databricks
documentation's own examples pass, and each check finds its problem at the exact field path. Nothing
here runs the Databricks CLI or calls the network.

Doc sources for the examples (docs.databricks.com/aws/en/dev-tools/bundles/...): "settings" (the
hello-bundle split into databricks.yml and hello-job.yml, and the dev/prod targets example), "variables"
(the hello-bundle with workspace.root_path) and "job-task-types" (the dashboard task as shown). RUN_JOB_EXAMPLE
is built from job-task-types' run_job_task example (the jobs my-first-job and my_second_job, a notebook at
./src/test.py and job_id: ${resources.jobs.my-first-job.id}); its cluster values are this file's own."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fabric_desktop import cli
from fabric_desktop.engines.databricks_bundle_check import ERROR, WARNING, Finding, validate


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def found(findings: list[Finding], severity: str | None = None) -> set[str]:
    return {f.path for f in findings if severity is None or f.severity == severity}


def one(findings: list[Finding], path: str) -> Finding:
    hits = [f for f in findings if f.path == path]
    assert len(hits) == 1, (path, findings)
    return hits[0]


# ---------------------------------------------------------------- the documentation's examples

SETTINGS_MAIN = """# databricks.yml

bundle:
  name: hello-bundle

include:
  - '*.yml'
"""
SETTINGS_JOB = """# hello-job.yml

resources:
  jobs:
    hello-job:
      name: hello-job
      tasks:
        - task_key: hello-task
          existing_cluster_id: 1234-567890-abcde123
          notebook_task:
            notebook_path: ./hello.py
"""
TARGETS_EXAMPLE = """bundle:
  name: hello-bundle

resources:
  jobs:
    hello-job:
      name: hello-job
      tasks:
        - task_key: hello-task
          existing_cluster_id: 1234-567890-abcde123
          notebook_task:
            notebook_path: ./hello.py

targets:
  dev:
    default: true
  prod:
    workspace:
      host: https://<production-workspace-url>
    resources:
      jobs:
        hello-job:
          name: hello-job
          tasks:
            - task_key: hello-task
              existing_cluster_id: 2345-678901-fabcd456
"""
VARIABLES_EXAMPLE = """bundle:
  name: hello-bundle

workspace:
  root_path: /Workspace/Users/${workspace.current_user.userName}/.bundle/${bundle.name}/my-envs/${bundle.target}

targets:
  dev:
    default: true
"""
RUN_JOB_EXAMPLE = """bundle:
  name: run-job-bundle

resources:
  jobs:
    my-first-job:
      name: my-first-job
      tasks:
        - task_key: my-first-job-task
          new_cluster:
            spark_version: 13.3.x-scala2.12
            node_type_id: i3.xlarge
            num_workers: 2
          notebook_task:
            notebook_path: ./src/test.py
    my_second_job:
      name: my-second-job
      tasks:
        - task_key: my-second-job-task
          run_job_task:
            job_id: ${resources.jobs.my-first-job.id}
    my-dashboard-job:
      name: my-dashboard-job
      tasks:
        - task_key: my-dashboard-task
          dashboard_task:
            dashboard_id: 11111111-1111-1111-1111-111111111111
"""


def test_settings_split_example_is_clean(tmp_path):
    write(tmp_path, {"databricks.yml": SETTINGS_MAIN, "hello-job.yml": SETTINGS_JOB, "hello.py": "print(1)\n"})
    assert validate(tmp_path) == []


def test_targets_example_is_clean_and_a_target_task_may_leave_out_its_type(tmp_path):
    write(tmp_path, {"databricks.yml": TARGETS_EXAMPLE, "hello.py": "print(1)\n"})
    assert validate(tmp_path) == []


def test_variables_example_is_clean(tmp_path):
    write(tmp_path, {"databricks.yml": VARIABLES_EXAMPLE})
    assert validate(tmp_path) == []


def test_run_job_and_dashboard_examples_are_clean(tmp_path):
    write(tmp_path, {"databricks.yml": RUN_JOB_EXAMPLE, "src/test.py": "print(1)\n"})
    assert validate(tmp_path) == []


def test_databricks_yaml_spelling_and_a_notebook_without_extension(tmp_path):
    write(tmp_path, {"databricks.yaml": SETTINGS_JOB.replace("./hello.py", "./hello") + "bundle:\n  name: x\n",
                     "hello.ipynb": "{}"})
    assert validate(tmp_path) == []


# ---------------------------------------------------------------- reading the files


def test_no_databricks_yml(tmp_path):
    [f] = validate(tmp_path)
    assert (f.severity, f.path, f.file) == (ERROR, "", "databricks.yml")


def test_yaml_parse_error_has_line_and_column(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs: [a, b\n"})
    [f] = validate(tmp_path)
    assert f.severity == ERROR and f.file == "databricks.yml" and f.path == ""
    assert f.line == 5 and f.column == 1
    assert "can't be read" in f.message


def test_yaml_parse_error_in_an_included_file(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - resources/*.yml\n",
                     "resources/bad.yml": "resources:\n  jobs:\n    a:\n   b: 1\n"})
    [f] = validate(tmp_path)
    assert (f.file, f.severity) == ("resources/bad.yml", ERROR)
    assert f.line == 4 and f.column is not None


def test_a_file_that_isnt_a_mapping(tmp_path):
    write(tmp_path, {"databricks.yml": "- just\n- a list\n"})
    [f] = validate(tmp_path)
    assert (f.severity, f.path, f.line) == (ERROR, "", 1)


# ---------------------------------------------------------------- unknown keys


def test_unknown_keys_at_every_checked_level(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
  nam: typo
resource: {}
variables:
  env:
    defualt: dev
targets:
  dev:
    mod: development
    bundle:
      colour: blue
resources:
  jobs:
    nightly:
      name: nightly
      shedule: {}
      tasks:
        - task_key: a
          notebook_tsk: {}
          spark_jar_task: {main_class_name: M}
  pipelines:
    p:
      name: p
      catlog: main
  widgets:
    w: {}
"""})
    findings = validate(tmp_path)
    assert found(findings, WARNING) == {
        "bundle.nam", "resource", "variables.env.defualt", "targets.dev.mod", "targets.dev.bundle.colour",
        "resources.jobs.nightly.shedule", "resources.jobs.nightly.tasks[0].notebook_tsk",
        "resources.pipelines.p.catlog", "resources.widgets"}
    assert found(findings, ERROR) == set()
    assert one(findings, "resources.jobs.nightly.tasks[0].notebook_tsk").line == 20
    assert one(findings, "resource").line == 4


def test_other_resource_types_are_not_key_checked(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nresources:\n  schemas:\n    s:\n      anything: 1\n"})
    assert validate(tmp_path) == []


# ---------------------------------------------------------------- required fields and tasks


def test_bundle_name_required(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  databricks_cli_version: '>=0.218'\n"})
    assert found(validate(tmp_path), ERROR) == {"bundle.name"}


def test_bundle_mapping_required(tmp_path):
    write(tmp_path, {"databricks.yml": "targets:\n  dev: {}\n"})
    f = one(validate(tmp_path), "bundle")
    assert (f.severity, f.line) == (ERROR, 1)


def test_bundle_name_from_an_included_file_counts(tmp_path):
    write(tmp_path, {"databricks.yml": "include:\n  - bundle.yml\n", "bundle.yml": "bundle:\n  name: x\n"})
    assert validate(tmp_path) == []


JOB = """bundle:
  name: x
resources:
  jobs:
    nightly:
      name: nightly
      tasks:
{tasks}"""


def job(tmp_path: Path, tasks: str) -> list[Finding]:
    write(tmp_path, {"databricks.yml": JOB.format(tasks=tasks)})
    return validate(tmp_path)


def test_task_key_required(tmp_path):
    findings = job(tmp_path, "        - notebook_task: {notebook_path: /Workspace/a}\n"
                             "        - task_key: ''\n          notebook_task: {notebook_path: /Workspace/b}\n")
    assert found(findings, ERROR) == {"resources.jobs.nightly.tasks[0]", "resources.jobs.nightly.tasks[1].task_key"}
    assert one(findings, "resources.jobs.nightly.tasks[0]").line == 8


def test_exactly_one_task_type(tmp_path):
    findings = job(tmp_path, "        - task_key: none\n          existing_cluster_id: c\n"
                             "        - task_key: two\n          notebook_task: {notebook_path: /a}\n"
                             "          spark_python_task: {python_file: /b.py}\n")
    assert found(findings, ERROR) == {"resources.jobs.nightly.tasks[0]", "resources.jobs.nightly.tasks[1]"}
    assert "notebook_task, spark_python_task" in one(findings, "resources.jobs.nightly.tasks[1]").message


def test_duplicate_task_key(tmp_path):
    findings = job(tmp_path, "        - task_key: a\n          notebook_task: {notebook_path: /a}\n"
                             "        - task_key: b\n          notebook_task: {notebook_path: /b}\n"
                             "        - task_key: a\n          notebook_task: {notebook_path: /c}\n")
    f = one(findings, "resources.jobs.nightly.tasks[2].task_key")
    assert f.severity == ERROR and "tasks[0]" in f.message and f.line == 12
    assert len(findings) == 1


def test_depends_on_unknown_task_key(tmp_path):
    findings = job(tmp_path, "        - task_key: a\n          notebook_task: {notebook_path: /a}\n"
                             "        - task_key: b\n          notebook_task: {notebook_path: /b}\n"
                             "          depends_on:\n            - task_key: a\n            - task_key: c\n")
    assert found(findings) == {"resources.jobs.nightly.tasks[1].depends_on[1].task_key"}
    assert one(findings, "resources.jobs.nightly.tasks[1].depends_on[1].task_key").line == 14


def test_for_each_inner_task_is_checked(tmp_path):
    findings = job(tmp_path, "        - task_key: loop\n          for_each_task:\n            inputs: '[1,2]'\n"
                             "            task:\n              task_key: inner\n              colour: red\n")
    assert found(findings, WARNING) == {"resources.jobs.nightly.tasks[0].for_each_task.task.colour"}
    assert found(findings, ERROR) == {"resources.jobs.nightly.tasks[0].for_each_task.task"}


def test_target_task_with_new_key_needs_a_type_and_depends_on_sees_the_job(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  jobs:
    nightly:
      name: nightly
      tasks:
        - task_key: a
          notebook_task: {notebook_path: /a}
targets:
  prod:
    resources:
      jobs:
        nightly:
          tasks:
            - task_key: a
              existing_cluster_id: c
            - task_key: extra
              depends_on: [{task_key: a}, {task_key: gone}]
"""})
    assert found(validate(tmp_path), ERROR) == {"targets.prod.resources.jobs.nightly.tasks[1]",
                                                "targets.prod.resources.jobs.nightly.tasks[1].depends_on[1].task_key"}


# ---------------------------------------------------------------- references


def test_undefined_variable_reference(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
variables:
  catalog:
    default: main
resources:
  pipelines:
    p:
      name: p
      catalog: ${var.catalog}
      schema: ${var.schema}
      configuration:
        cluster: ${var.cluster.node_type}
"""})
    findings = validate(tmp_path)
    assert found(findings, ERROR) == {"resources.pipelines.p.schema", "resources.pipelines.p.configuration.cluster"}
    assert one(findings, "resources.pipelines.p.schema").line == 11


def test_variable_declared_in_an_included_file(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude: [vars.yml]\nworkspace:\n  root_path: /a/${var.env}\n",
                     "vars.yml": "variables:\n  env: {default: dev}\n"})
    assert validate(tmp_path) == []


def test_undefined_resource_reference(tmp_path):
    findings = job(tmp_path, "        - task_key: a\n          run_job_task:\n            job_id: ${resources.jobs.missing.id}\n"
                             "        - task_key: b\n          run_job_task:\n            job_id: ${resources.jobs.nightly.id}\n")
    f = one(findings, "resources.jobs.nightly.tasks[0].run_job_task.job_id")
    assert f.severity == ERROR and "resources.jobs.missing" in f.message
    assert len(findings) == 1


def test_resource_defined_only_in_a_target_counts(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
targets:
  dev:
    resources:
      jobs:
        side:
          name: side
          tasks: [{task_key: a, run_job_task: {job_id: "${resources.jobs.side.id}"}}]
"""})
    assert validate(tmp_path) == []


def test_target_sets_an_undeclared_variable(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nvariables:\n  a: {}\ntargets:\n  dev:\n"
                                       "    variables:\n      a: 1\n      b: 2\n"})
    assert found(validate(tmp_path), ERROR) == {"targets.dev.variables.b"}


def test_variable_type_and_complex_default(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nvariables:\n  a:\n    type: string\n"
                                       "  b:\n    type: complex\n    default: one\n  c:\n    type: complex\n"
                                       "    default: {k: v}\n"})
    assert found(validate(tmp_path), ERROR) == {"variables.a.type", "variables.b.default"}


# ---------------------------------------------------------------- targets


def test_target_mode_values(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ntargets:\n  dev:\n    mode: development\n"
                                       "  prod:\n    mode: production\n  qa:\n    mode: staging\n"})
    f = one(validate(tmp_path), "targets.qa.mode")
    assert (f.severity, f.line) == (ERROR, 9)


def test_at_most_one_default_target(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ntargets:\n  dev:\n    default: true\n"
                                       "  test:\n    default: false\n  prod:\n    default: true\n"})
    assert found(validate(tmp_path), ERROR) == {"targets.dev.default", "targets.prod.default"}


def test_default_targets_across_files(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude: [t.yml]\ntargets:\n  dev:\n    default: true\n",
                     "t.yml": "targets:\n  prod:\n    default: true\n"})
    findings = validate(tmp_path)
    assert {(f.file, f.path) for f in findings} == {("databricks.yml", "targets.dev.default"),
                                                    ("t.yml", "targets.prod.default")}


def test_default_must_be_a_boolean(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ntargets:\n  dev:\n    default: 'yes'\n"})
    assert found(validate(tmp_path), ERROR) == {"targets.dev.default"}


# ---------------------------------------------------------------- includes


def test_include_that_matches_nothing(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - resources/*.yml\n  - other.yml\n",
                     "other.yml": "{}\n"})
    f = one(validate(tmp_path), "include[0]")
    assert (f.severity, f.file, f.line) == (ERROR, "databricks.yml", 4)


def test_include_that_isnt_a_list(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude: a.yml\n"})
    assert found(validate(tmp_path), ERROR) == {"include"}


def test_recursive_include_glob(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude: ['resources/**/*.yml']\n",
                     "resources/deep/j.yml": "resources:\n  jobs:\n    j:\n      nme: j\n"})
    f = one(validate(tmp_path), "resources.jobs.j.nme")
    assert (f.severity, f.file, f.line) == (WARNING, "resources/deep/j.yml", 4)


# ---------------------------------------------------------------- local files


def test_missing_notebook_relative_to_the_including_file(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude: [resources/*.yml]\n",
                     "resources/job.yml": """resources:
  jobs:
    nightly:
      name: nightly
      tasks:
        - task_key: a
          notebook_task: {notebook_path: ../src/a.py}
        - task_key: b
          notebook_task: {notebook_path: ./src/b.py}
        - task_key: c
          spark_python_task: {python_file: ../src/missing.py}
        - task_key: d
          sql_task: {file: {path: ../src/q.sql}, warehouse_id: w}
        - task_key: ws
          notebook_task: {notebook_path: /Workspace/Shared/x}
        - task_key: sub
          notebook_task: {notebook_path: "${workspace.file_path}/y"}
""",
                     "src/a.py": "", "src/b.py": ""})
    findings = validate(tmp_path)
    assert found(findings, ERROR) == {"resources.jobs.nightly.tasks[1].notebook_task.notebook_path",
                                      "resources.jobs.nightly.tasks[2].spark_python_task.python_file",
                                      "resources.jobs.nightly.tasks[3].sql_task.file.path"}
    f = one(findings, "resources.jobs.nightly.tasks[1].notebook_task.notebook_path")
    assert f.file == "resources/job.yml" and f.line == 9 and "resources/src/b.py" in f.message


def test_paths_in_a_git_source_are_not_looked_for(tmp_path):
    findings = job(tmp_path, "        - task_key: a\n          notebook_task: {notebook_path: nb/a, source: GIT}\n")
    assert findings == []
    write(tmp_path, {"databricks.yml": JOB.format(tasks="        - task_key: a\n          notebook_task: {notebook_path: nb/a}\n")
                     + "      git_source: {git_url: 'https://example.com/r', git_provider: gitHub, git_branch: main}\n"})
    assert validate(tmp_path) == []


def test_pipeline_library_paths(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  pipelines:
    p:
      name: p
      libraries:
        - notebook: {path: ./dlt/one}
        - file: {path: ./dlt/two.py}
        - glob: {include: ./dlt/**}
""", "dlt/one.sql": ""})
    assert found(validate(tmp_path)) == {"resources.pipelines.p.libraries[1].file.path"}


# ---------------------------------------------------------------- more resource types' keys

OTHER_RESOURCES = """bundle:
  name: x
resources:
  experiments:
    exp:
      name: /Workspace/Users/someone@example.com/exp
      artifact_location: dbfs:/exp
      tags: [{key: team, value: data}]
      permissions: [{level: CAN_READ, group_name: users}]
  models:
    m:
      name: my-model
      description: a model
      tags: [{key: team, value: data}]
  sql_warehouses:
    wh:
      name: wh
      cluster_size: 2X-Small
      max_num_clusters: 1
      auto_stop_mins: 10
      enable_serverless_compute: true
  apps:
    app:
      name: my-app
      source_code_path: ./app
      description: an app
  alerts:
    a:
      display_name: a
      query_text: select 1
      warehouse_id: abc
      evaluation: {}
  catalogs:
    c:
      name: c
      comment: a catalog
      grants: []
"""


def test_other_resource_types_with_documented_keys_are_clean(tmp_path):
    write(tmp_path, {"databricks.yml": OTHER_RESOURCES})
    assert validate(tmp_path) == []


def test_unknown_keys_in_other_resource_types(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  experiments:
    exp:
      name: e
      artifact_locaton: dbfs:/exp
  models:
    m:
      name: m
      descripton: typo
  sql_warehouses:
    wh:
      name: wh
      clustr_size: Small
  apps:
    app:
      name: a
      source_path: ./app
  alerts:
    a:
      display_name: a
      query: select 1
  catalogs:
    c:
      name: c
      permissions: []
"""})
    findings = validate(tmp_path)
    expected = {"resources.experiments.exp.artifact_locaton": 7, "resources.models.m.descripton": 11,
                "resources.sql_warehouses.wh.clustr_size": 15, "resources.apps.app.source_path": 19,
                "resources.alerts.a.query": 23, "resources.catalogs.c.permissions": 27}
    assert {f.path: f.line for f in findings} == expected
    assert {f.severity for f in findings} == {WARNING}


# ---------------------------------------------------------------- duplicate keys


def test_a_key_given_twice_is_an_error_with_both_lines(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  jobs:
    nightly:
      name: nightly
      tasks:
        - task_key: a
          notebook_task: {notebook_path: /a}
          task_key: b
      name: again
"""})
    findings = validate(tmp_path)
    assert found(findings) == {"resources.jobs.nightly.name", "resources.jobs.nightly.tasks[0].task_key"}
    f = one(findings, "resources.jobs.nightly.name")
    assert (f.severity, f.line, f.file) == (ERROR, 11, "databricks.yml")
    assert "lines 6 and 11" in f.message
    g = one(findings, "resources.jobs.nightly.tasks[0].task_key")
    assert (g.severity, g.line) == (ERROR, 10) and "lines 8 and 10" in g.message


def test_the_same_key_in_different_mappings_is_fine(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  jobs:
    a:
      name: a
    b:
      name: b
targets:
  dev:
    default: true
  prod:
    default: false
"""})
    assert validate(tmp_path) == []


def test_a_key_given_twice_in_an_included_file(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - more.yml\n",
                     "more.yml": "variables:\n  env:\n    default: dev\n  env:\n    default: prod\n"})
    [f] = validate(tmp_path)
    assert (f.severity, f.path, f.file, f.line) == (ERROR, "variables.env", "more.yml", 4)


# ---------------------------------------------------------------- ${bundle.*} and ${workspace.*}


def test_documented_bundle_and_workspace_references_are_clean(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
workspace:
  host: https://example.cloud.databricks.com
resources:
  jobs:
    nightly:
      name: ${bundle.name}-${bundle.target}
      description: ${workspace.host} ${workspace.current_user.short_name} ${workspace.current_user.domain_friendly_name}
      tasks:
        - task_key: a
          notebook_task:
            notebook_path: ${workspace.file_path}/a
            base_parameters: {root: '${workspace.root_path}', git: '${bundle.git.branch}'}
"""})
    assert validate(tmp_path) == []


def test_undocumented_bundle_and_workspace_references(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
resources:
  jobs:
    nightly:
      name: ${bundle.nme}
      description: ${workspace.currentUser.userName}
      tags:
        env: ${bundle.environment}
"""})
    findings = validate(tmp_path)
    assert {(f.path, f.severity, f.line) for f in findings} == {
        ("resources.jobs.nightly.name", ERROR, 6),
        ("resources.jobs.nightly.description", ERROR, 7),
        ("resources.jobs.nightly.tags.env", WARNING, 9)}
    assert "bundle.nme" in one(findings, "resources.jobs.nightly.name").message


# ---------------------------------------------------------------- field types


TYPED = """bundle:
  name: x
variables:
  tasks_list:
    type: complex
    default: []
resources:
  jobs:
    nightly:
      name: nightly
      max_concurrent_runs: {runs}
      timeout_seconds: {timeout}
      email_notifications: {email}
      tasks:
        - task_key: a
          notebook_task: {{notebook_path: /a}}
          depends_on: {deps}
          libraries: {libs}
          timeout_seconds: 60
          max_retries: {retries}
    other:
      name: other
      tasks: {tasks}
  pipelines:
    p:
      name: p
      libraries: {plibs}
      configuration: {conf}
"""


def test_documented_field_types_are_clean(tmp_path):
    write(tmp_path, {"databricks.yml": TYPED.format(
        runs=1, timeout=3600, email="{on_failure: [a@example.com]}", deps="[]", libs="[{whl: /a.whl}]",
        retries=2, tasks="${var.tasks_list}", plibs="[]", conf="{a: b}")})
    assert validate(tmp_path) == []


def test_wrong_field_types_name_the_path_and_the_type(tmp_path):
    write(tmp_path, {"databricks.yml": TYPED.format(
        runs="'5'", timeout=1.5, email="[a@example.com]", deps="a", libs="{whl: /a.whl}",
        retries="true", tasks="{a: 1}", plibs="{notebook: {path: /a}}", conf="[a]")})
    findings = validate(tmp_path)
    assert {(f.path, f.severity, f.line) for f in findings} == {
        ("resources.jobs.nightly.max_concurrent_runs", ERROR, 11),
        ("resources.jobs.nightly.timeout_seconds", ERROR, 12),
        ("resources.jobs.nightly.email_notifications", ERROR, 13),
        ("resources.jobs.nightly.tasks[0].depends_on", ERROR, 17),
        ("resources.jobs.nightly.tasks[0].libraries", ERROR, 18),
        ("resources.jobs.nightly.tasks[0].max_retries", ERROR, 20),
        ("resources.jobs.other.tasks", ERROR, 23),
        ("resources.pipelines.p.libraries", ERROR, 27),
        ("resources.pipelines.p.configuration", ERROR, 28)}
    assert one(findings, "resources.jobs.nightly.max_concurrent_runs").message == \
        "max_concurrent_runs is an integer, not a string."
    assert one(findings, "resources.jobs.nightly.email_notifications").message == \
        "email_notifications is a mapping, not a list."


# ---------------------------------------------------------------- sync and artifacts


def test_sync_lists_and_artifact_folders_that_exist_are_clean(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
sync:
  include: ['dist/*.whl']
  exclude: [scratch/**]
artifacts:
  wheel:
    type: whl
    path: ./pkg
  remote:
    path: /Workspace/Shared/pkg
targets:
  dev:
    sync:
      exclude: []
""", "pkg/setup.py": ""})
    assert validate(tmp_path) == []


def test_sync_entries_must_be_lists_of_strings(tmp_path):
    write(tmp_path, {"databricks.yml": """bundle:
  name: x
sync:
  include: dist/*.whl
  exclude:
    - scratch
    - 7
targets:
  dev:
    sync:
      include: {a: b}
"""})
    findings = validate(tmp_path)
    assert {(f.path, f.severity, f.line) for f in findings} == {
        ("sync.include", ERROR, 4), ("sync.exclude[1]", ERROR, 7), ("targets.dev.sync.include", ERROR, 11)}


def test_relative_artifact_path_must_be_a_folder(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - conf/*.yml\n",
                     "conf/artifacts.yml": "artifacts:\n  wheel:\n    path: ../pkg\n  other:\n    path: ./setup.py\n",
                     "conf/setup.py": ""})
    findings = validate(tmp_path)
    assert {(f.path, f.severity, f.file, f.line) for f in findings} == {
        ("artifacts.wheel.path", ERROR, "conf/artifacts.yml", 3),
        ("artifacts.other.path", ERROR, "conf/artifacts.yml", 5)}
    assert "no folder at pkg" in one(findings, "artifacts.wheel.path").message


# ---------------------------------------------------------------- the command line


def test_cli_prints_findings_and_exits_1_on_errors(tmp_path, capsys):
    write(tmp_path, {"databricks.yml": "bundle:\n  nme: x\n"})
    with pytest.raises(SystemExit) as done:
        cli.main(["databricks-bundle", "validate", str(tmp_path)])
    assert done.value.code == 1
    out = capsys.readouterr().out
    assert "databricks.yml:2:1: warning: bundle.nme: " in out
    assert ": error: bundle.name: " in out
    assert out.splitlines()[-1] == "1 error, 1 warning (checked on this PC; not databricks bundle validate)"


@pytest.mark.parametrize(("yml", "code", "summary"), [
    ("bundle:\n  name: x\ninclude:\n  - nope.yml\n", 1, "1 error, 0 warnings"),
    ("bundle:\n  nme: x\ninclude:\n  - nope.yml\n", 1, "2 errors, 1 warning"),
    ("bundle:\n  name: x\n  nme: y\n", 0, "0 errors, 1 warning"),
    ("bundle:\n  name: x\n  nme: y\n  nm: z\n", 0, "0 errors, 2 warnings"),
])
def test_cli_summary_is_pluralized(tmp_path, capsys, yml, code, summary):
    write(tmp_path, {"databricks.yml": yml})
    with pytest.raises(SystemExit) as done:
        cli.main(["databricks-bundle", "validate", str(tmp_path)])
    assert done.value.code == code
    assert capsys.readouterr().out.splitlines()[-1] == f"{summary} (checked on this PC; not databricks bundle validate)"


def test_docs_example_output_matches_a_real_run(tmp_path, capsys):
    """docs/engines.md's example: its finding and summary lines, with this run's folder for C:\\repos\\sales-etl."""
    tasks = "".join(f"      - task_key: t{i}\n        notebook_task:\n          notebook_path: ../src/t{i}.py\n"
                    for i in range(2))
    write(tmp_path, {"databricks.yml": "bundle:\n  name: sales-etl\ninclude:\n  - resources/*.yml\n",
                     "resources/nightly.yml": "# The nightly load.\nresources:\n  jobs:\n    nightly:\n      tasks:\n" + tasks
                     + "      - task_key: load\n        notebook_task:\n          notebook_path: ../src/load.py\n",
                     "src/t0.py": "", "src/t1.py": ""})
    with pytest.raises(SystemExit) as done:
        cli.main(["databricks-bundle", "validate", str(tmp_path)])
    assert done.value.code == 1
    got = capsys.readouterr().out.replace(str(tmp_path.resolve() / "resources") + os.sep, "").splitlines()
    docs = (Path(__file__).resolve().parents[2] / "docs" / "engines.md").read_text(encoding="utf-8")
    block = docs.split("> fabric-desktop databricks-bundle validate C:\\repos\\sales-etl\n", 1)[1].split("```", 1)[0]
    want = block.replace("C:\\repos\\sales-etl\\resources\\", "").splitlines()
    assert got == want


def test_cli_json_and_clean_exit(tmp_path, capsys):
    write(tmp_path, {"databricks.yml": VARIABLES_EXAMPLE})
    with pytest.raises(SystemExit) as done:
        cli.main(["databricks-bundle", "validate", str(tmp_path), "--json"])
    assert done.value.code == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


def test_cli_missing_folder(tmp_path):
    with pytest.raises(SystemExit) as done:
        cli.main(["databricks-bundle", "validate", str(tmp_path / "nope")])
    assert done.value.code == 2


def test_the_check_uses_no_network_or_subprocess(tmp_path, monkeypatch):
    import socket
    import subprocess

    def refuse(*args, **kwargs):
        raise AssertionError("the local bundle check must not call out")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    write(tmp_path, {"databricks.yml": RUN_JOB_EXAMPLE, "src/test.py": ""})
    assert validate(tmp_path) == []


# ---------------------------------------------------------------- bundle includes stay inside the folder; YAML aliases refused


@pytest.mark.parametrize("pattern", ["C:/x/*.yml", "c:x.yml", "\\\\host\\share\\x.yml", "\\x.yml", "/x/*.yml",
                                     "../x.yml", "resources/../../x.yml", "resources\\..\\x.yml"])
def test_an_include_that_leaves_the_folder_is_refused_before_globbing(tmp_path, monkeypatch, pattern):
    import glob as glob_module

    def refuse(*args, **kwargs):
        raise AssertionError("a refused include must not be globbed")

    monkeypatch.setattr(glob_module, "iglob", refuse)
    write(tmp_path, {"databricks.yml": f"bundle:\n  name: x\ninclude:\n  - '{pattern}'\n"})
    finding = one(validate(tmp_path), "include[0]")
    assert finding.severity == ERROR and finding.line == 4
    assert finding.message == (f"The include {pattern!r} leads outside the bundle folder. An include must stay "
                               "inside the bundle folder, relative to databricks.yml.")


def test_an_include_match_outside_the_folder_through_a_link_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    write(outside, {"evil.yml": "resources:\n  bogus: {}\n"})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - res/*.yml\n"})
    try:
        (bundle / "res").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    findings = validate(bundle)
    assert [(f.path, f.file) for f in findings] == [("include[0]", "databricks.yml")]
    assert findings[0].message == ("The include 'res/*.yml' goes through 1 link, which isn't followed. "
                                   "An include must stay inside the bundle folder.")


def test_a_recursive_include_never_walks_into_a_linked_folder(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    write(outside, {"deep/evil.yml": "resources:\n  bogus: {}\n"})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - '**/*.yml'\n",
                                         "res/a.yml": "resources: {}\n"})
    try:
        (bundle / "res" / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    listed = []
    real_scandir = os.scandir

    def spy(path):
        listed.append(os.path.realpath(path))
        return real_scandir(path)
    monkeypatch.setattr(os, "scandir", spy)
    findings = validate(bundle)
    assert not any(p.startswith(os.path.realpath(outside)) for p in listed), listed
    assert [f.message for f in findings] == [("The include '**/*.yml' goes through 1 link, which isn't "
                                             "followed. An include must stay inside the bundle folder.")]


def test_a_file_link_outside_the_folder_is_never_followed(tmp_path, monkeypatch):
    write(tmp_path / "outside", {"evil.yml": "resources: {}\n"})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - res/*.yml\n",
                                         "res/ok.yml": "resources: {}\n"})
    try:
        (bundle / "res" / "evil.yml").symlink_to(tmp_path / "outside" / "evil.yml")
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    stats = []
    real_stat = os.stat

    def spy(path, *args, **kwargs):
        stats.append(os.path.realpath(path))
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, "stat", spy)
    [finding] = validate(bundle)
    monkeypatch.undo()
    assert not any(p.startswith(os.path.realpath(tmp_path / "outside")) for p in stats), stats
    assert finding.message == ("The include 'res/*.yml' goes through 1 link, which isn't followed. "
                               "An include must stay inside the bundle folder.")


def test_two_links_read_in_the_plural(tmp_path):
    write(tmp_path / "outside", {"a.yml": "resources: {}\n", "b.yml": "resources: {}\n"})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - res/*.yml\n",
                                         "res/ok.yml": "resources: {}\n"})
    try:
        for name in ("a", "b"):
            (bundle / "res" / f"{name}.yml").symlink_to(tmp_path / "outside" / f"{name}.yml")
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    [finding] = validate(bundle)
    assert finding.message == ("The include 'res/*.yml' goes through 2 links, which aren't followed. "
                               "An include must stay inside the bundle folder.")


def test_a_run_of_double_stars_walks_each_folder_once(tmp_path, monkeypatch):
    from fabric_desktop.engines.databricks_bundle_check import _walk_glob
    write(tmp_path, {"databricks.yml": "x: 1\n", "res/a.yml": "", "res/sub/c.yml": "", "res/sub/deep/d.yml": ""})
    listed = []
    real_scandir = os.scandir

    def spy(path):
        listed.append(path)
        return real_scandir(path)
    monkeypatch.setattr(os, "scandir", spy)
    once, _ = _walk_glob(tmp_path, "**/*.yml", 100)
    single = len(listed)
    listed.clear()
    many, linked = _walk_glob(tmp_path, "**/**/**/**/*.yml", 100)
    assert sorted(many) == sorted(once) and linked == 0
    assert len(listed) == single


@pytest.mark.parametrize("pattern,want", [
    ("res/*.yml", ["res/a.yml", "res/b.yml"]), ("**/*.yml", ["databricks.yml", "res/a.yml", "res/b.yml",
                                                            "res/sub/c.yml"]),
    ("res/**", ["res/a.yml", "res/b.yml", "res/sub/c.yml"]), ("res/?.yml", ["res/a.yml", "res/b.yml"]),
    ("res/[a].yml", ["res/a.yml"]), ("res\\sub\\*.yml", ["res/sub/c.yml"]), ("res/*", ["res/a.yml", "res/b.yml"]),
])
def test_the_include_walk_matches_as_glob_does(tmp_path, pattern, want):
    from fabric_desktop.engines.databricks_bundle_check import _walk_glob
    write(tmp_path, {"databricks.yml": "x: 1\n", "res/a.yml": "", "res/b.yml": "", "res/sub/c.yml": "",
                     "res/.hidden.yml": ""})
    files, linked = _walk_glob(tmp_path, pattern, 100)
    assert sorted(Path(f).relative_to(tmp_path).as_posix() for f in files) == want and linked == 0


def test_an_include_reads_at_most_the_cap(tmp_path, monkeypatch):
    from fabric_desktop.engines import databricks_bundle_check as check

    monkeypatch.setattr(check, "MAX_INCLUDE_FILES", 3)
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - res/*.yml\n",
                     **{f"res/{i}.yml": "resources: {}\n" for i in range(5)}})
    assert [f.message for f in validate(tmp_path)] == [
        "The include 'res/*.yml' matches more than 3 files, so only 3 are read."]


@pytest.mark.parametrize("value", ["../outside.py", "src/../../outside.py"])
def test_a_local_path_outside_the_folder_is_a_finding_and_not_looked_for(tmp_path, value):
    bundle = tmp_path / "bundle"
    write(tmp_path, {"outside.py": ""})
    write(bundle, {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs:\n    j:\n      tasks:\n"
                                     f"        - task_key: a\n          spark_python_task:\n            python_file: {value}\n"
                                     f"artifacts:\n  w:\n    path: {value}\n"})
    findings = validate(bundle)
    task = one(findings, "resources.jobs.j.tasks[0].spark_python_task.python_file")
    assert task.message == (f"{value!r} leads outside the bundle folder (../outside.py, relative to databricks.yml). "
                            "A local path must stay inside the bundle folder.")
    art = one(findings, "artifacts.w.path")
    assert art.message.startswith(f"{value!r} leads outside the bundle folder")


def test_a_relative_path_that_climbs_but_stays_inside_is_still_checked(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - resources/*.yml\n",
                     "resources/job.yml": "resources:\n  jobs:\n    j:\n      tasks:\n        - task_key: a\n"
                                          "          notebook_task:\n            notebook_path: ../src/nb\n",
                     "src/nb.py": ""})
    assert validate(tmp_path) == []


def test_a_local_path_through_a_link_outside_is_a_finding(tmp_path):
    write(tmp_path, {"outside/real.py": ""})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs:\n    j:\n"
                                         "      tasks:\n        - task_key: a\n          spark_python_task:\n"
                                         "            python_file: link.py\n"})
    try:
        (bundle / "link.py").symlink_to(tmp_path / "outside" / "real.py")
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    assert "leads outside the bundle folder" in one(
        validate(bundle), "resources.jobs.j.tasks[0].spark_python_task.python_file").message


def test_an_alias_bomb_is_refused_quickly_with_its_line(tmp_path):
    import time

    lines = ["bundle:\n  name: x\n", "a0: &a0 [lol, lol, lol, lol, lol, lol, lol, lol, lol]\n"]
    lines += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]\n" for i in range(1, 10)]
    write(tmp_path, {"databricks.yml": "".join(lines)})
    assert len("".join(lines)) < 1000
    started = time.monotonic()
    findings = validate(tmp_path)
    assert time.monotonic() - started < 1
    assert [(f.severity, f.line, f.column) for f in findings] == [(ERROR, 3, 5)]
    assert findings[0].message == ("YAML anchors and aliases (&name, *name) aren't read here. Write the values out "
                                   "in full.")


def test_an_alias_without_its_own_anchor_is_refused_on_its_line(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nvariables:\n  a:\n    default: *nowhere\n"})
    findings = validate(tmp_path)
    assert [f.line for f in findings] == [5] and "aliases" in findings[0].message


def test_deep_nesting_is_a_finding_not_a_recursion_error(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nx: " + "[" * 5000 + "]" * 5000 + "\n"})
    findings = validate(tmp_path)
    assert [f.message for f in findings] == ["The YAML is nested more than 200 levels deep."]
    assert findings[0].line == 3


def test_a_file_over_the_size_cap_is_not_read(tmp_path, monkeypatch):
    from fabric_desktop.engines import databricks_bundle_check as check

    assert check.MAX_FILE_BYTES == 5 * 1024 * 1024
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - big.yml\n",
                     "big.yml": "# " + "x" * check.MAX_FILE_BYTES + "\n"})
    findings = validate(tmp_path)
    assert [(f.file, f.message) for f in findings] == [("big.yml", "The file is over 5 MB, so it isn't read.")]


def test_a_link_reached_by_several_double_star_paths_counts_once(tmp_path):
    write(tmp_path / "outside", {"x.yml": "resources: {}\n"})
    bundle = write(tmp_path / "bundle", {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - '**/**/**/*.yml'\n",
                                         "res/a.yml": "resources: {}\n"})
    try:
        (bundle / "f.yml").symlink_to(tmp_path / "outside" / "x.yml")
        (bundle / "res" / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    [finding] = validate(bundle)
    assert finding.message == ("The include '**/**/**/*.yml' goes through 2 links, which aren't followed. "
                               "An include must stay inside the bundle folder.")
