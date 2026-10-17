"""Local copies of lakehouse data are deleted after the retention period; nothing else is."""

import json
import os
import time

from fabric_desktop import app_settings, retention

NOW = time.time()
OLD = NOW - 40 * retention.DAY
NEW = NOW - 2 * retention.DAY


def touch(path, when, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (when, when))
    return path


def test_sweep_deletes_old_copies_and_keeps_everything_else(tmp_path):
    old_preview = touch(tmp_path / "files" / "lh1" / "Files" / "a.csv", OLD, b"12345")
    new_preview = touch(tmp_path / "files" / "lh1" / "Files" / "b.csv", NEW)
    old_cache = touch(tmp_path / "onelake-cache" / "test" / "LH_Landing" / "Files" / "x.json", OLD)
    old_notebook = touch(tmp_path / "notebooks" / "sales.ipynb", OLD)
    old_history = touch(tmp_path / "history.jsonl", OLD)
    # A staged table: half old (an earlier version), half new (the latest), so it stays whole.
    mixed = tmp_path / "staging" / "orders"
    touch(mixed / "_delta_log" / "00000000000000000000.json", OLD)
    touch(mixed / "part-0.parquet", OLD)
    touch(mixed / "_delta_log" / "00000000000000000001.json", NEW)
    stale = tmp_path / "staging" / "customers"
    touch(stale / "_delta_log" / "00000000000000000000.json", OLD)
    touch(stale / "part-0.parquet", OLD, b"123")

    preview = retention.sweep(tmp_path, 30, dry_run=True, now=NOW)
    assert sorted(preview["removed"]) == sorted(["files/lh1/Files/a.csv", "onelake-cache/test/LH_Landing/Files/x.json",
                                                 "staging/customers"])
    assert old_preview.exists() and stale.exists()  # a dry run deletes nothing

    done = retention.sweep(tmp_path, 30, now=NOW)
    assert done["removed"] == preview["removed"] and done["bytes"] == 5 + 1 + 1 + 3
    assert not old_preview.exists() and not old_cache.exists() and not stale.exists()
    assert not (tmp_path / "onelake-cache" / "test").exists()  # emptied folders go too
    assert new_preview.exists() and old_notebook.exists() and old_history.exists()
    assert (mixed / "part-0.parquet").exists()  # never half a table


def test_zero_keeps_everything(tmp_path):
    touch(tmp_path / "files" / "a.csv", OLD)
    assert retention.sweep(tmp_path, 0, now=NOW)["removed"] == []
    assert (tmp_path / "files" / "a.csv").exists()


def test_policy_sets_the_period(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    assert app_settings.value("local_retention_days") == 30
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"local_retention_days": 7}))
    assert app_settings.value("local_retention_days") == 7
    for bad, problem in ((-1, "can't be negative"), (True, "should be int"), ("7", "should be int")):
        policy.write_text(json.dumps({"local_retention_days": bad}))
        assert app_settings.value("local_retention_days") == 30
        assert problem in app_settings.policy_problems()[0]
