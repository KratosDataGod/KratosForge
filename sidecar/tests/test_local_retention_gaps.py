"""What this PC keeps stays bounded: run history is trimmed, replay copies follow local_retention_days,
the local API's log rolls over, and restore keeps copies of only its last few restores."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fabric_desktop import backup, retention, runlog
from fabric_desktop.integrations import app_service

NOW = time.time()
OLD = NOW - 40 * retention.DAY


def test_run_history_is_trimmed_to_its_newest_rows(tmp_path):
    path = tmp_path / "history.jsonl"
    for i in range(50):
        runlog.append(path, {"job": f"j{i}", "pad": "x" * 40}, trim_bytes=2000, keep_rows=10)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert path.stat().st_size <= 2000
    assert rows[-1]["job"] == "j49" and rows[0]["job"] != "j0"  # trimmed, and still growing from the newest
    assert [r["job"] for r in rows] == [f"j{i}" for i in range(50 - len(rows), 50)]  # newest, in order


def test_a_trim_keeps_at_most_half_the_trigger_size(tmp_path):
    path = tmp_path / "history.jsonl"
    for i in range(30):
        runlog.append(path, {"job": f"j{i}", "pad": "x" * 200}, trim_bytes=3000)
    assert path.stat().st_size <= 3000
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["job"] == "j29"


def test_notebook_and_job_runs_append_through_the_trimmed_log():
    # Every writer of history.jsonl goes through runlog.append, none opens it to append on its own.
    from fabric_desktop import lakehouse, publish
    for mod in (lakehouse, publish):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "history_file.open(" not in text, mod.__name__
        assert "runlog.append(" in text, mod.__name__


def test_replay_copies_follow_local_retention_days(tmp_path):
    old = tmp_path / "replays" / "0123456789abcdef.zip"
    new = tmp_path / "replays" / "fedcba9876543210.zip"
    for path, when in ((old, OLD), (new, NOW)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PK")
        os.utime(path, (when, when))
    kept = retention.sweep(tmp_path, 0, now=NOW)  # 0 keeps copies
    assert kept["removed"] == [] and old.exists()
    done = retention.sweep(tmp_path, 30, now=NOW)
    assert done["removed"] == ["replays/0123456789abcdef.zip"]
    assert not old.exists() and new.exists()


def test_a_replay_whose_copy_was_swept_says_so_and_one_in_use_is_touched(tmp_path):
    from fabric_desktop.prove_service import ProveMixin

    class Notebooks:
        def extra(self, name, key):
            return {"bundle": "0123456789abcdef", "notebook": "Sales"}

    svc = ProveMixin.__new__(ProveMixin)
    svc.root = tmp_path
    svc.notebooks = Notebooks()
    with pytest.raises(LookupError, match="copy of this replay's bundle is gone.*Open the bundle again"):
        svc._replay_of("Sales replay")
    path = tmp_path / "replays" / "0123456789abcdef.zip"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a zip")
    os.utime(path, (OLD, OLD))
    with pytest.raises(ValueError):  # not a bundle; the copy was still marked as used first
        svc._replay_of("Sales replay")
    assert path.stat().st_mtime > NOW - 60


def test_the_local_api_log_rolls_over_past_its_cap(tmp_path):
    log = tmp_path / "http.log"
    app_service._roll_log(log, cap=10)  # no log yet: nothing to do
    log.write_text("small")
    app_service._roll_log(log, cap=10)
    assert log.read_text() == "small"
    log.write_text("x" * 20)
    (tmp_path / "http.prev.log").write_text("older")
    app_service._roll_log(log, cap=10)
    assert not log.exists() and (tmp_path / "http.prev.log").read_text() == "x" * 20


def test_starting_the_local_api_rolls_its_log(tmp_path, monkeypatch):
    class Store:
        def settings(self):
            return {"http_port": 1}

    class Svc(app_service.IntegrationsMixin):
        integration_store = Store()
    svc = Svc.__new__(Svc)
    svc.root = tmp_path
    rolled = []
    monkeypatch.setattr(app_service, "_roll_log", lambda path, cap=0: rolled.append(path.name))
    real = subprocess.Popen
    monkeypatch.setattr(app_service.subprocess, "Popen",
                        lambda args, **kw: real([sys.executable, "-c", "import sys; sys.exit(2)"], **kw))
    with pytest.raises(RuntimeError):
        svc._http_start()
    assert rolled == ["http.log"]


def _restore_once(root, tmp_path, when):
    out = backup.create(tmp_path / "out", root, now=when)
    (root / "settings.json").write_text(json.dumps({"n": when.isoformat()}))
    return backup.restore(Path(out["path"]), root, apply=True, now=when)


def test_restore_keeps_copies_of_only_its_newest_restores(tmp_path, monkeypatch):
    root = tmp_path / "FabricDesktop"
    root.mkdir()
    (root / "settings.json").write_text("{}")
    monkeypatch.setattr(backup, "KEEP_SAFETY", 2)
    start = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    pruned = []
    for i in range(4):
        result = _restore_once(root, tmp_path, start + timedelta(minutes=i))
        pruned += result["pruned"]
    left = sorted(d.name for d in (root / backup.SAFETY).iterdir())
    assert left == ["20261101-120200", "20261101-120300"]
    assert pruned == ["20261101-120000", "20261101-120100"]
    # Anything not named as a restore's copies is never removed.
    (root / backup.SAFETY / "notes").mkdir()
    assert backup._prune_safety(root / backup.SAFETY, keep=1) == ["20261101-120200"]
    assert (root / backup.SAFETY / "notes").is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link to a folder needs rights on Windows")
def test_restore_never_follows_a_link_in_its_copies_folder(tmp_path):
    safety = tmp_path / backup.SAFETY
    outside = tmp_path / "outside"
    (outside / "keep.txt").parent.mkdir(parents=True)
    (outside / "keep.txt").write_text("not the app's")
    safety.mkdir()
    (safety / "20200101-000000").symlink_to(outside, target_is_directory=True)
    (safety / "20260101-000000").mkdir()
    assert backup._prune_safety(safety, keep=0) == ["20260101-000000"]
    assert (outside / "keep.txt").exists() and (safety / "20200101-000000").is_symlink()


def test_ended_ai_tool_requests_are_deleted_after_the_days_their_uses_are_kept(tmp_path):
    from fabric_desktop.integrations.store import USES_SECONDS, IntegrationStore
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    now = [1_000_000.0]
    store.clock = lambda: now[0]
    client, _ = store.add_client("Tool", "other", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    ask = lambda code: store.request_approval(client, "ext_run", "Run", "EXECUTE_LOCAL_CODE", {}, {"code": code})  # noqa: E731
    denied = store.decide(ask("denied")["id"], False)
    used = store.decide(ask("used")["id"], True)
    assert store.consume(used["id"], client["id"], used["args_hash"])
    now[0] += USES_SECONDS - 60
    waiting = ask("waiting")
    assert {a["id"] for a in store.approvals("")} >= {denied["id"], used["id"], waiting["id"]}
    now[0] += 120  # past the days kept: the ended requests go, the used one with its use
    later = ask("later")
    ids = {a["id"] for a in store.approvals("", limit=100)}
    assert denied["id"] not in ids and used["id"] not in ids
    assert {waiting["id"], later["id"]} <= ids  # waiting ended (expired) only minutes ago
