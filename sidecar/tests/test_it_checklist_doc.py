"""#1671: the IT deployment checklist (docs/it-deployment-checklist.md) names only settings, paths and
commands that exist, and its "Not ready yet" list matches the enterprise readiness scorecard."""

import re
from pathlib import Path

from fabric_desktop import app_settings, cli

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "docs" / "it-deployment-checklist.md").read_text(encoding="utf-8")
# Code spans that read as a policy key: lowercase words, joined by underscores, as written or quoted
# in a JSON fragment ("auto_update": false).
KEY = re.compile(r'`(?:([a-z]+(?:_[a-z]+)*)|"([a-z]+(?:_[a-z]+)*)": [^`]*)`')
NOT_KEYS: set[str] = set()  # a code span that reads like a key but isn't a setting goes here, with why


def _bullets(text: str) -> list[str]:
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


def test_every_setting_it_names_is_a_policy_key():
    named = {a or b for a, b in KEY.findall(PAGE)} - NOT_KEYS
    assert len(named) >= 10, named
    unknown = sorted(named - set(app_settings.POLICY_KEYS))
    assert not unknown, f"docs/it-deployment-checklist.md names settings the policy file doesn't have: {unknown}"


def test_the_paths_it_names_are_where_the_engine_looks(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert app_settings.policy_path().parts[-2:] == ("FabricDesktop", "policy.json")
    assert r"`%ProgramData%\FabricDesktop\policy.json`" in PAGE
    server = (ROOT / "sidecar" / "fabric_desktop" / "server.py").read_text(encoding="utf-8")
    assert '"FabricDesktop", "sidecar.log"' in server
    assert r"`%LOCALAPPDATA%\FabricDesktop\sidecar.log`" in PAGE
    from fabric_desktop.audit import AuditLog
    log = AuditLog(tmp_path / "audit")
    log.record("x", "ok", 0)
    assert [p.name[:6] for p in log._files()] == ["audit-"]
    assert r"`%LOCALAPPDATA%\FabricDesktop\audit\audit-YYYY-MM.jsonl`" in PAGE


def test_the_support_bundle_command_it_names_runs(monkeypatch, tmp_path, capsys):
    assert "`fabric-desktop support-bundle --out <folder>`" in PAGE and "`--preview`" in PAGE
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    cli.main(["support-bundle", "--preview"])
    assert "CONTENTS.txt" in capsys.readouterr().out
    cli.main(["support-bundle", "--out", str(tmp_path / "out")])
    assert len(list((tmp_path / "out").glob("fabric-desktop-support-*.zip"))) == 1


def test_not_ready_yet_matches_the_readiness_scorecard():
    scorecard = (ROOT / "docs" / "audits" / "ENTERPRISE_READINESS.md").read_text(encoding="utf-8")
    deployment = scorecard.split("### Enterprise deployment", 1)[1].split("\n### ", 1)[0]
    missing = _bullets(deployment.split("What's missing:", 1)[1])
    ours = _bullets(PAGE.split("## Not ready yet", 1)[1])
    assert missing and ours == missing, "keep 'Not ready yet' the same as the scorecard's 'What's missing'"
