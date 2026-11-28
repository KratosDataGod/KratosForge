"""Databricks bundles: the path the other tests leave out (coverage pass 20). A target whose name the
CLI couldn't be given safely is left out of the bundle's targets."""

from __future__ import annotations

from fabric_desktop.engines import databricks_bundles as db


def test_targets_with_names_that_arent_plain_are_left_out(tmp_path):
    (tmp_path / "databricks.yml").write_text(
        "bundle:\n  name: sales\n"
        "targets:\n"
        "  dev:\n    mode: development\n    default: true\n"
        "  'prod --auto-approve':\n    mode: production\n"
        "  'a b':\n    mode: production\n"
        "  test: plain-text-not-a-mapping\n",
        encoding="utf-8")
    out = db.targets(str(tmp_path))
    assert out["name"] == "sales"
    assert out["targets"] == [{"name": "dev", "mode": "development", "default": True},
                              {"name": "test", "mode": "", "default": False}]
