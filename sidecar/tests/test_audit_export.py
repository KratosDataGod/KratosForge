"""An audit export IT can check with nothing but the file and its manifest (WR-403): any changed,
deleted, reordered or added line fails, and against the trail it came from, so does an export
rebuilt from scratch with a manifest to match."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from fabric_desktop import audit_chain


@pytest.fixture
def trail(tmp_path):
    folder = tmp_path / "audit"
    for i, at in enumerate(["2026-10-30T09:00:00+00:00", "2026-10-31T09:00:00+00:00", "2026-11-01T09:00:00+00:00",
                            "2026-11-02T09:00:00+00:00", "2026-11-03T09:00:00+00:00"]):
        audit_chain.append(folder, {"at": at, "action": f"a{i}", "outcome": "ok", "who": "dana@example.com"})
    return folder


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_a_whole_export_checks_out_on_its_own_and_against_its_trail(trail, tmp_path):
    out = tmp_path / "x" / "audit-export.jsonl"
    made = audit_chain.export(trail, out)
    assert made["lines"] == 5 and made["first_prev"] == audit_chain.GENESIS
    assert audit_chain.verify_export(out) == {"ok": True, "lines": 5, "problems": []}
    assert audit_chain.verify_export(out, trail)["ok"]


def test_an_export_from_a_date_is_a_chain_of_its_own(trail, tmp_path):
    out = tmp_path / "since.jsonl"
    made = audit_chain.export(trail, out, since="2026-11-01")
    assert made["lines"] == 3 and made["first_prev"] != audit_chain.GENESIS  # it starts mid-trail across months
    assert [json.loads(line)["action"] for line in _lines(out)] == ["a2", "a3", "a4"]
    assert audit_chain.verify_export(out)["ok"] and audit_chain.verify_export(out, trail)["ok"]


def _tamper(out, change):
    lines = _lines(out)
    change(lines)
    out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _changed(lines):
    entry = json.loads(lines[2])
    entry["outcome"] = "error"
    lines[2] = json.dumps(entry)


@pytest.mark.parametrize("change, says", [
    (_changed, "line 3: this line was changed"),
    (lambda lines: lines.pop(2), "line 3: a line before it is missing or changed"),
    (lambda lines: lines.insert(1, lines.pop(3)), "a line before it is missing or changed"),
    (lambda lines: lines.pop(), "lines were cut from the end"),
])
def test_a_tampered_export_fails(trail, tmp_path, change, says):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    _tamper(out, change)
    result = audit_chain.verify_export(out)
    assert not result["ok"]
    assert any("SHA-256" in p for p in result["problems"])
    assert any(says in p for p in result["problems"]), result["problems"]


def test_a_changed_line_fails_even_with_the_manifests_checksum_rewritten(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    _tamper(out, _changed)
    manifest = json.loads(audit_chain.manifest_path(out).read_text())
    manifest["sha256"] = audit_chain._sha256(out)
    audit_chain.manifest_path(out).write_text(json.dumps(manifest))
    assert audit_chain.verify_export(out)["problems"] == ["line 3: this line was changed"]


def test_an_export_rebuilt_from_scratch_passes_alone_but_not_against_its_trail(trail, tmp_path):
    forged = tmp_path / "forged"
    for line in _lines(audit_chain.files(trail)[0]):
        entry = json.loads(line)
        entry.pop("hash"), entry.pop("prev")
        entry["who"] = "someone-else@example.com"
        audit_chain.append(forged, entry)
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(forged, out)
    assert audit_chain.verify_export(out)["ok"]  # self-consistent: no secret makes a manifest
    assert audit_chain.verify_export(out, trail)["problems"] == ["the export's lines aren't in this trail"]


def test_a_missing_or_foreign_manifest_fails(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    audit_chain.manifest_path(out).write_text("{}")
    assert not audit_chain.verify_export(out)["ok"]
    audit_chain.manifest_path(out).unlink()
    assert not audit_chain.verify_export(out)["ok"]


def test_the_command_line_exports_and_checks(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    run = [sys.executable, "-m", "fabric_desktop.cli", "audit", "--folder", str(trail)]
    assert subprocess.run([*run, "--export", str(out), "--since", "2026-11-02"], capture_output=True).returncode == 0
    ok = subprocess.run([*run, "--verify-export", str(out)], capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout)["lines"] == 2
    _tamper(out, lambda lines: lines.pop(0))
    assert subprocess.run([*run, "--verify-export", str(out)], capture_output=True).returncode == 1


@pytest.mark.parametrize("where", ["audit-2026-11.jsonl", "copy.jsonl", "sub/copy.jsonl"])
def test_an_export_into_the_audit_folder_is_refused_and_the_trail_is_untouched(trail, where):
    before = {p.name: p.read_bytes() for p in trail.iterdir()}
    with pytest.raises(ValueError, match="outside the audit folder"):
        audit_chain.export(trail, trail / where, since="2026-11-02")
    assert {p.name: p.read_bytes() for p in trail.iterdir()} == before
    assert audit_chain.verify(trail)["ok"]


def test_an_earlier_export_is_kept_unless_replaced_on_purpose(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out, since="2026-11-03")
    with pytest.raises(ValueError, match="there already"):
        audit_chain.export(trail, out)
    assert audit_chain.verify_export(out)["lines"] == 1
    assert audit_chain.export(trail, out, replace=True)["lines"] == 5


@pytest.mark.parametrize("since", ["yesterday", "11/09/2026", "2026-13-01"])
def test_a_since_that_isnt_a_date_is_refused(trail, tmp_path, since):
    with pytest.raises(ValueError, match="isn't a date"):
        audit_chain.export(trail, tmp_path / "x.jsonl", since=since)
    assert not (tmp_path / "x.jsonl").exists()


def test_exporting_this_pcs_trail_is_itself_recorded_after_the_copy(tmp_path):
    import os

    from fabric_desktop.audit import AuditLog
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "appdata")}
    folder = tmp_path / "appdata" / "FabricDesktop" / "audit"
    AuditLog(folder).record("meta_promote", "ok", 1.0, {"env": "prod"})
    out = tmp_path / "audit-export.jsonl"
    run = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", "audit", "--export", str(out)],
                         capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    assert [json.loads(line)["action"] for line in _lines(out)] == ["meta_promote"]  # the copy doesn't hold its own line
    (line,) = AuditLog(folder).recent(1, "audit_export")
    assert line["target"] == {"since": "", "lines": 1, "sha256": json.loads(run.stdout)["sha256"]}
    assert AuditLog(folder).verify()["ok"] and audit_chain.verify_export(out, folder)["ok"]


@pytest.mark.parametrize("since, first", [("20261101", "a2"), ("2026-11-01T10:30:00+02:00", "a2"),
                                          ("2026-11-01T09:00:01Z", "a3"), ("2026-11-01T09:00:00", "a2")])
def test_since_is_compared_as_a_time_whatever_its_iso_form(trail, tmp_path, since, first):
    out = tmp_path / "since.jsonl"
    audit_chain.export(trail, out, since=since)
    assert json.loads(_lines(out)[0])["action"] == first


def test_an_agents_trail_exports_and_checks_by_its_home(tmp_path):
    from fabric_desktop.audit import AuditLog
    home = tmp_path / "agent-home"
    agent = AuditLog(home / "audit", app="agent")
    for n in range(3):
        agent.record("agent_job", "ok", 1.0, {"job_id": f"j{n}"})
    out = tmp_path / "handover" / "agent-audit.jsonl"
    run = [sys.executable, "-m", "fabric_desktop.cli", "audit", "--agent-home", str(home)]
    assert subprocess.run([*run, "--export", str(out)], capture_output=True).returncode == 0
    ok = subprocess.run([*run, "--verify-export", str(out)], capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout) == {"ok": True, "lines": 3, "problems": []}
    assert [line["action"] for line in AuditLog(home / "audit").recent(10)] == ["agent_job"] * 3  # not written to

    forged = tmp_path / "forged"
    for n in range(3):
        audit_chain.append(forged, {"at": "2026-11-11T00:00:00+00:00", "action": "agent_job", "n": n})
    fake = tmp_path / "fake.jsonl"
    audit_chain.export(forged, fake)
    bad = subprocess.run([*run, "--verify-export", str(fake)], capture_output=True, text=True)
    assert bad.returncode == 1 and json.loads(bad.stdout)["problems"] == ["the export's lines aren't in this trail"]

    both = subprocess.run([*run, "--folder", str(home / "audit"), "--verify-export", str(out)], capture_output=True,
                          text=True)
    assert both.returncode == 2 and "not both" in both.stderr
