"""The local bundle check (engines/databricks_bundle_check.py): edges the earlier tests left open (mutation
batch 477). The nesting limit counted exactly, findings ordered by line, the words of a few findings, and
the include walk. Nothing here runs the Databricks CLI or calls the network."""

from __future__ import annotations

from pathlib import Path

from fabric_desktop.engines import databricks_bundle_check as check
from fabric_desktop.engines.databricks_bundle_check import validate


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def messages(findings, path):
    return [f.message for f in findings if f.path == path]


def nested(depth: int) -> str:
    return "bundle:\n  name: x\nz: " + "[" * depth + "1" + "]" * depth + "\n"


def test_yaml_nested_to_exactly_200_levels_is_read_and_one_more_isnt(tmp_path):
    write(tmp_path / "ok", {"databricks.yml": nested(198)})  # the top mapping, 198 lists and the 1: 200 levels
    write(tmp_path / "deep", {"databricks.yml": nested(199)})
    assert not [f for f in validate(tmp_path / "ok") if "nested" in f.message]
    assert [f.message for f in validate(tmp_path / "deep")] == ["The YAML is nested more than 200 levels deep."]


def test_many_keys_side_by_side_arent_nesting(tmp_path):
    keys = "".join(f"    k{i}: {i}\n" for i in range(300))
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nvariables:\n  v:\n    default:\n" + keys})
    assert not [f for f in validate(tmp_path) if "nested" in f.message]


def test_a_key_given_twice_is_named_by_its_own_name(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: a\n  name: b\n"})
    assert messages(validate(tmp_path), "bundle.name") == [
        "The key 'name' is given twice in one mapping, on lines 2 and 3; only the one on line 3 would be read."]


def test_a_blank_bundle_name_is_an_error(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: '  '\n"})
    assert "bundle.name is the bundle's name, a string." in messages(validate(tmp_path), "bundle.name")


def test_an_integer_where_a_list_belongs_is_called_an_integer(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs:\n    j:\n      tasks: 5\n"})
    assert messages(validate(tmp_path), "resources.jobs.j.tasks") == ["tasks is a list, not an integer."]


def test_a_setting_that_isnt_a_mapping_is_named_by_its_last_part(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs:\n    j: 5\n"})
    assert messages(validate(tmp_path), "resources.jobs.j") == ["j is a mapping of settings."]


def test_depends_on_that_isnt_a_mapping_is_reported_at_the_entry(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nresources:\n  jobs:\n    j:\n      tasks:\n"
                                       "        - task_key: a\n          notebook_task:\n"
                                       "            notebook_path: /Workspace/n\n          depends_on:\n"
                                       "            - b\n            - {}\n"})
    paths = {f.path for f in validate(tmp_path) if f.message == "Each depends_on entry names a task_key."}
    assert paths == {"resources.jobs.j.tasks[0].depends_on[0]", "resources.jobs.j.tasks[0].depends_on[1].task_key"}


def test_a_reference_to_a_missing_resource_names_its_type_and_name(tmp_path):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\nvariables:\n  v:\n"
                                       "    default: ${resources.jobs.nope.id}\n"})
    assert messages(validate(tmp_path), "variables.v.default") == [
        "${resources.jobs.nope.id} refers to resources.jobs.nope, which the bundle doesn't define."]


def test_findings_in_one_file_come_in_line_order(tmp_path):
    write(tmp_path, {"databricks.yml": "zzz: 1\nbundle:\n  name: x\naaa: 2\n"})
    assert [f.path for f in validate(tmp_path)] == ["zzz", "aaa"]


def test_files_matched_outside_the_folder_are_counted_in_the_finding(tmp_path, monkeypatch):
    write(tmp_path, {"databricks.yml": "bundle:\n  name: x\ninclude:\n  - one/*.yml\n  - two/*.yml\n",
                     "one/out1.yml": "{}\n", "two/out1.yml": "{}\n", "two/out2.yml": "{}\n"})
    inside = check._Checker.inside
    monkeypatch.setattr(check._Checker, "inside", lambda self, p: not p.name.startswith("out") and inside(self, p))
    findings = validate(tmp_path)
    assert messages(findings, "include[0]") == [(
        "The include 'one/*.yml' matches 1 file outside the bundle folder, which isn't read. An include must stay "
        "inside the bundle folder.")]
    assert messages(findings, "include[1]") == [(
        "The include 'two/*.yml' matches 2 files outside the bundle folder, which aren't read. An include must stay "
        "inside the bundle folder.")]


def test_a_path_that_cant_be_resolved_isnt_inside(tmp_path, monkeypatch):
    checker = check._Checker(tmp_path)

    def refuse(self, strict=False):
        raise OSError("no")

    monkeypatch.setattr(Path, "resolve", refuse)
    assert checker.inside(tmp_path / "a.yml") is False


def test_a_double_star_before_a_name_matches_only_that_name(tmp_path):
    write(tmp_path, {"a.yml": "", "other.yml": "", "sub/a.yml": "", "sub/b.yml": ""})
    files, links = check._walk_glob(tmp_path, "**/a.yml", 10)
    assert sorted(Path(f).relative_to(tmp_path).as_posix() for f in files) == ["a.yml", "sub/a.yml"]
    assert links == 0


def test_a_path_on_another_drive_is_shown_whole(monkeypatch):
    def other_drive(path, start=None):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(check.os.path, "relpath", other_drive)
    assert check._rel(Path("/elsewhere/x.yml"), Path("/bundle")) == "/elsewhere/x.yml"


def test_a_line_is_found_from_the_nearest_parent():
    assert check._line_of({"a": 3}, "a.b[0].c") == 3
    assert check._line_of({"a.b": 5}, "a.b[0]") == 5
    assert check._line_of({}, "a.b") is None

