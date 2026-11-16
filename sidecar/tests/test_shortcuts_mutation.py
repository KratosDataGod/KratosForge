"""OneLake shortcuts: which managed tables a shortcut is used by, how a shortcut's kind and target type are
named, and which names are refused. Mutation batch 58."""

from __future__ import annotations

import pytest

from fabric_desktop import shortcuts


def _folder(path="Files/raw", name="sales"):
    return {"kind": "folder", "path": path, "name": name, "full_path": f"{path}/{name}"}


def _table(schema="dbo", name="orders"):
    return {"kind": "table", "path": f"Tables/{schema}", "name": name, "full_path": f"Tables/{schema}/{name}"}


def test_a_folder_shortcut_is_used_by_tables_reading_files_under_it():
    entities = [
        {"name": "inside", "files": "/raw/sales/2024/"},
        {"name": "deeper", "files": "raw/sales/x/y"},
        {"name": "sibling", "files": "raw/salesx"},  # shares the prefix but isn't under the folder
        {"name": "itself", "files": "raw/sales"},  # the folder itself, not a file under it
        {"name": "no files", "files": ""},
        {"name": "managed", "schema": "dbo", "table": "sales"},
    ]
    assert shortcuts.used_by(_folder(), entities) == [
        {"entity": "inside", "how": "reads its files"},
        {"entity": "deeper", "how": "reads its files"},
    ]


def test_a_table_shortcut_clashes_only_with_the_same_schema_and_name():
    how = "has the same name, so its runs write into the shortcut's target"
    entities = [
        {"name": "same", "schema": "dbo", "table": "orders"},
        {"name": "other schema", "schema": "sales", "table": "orders"},
        {"name": "other name", "schema": "dbo", "table": "orders2"},
        {"name": "reads files", "files": "raw/orders", "schema": "dbo", "table": "orders"},
    ]
    assert shortcuts.used_by(_table(), entities) == [
        {"entity": "same", "how": how}, {"entity": "reads files", "how": how}]


def test_a_shortcut_of_another_kind_is_used_by_nothing():
    other = {**_folder(), "kind": "other"}
    assert shortcuts.used_by(other, [{"name": "x", "files": "raw/sales/a", "schema": "dbo", "table": "sales"}]) == []


def test_kind_and_target_type_are_named_from_the_path_and_target():
    table = shortcuts.describe({"name": "t", "path": "/Tables/dbo/", "target": {"amazonS3": {"location": "https://b"}}})
    assert (table["kind"], table["target_type"], table["path"]) == ("table", "Amazon S3", "Tables/dbo")
    folder = shortcuts.describe({"name": "f", "path": "Files/x", "target": {"type": "Custom"}})
    assert (folder["kind"], folder["target_type"]) == ("folder", "Custom")
    bare = shortcuts.describe({"name": "f", "path": "Files/x", "target": {}})
    assert bare["target_type"] == "Other" and bare["rest_target"] == {}


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "c:x", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "", "x" * 257])
def test_names_with_other_characters_are_refused(name):
    with pytest.raises(ValueError):
        shortcuts.check_name(name)


def test_a_name_may_be_256_characters_and_is_trimmed():
    assert shortcuts.check_name("  " + "x" * 256 + " ") == "x" * 256
