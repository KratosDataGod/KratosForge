"""warehouse_compare.py at its edges (mutation batch 164, a boundary re-score of batch 31,
docs/quality/mutation-testing.md): one new column is named in the singular, two in the plural."""

from fabric_desktop import warehouse_compare as wc


def _changed_table(project: str):
    return {"kind": "table", "schema": "dbo", "name": "t", "state": "changed", "path": "t.sql",
            "project_text": project, "warehouse_text": "CREATE TABLE dbo.t (a int NOT NULL)"}


def test_one_new_column_is_a_column_and_two_are_columns():
    [one] = wc.deploy_plan([_changed_table("CREATE TABLE dbo.t (a int NOT NULL, b int NULL)")])
    assert one["action"] == "add_columns" and one["why"].startswith("new nullable column b; ")
    [two] = wc.deploy_plan([_changed_table("CREATE TABLE dbo.t (a int NOT NULL, b int NULL, c int NULL)")])
    assert two["why"].startswith("new nullable columns b, c; ")
