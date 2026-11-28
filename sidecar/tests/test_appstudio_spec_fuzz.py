"""Fuzz App Studio specifications (spec.validate) and the exported project a person imports
(project.import_): seeded mutations of a valid specification, torn, not UTF-8, a BOM, NUL bytes, wrong
JSON types at any depth, deep nesting, huge strings and numbers, duplicate keys and missing fields.

validate must return its problems and never raise. A specification it passes must be usable as one:
every id is text, so the dependency index, find and page_of work on it. An import must give a project
that opens as valid, or be refused with ProjectError or SpecError."""

from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

from fabric_desktop.appstudio import project
from fabric_desktop.appstudio import spec as specs

from tests.settings_fuzz import deep_mutations, mutations


def _valid() -> dict:
    sp = specs.new("Sales")
    sp["sources"] = [{"id": "src_sales", "kind": "semantic-model", "name": "Sales", "workspace_id": "w", "item_id": "i"}]
    sp["measures"] = [{"id": "m_rev", "name": "Revenue", "source": "src_sales", "expression": "SUM(Sales[Amount])"}]
    sp["pages"] = [{"id": "pg_1", "title": "Overview", "components": [
        {"id": "c_rev", "type": "card", "title": "Revenue", "measures": ["m_rev"], "fields": ["Sales[Region]"],
         "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "props": {}}]},
        {"id": "pg_2", "title": "Detail", "components": []}]
    sp["filters"] = [{"id": "f_r", "field": "Region[Name]", "pages": ["pg_1"]}]
    sp["interactions"] = [{"id": "i_d", "kind": "drillthrough", "from": "c_rev", "to": "pg_2"}]
    sp["theme"] = {"colors": {"primary": "#1F6FEB"}, "font": "Segoe UI"}
    assert specs.validate(sp) == []
    return sp


def _check_spec(label: str, raw: bytes) -> None:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return  # not JSON: the readers refuse it before validate (see the import test)
    problems = specs.validate(doc)
    assert isinstance(problems, list) and all(isinstance(p, str) for p in problems), label
    if problems:
        return
    deps = specs.dependencies(doc)
    for id_ in deps:
        assert isinstance(id_, str), (label, id_)
        assert specs.find(doc, id_)[0] in ("source", "measure", "page", "filter", "interaction", "component"), label
    for page in doc["pages"]:
        for comp in page["components"]:
            assert specs.page_of(doc, comp["id"]) == page["id"], label


@pytest.mark.parametrize("seed", [1, 2])
def test_any_specification_is_validated_without_raising(seed):
    for label, raw in mutations(_valid(), seed, 150):
        _check_spec(label, raw)


@pytest.mark.parametrize("seed", [3, 4])
def test_a_specification_damaged_anywhere_inside_is_validated_without_raising(seed):
    for label, raw in deep_mutations(_valid(), seed, 2000):
        _check_spec(label, raw)


def _zip(app: bytes, version: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(project.SPEC, app)
        z.writestr(project.META, json.dumps({"format": 1, "app_id": "x"}))
        if version is not None:
            z.writestr(f"{project.VERSIONS}/0001.json", version)
    return buf.getvalue()


def _check_import(tmp_path, label: str, raw: bytes, n: int) -> None:
    try:
        dest = project.import_(_zip(raw, b'{"spec": ' + raw + b"}"), tmp_path, f"app {n}")
    except (project.ProjectError, project.SpecError) as exc:
        assert str(exc), label
        return
    assert project.open_project(dest)["valid"], label


def test_any_imported_project_is_opened_or_refused_by_name(tmp_path):
    cases = list(mutations(_valid(), 5, 60)) + list(deep_mutations(_valid(), 6, 250))
    for n, (label, raw) in enumerate(cases):
        _check_import(tmp_path, label, raw, n)


def _deep_props(depth: int) -> bytes:
    sp = _valid()
    sp["pages"][0]["components"][0]["props"] = json.loads('{"a":' * depth + "1" + "}" * depth)
    return json.dumps(sp).encode()


@pytest.mark.parametrize("raw", [b"[" * 100_000 + b"]" * 100_000, b'{"a":' * 50_000 + b"1" + b"}" * 50_000],
                         ids=["deep list", "deep object"])
def test_an_import_nested_too_deeply_for_json_is_refused_by_name(tmp_path, raw):
    with pytest.raises(project.ProjectError, match="nests too deeply"):
        project.import_(_zip(raw), tmp_path)


def test_an_import_whose_props_nest_too_deeply_for_the_secret_scan_is_refused_by_name(tmp_path):
    raw = _deep_props(980)
    with pytest.raises(project.SpecError, match="nests too deeply"):
        project.import_(_zip(raw), tmp_path)


def test_a_version_nested_too_deeply_is_left_behind(tmp_path):
    dest = project.import_(_zip(json.dumps(_valid()).encode(), b"[" * 100_000 + b"]" * 100_000), tmp_path)
    assert not list((dest / project.VERSIONS).iterdir())


@pytest.mark.parametrize("where,value", [
    ("target", ["powerbi-report"]), ("refresh", {"mode": ["live"]}), ("sources.0.kind", {"a": 1}),
    ("pages.0.components.0.type", ["card"]), ("interactions.0.kind", ["drillthrough"]),
    ("pages.0.components", 5), ("pages.1.components", True),
])
def test_a_value_of_the_wrong_type_is_a_problem_not_a_crash(where, value):
    sp = _valid()
    *path, last = [int(p) if p.isdigit() else p for p in where.split(".")]
    node = sp
    for step in path:
        node = node[step]
    node[last] = value
    assert specs.validate(sp)


@pytest.mark.parametrize("where", ["measures.0", "pages.1", "pages.0.components.0", "filters.0", "interactions.0"])
@pytest.mark.parametrize("bad", [None, True, 7, ["x"]])
def test_an_entry_without_a_text_id_is_refused(where, bad):
    sp = _valid()
    node = sp
    for step in [int(p) if p.isdigit() else p for p in where.split(".")]:
        node = node[step]
    if bad is None:
        del node["id"]
    else:
        node["id"] = bad
    problems = specs.validate(copy.deepcopy(sp))
    assert any(".id must start with a letter" in p for p in problems), problems


def test_a_reference_that_isnt_text_doesnt_name_an_id():
    sp = _valid()
    sp["pages"][1]["id"] = "True"
    sp["interactions"][0]["to"] = "True"
    sp["filters"][0]["pages"] = [True]
    assert specs.validate(sp) == ["filters[0].pages[0] must name a page in this specification."]
