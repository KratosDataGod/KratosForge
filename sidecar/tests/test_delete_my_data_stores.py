"""Delete my data (WR-412): every store in the personal-data inventory (docs/enterprise-privacy.md) is
either removed by the way a person deletes their data, or listed here as kept, with the reason.

A person deletes their data in two ways (enterprise-privacy.md, "Requests from a person"):

- **Profile > Delete my profile data**, the app's button: the ``profile_delete`` call. Linux and Windows.
- **Everything else on the PC**: ``uninstall-kratosforge.ps1 -RemoveData`` (``-RemoveAuditTrail`` for
  the audit trails too). The script is Windows PowerShell, so the run that seeds every store and checks
  each is gone afterwards is Windows only (CI runs the engine tests on Windows); the checks that tie
  the seeded paths to where the engine writes them, and every inventory row to this file, run anywhere.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from test_profile import call, plain  # noqa: F401 - the signed-in service fixture
from test_ranks import png

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "windows" / "uninstall-kratosforge.ps1"
INVENTORY = ROOT / "docs" / "enterprise-privacy.md"
PACKAGE = ROOT / "sidecar" / "fabric_desktop"
PERSON = "ada.lovelace@contoso.com"
WEBVIEW = "<webview>/"  # a path in the app window's profile, %LOCALAPPDATA%\com.kratosbi.fabricdesktop
CREDENTIAL = "<credential>"  # the mail server password in Windows Credential Manager

# Each inventory row (its first cell, as written) -> what -RemoveData removes: paths in the data folder
# (or the app window's profile), each seeded with personal data and checked gone afterwards.
REMOVED: dict[str, tuple[str, ...]] = {
    "`integrations\\integrations.sqlite`": ("integrations/integrations.sqlite",),
    "`msal_cache.bin`, `fabric-view-signin.bin`, `fabric-view\\`, `%LOCALAPPDATA%\\com.kratosbi.fabricdesktop`": (
        "msal_cache.bin", "fabric-view-signin.bin", "fabric-view/Default/Cookies", WEBVIEW + "EBWebView/Default/Cookies"),
    "`profile\\<hash>\\profile.json`, `avatar.png`": ("profile/0123abcd/profile.json", "profile/0123abcd/avatar.png"),
    "Notebooks (`notebooks\\*.ipynb`)": ("notebooks/Orders.ipynb",),
    "`notebook-sync\\<hash>\\*.json`": ("notebook-sync/0123abcd/stamp0001.json",),
    "`history.jsonl`, `schedule_runs.jsonl`, `fabric_runs.jsonl`, `side_by_side.jsonl`, `proofs.jsonl`, `validations.json`": (
        "history.jsonl", "schedule_runs.jsonl", "fabric_runs.jsonl", "side_by_side.jsonl", "proofs.jsonl", "validations.json"),
    "`receipts\\`": ("receipts/proof-1.json",),
    "`risk_acknowledgements.jsonl`": ("risk_acknowledgements.jsonl",),
    "`organizations.json`": ("organizations.json",),
    "`workloads.sqlite`": ("workloads.sqlite",),
    "`platform_outbox.sqlite`, `alerts_outbox.sqlite`": ("platform_outbox.sqlite", "alerts_outbox.sqlite"),
    "`settings.json`": ("settings.json",),
    "Windows Credential Manager `KratosForge/mail-server`": (CREDENTIAL,),
    "`restore-safety\\<time>`": ("restore-safety/20261120-101500/settings.json",),
    "`files\\`, `staging\\`, `onelake-cache\\`, `replays\\`, `preview-files\\`": (
        "files/sales/orders.parquet", "staging/w1/part-0.parquet", "onelake-cache/c1.parquet", "replays/r1.zip",
        "preview-files/p1.csv"),
    "`sql-runs\\`": ("sql-runs/run-1.json",),
    "`local-data\\`, `usage_imports\\`, `learn\\`, `academy\\`": (
        "local-data/people.csv", "usage_imports/u1.csv", "learn/progress.json", "academy/exams/e1.json"),
    "App storage in the window (`localStorage`)": (WEBVIEW + "EBWebView/Default/Local Storage/leveldb/000003.log",),
    "`sidecar.log`, `sidecar.prev.log`, `integrations\\http.log`, `http.prev.log`, agent `logs\\agent.log`": (
        "sidecar.log", "sidecar.prev.log", "integrations/http.log", "integrations/http.prev.log"),
    "`app-crash.log`": ("app-crash.log",),
    "`keys\\fingerprint.key`": ("keys/fingerprint.key",),
    "`telemetry\\usage.json` (only when usage data is on)": ("telemetry/usage.json",),
    # Rows #3159 adds (IN_REVIEW below).
    "`alerts_outbox.sqlite.sent-not-removed.json` (and the same beside each outbox below)": (
        "alerts_outbox.sqlite.sent-not-removed.json", "platform_outbox.sqlite.sent-not-removed.json",
        "schedule_alerts.sqlite3.sent-not-removed.json", "optimize/outbox.sqlite.sent-not-removed.json"),
    "`schedule_alerts.sqlite3`": ("schedule_alerts.sqlite3",),
    "`optimize\\outbox.sqlite`": ("optimize/outbox.sqlite",),
    "`platform\\platform-<env>.sqlite`, `platform\\keys.sqlite` (only when no platform in Fabric is set)": (
        "platform/platform-dev.sqlite", "platform/keys.sqlite"),
}

# Each inventory row (or the part of one) that deleting a person's data deliberately leaves, and why.
KEPT: dict[str, str] = {
    "`audit\\audit-YYYY-MM.jsonl`": (
        "The audit trail: evidence of who ran or changed what, which the organization may have to keep. "
        "-RemoveData keeps it; -RemoveAuditTrail deletes it too (checked below)."),
    "`integrations\\audit\\`": "The AI tools' audit trail: as the audit trail above.",
    "Notebooks (`notebooks\\*.ipynb`)": (
        "Only the copies in Git: a pushed notebook, with its comments' authors, is the repository's to remove."),
    "`sidecar.log`, `sidecar.prev.log`, `integrations\\http.log`, `http.prev.log`, agent `logs\\agent.log`": (
        "Only the agent's logs\\agent.log: it is in the headless agent's home (%ProgramData%\\FabricDesktop\\agent "
        "or a server), which is IT's, run by the agent's own account; see the agent home row."),
    "Git configuration in a repository folder": (
        "It belongs to the repository and to Git, not to the app's data folder; the person changes it with Git."),
    # Rows #3159 adds (IN_REVIEW below).
    "Incident list (`incidents/incidents.json` in each environment's control folder, on this PC or in OneLake)": (
        "Open question. It is the environment's shared record (in OneLake, or a local environment's project "
        "folder, .local\\incidents), with every team member's notes and actions in it: deleting the file erases "
        "colleagues' records too, and removing one person would mean redacting their name in each incident, "
        "which nothing does yet. The data folder holds no copy."),
    "Agent home: `queue.sqlite3`, `alerts.sqlite3`, `job_alerts.sqlite3`, `shortcut_alerts.sqlite3`": (
        "Open question. The headless agent's home (%ProgramData%\\FabricDesktop\\agent, or a Linux server) is IT's: "
        "the agent's account writes it, it serves an environment rather than one person, and the uninstall runs "
        "unelevated and never touches %ProgramData%. Its alerts.sqlite3 (and the .sent-not-removed.json beside "
        "it) is deleted with the agent stopped, by IT."),
    "Windows Application event log, source KratosForge (Windows only)": (
        "No personal data (a fingerprint of the audit folder's path, a hash and a count), and a person can't "
        "delete event log records: an administrator clears the Application log."),
}

# Rows of the inventory that PR #3159 adds and main doesn't have yet. Once it merges they are rows like
# any other and this set can go.
IN_REVIEW = {
    "`alerts_outbox.sqlite.sent-not-removed.json` (and the same beside each outbox below)",
    "`schedule_alerts.sqlite3`",
    "`optimize\\outbox.sqlite`",
    "`platform\\platform-<env>.sqlite`, `platform\\keys.sqlite` (only when no platform in Fabric is set)",
    "Incident list (`incidents/incidents.json` in each environment's control folder, on this PC or in OneLake)",
    "Agent home: `queue.sqlite3`, `alerts.sqlite3`, `job_alerts.sqlite3`, `shortcut_alerts.sqlite3`",
    "Windows Application event log, source KratosForge (Windows only)",
}

OUTBOXES = {"alerts_outbox.sqlite", "platform_outbox.sqlite", "schedule_alerts.sqlite3", "outbox.sqlite"}
AUDIT_TRAILS = ("audit/audit-2026-11.jsonl", "integrations/audit/audit-2026-11.jsonl")


def _inventory_rows() -> list[str]:
    text = INVENTORY.read_text(encoding="utf-8")
    table = text.split("## Inventory", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    return [row.split(" | ", 1)[0][2:].strip() for row in rows[2:]]  # past the header and its rule


def _source(rel: str) -> str:
    return (PACKAGE / rel).read_text(encoding="utf-8")


# ---- the guard: every store is removed or kept for a reason


def test_every_store_in_the_inventory_is_removed_or_kept_for_a_stated_reason():
    rows = _inventory_rows()
    assert len(rows) >= 20, rows
    missing = [r for r in rows if r not in REMOVED and r not in KEPT]
    assert missing == [], ("each new inventory row needs an entry in REMOVED (seeded and checked gone) "
                           f"or in KEPT (with why delete-my-data leaves it): {missing}")
    assert all(len(why) > 40 for why in KEPT.values())


def test_this_file_names_no_store_the_inventory_doesnt_have():
    rows = set(_inventory_rows())
    stale = sorted((set(REMOVED) | set(KEPT)) - rows - IN_REVIEW)
    assert stale == [], f"rows no longer in docs/enterprise-privacy.md: {stale}"


def test_the_audit_trail_is_kept_as_the_privacy_page_says():
    text = INVENTORY.read_text(encoding="utf-8")
    assert "keeps the audit trails unless you also pass `-RemoveAuditTrail`" in text
    assert "Uninstall with `-RemoveData` keeps them unless `-RemoveAuditTrail` is added" in text
    script = SCRIPT.read_text(encoding="utf-8")
    assert '$AuditTrails = @("audit", "integrations\\audit")' in script
    assert not any(p.startswith(("audit/", "integrations/audit/")) for paths in REMOVED.values() for p in paths)


# ---- the seeded paths are where the engine writes them


def test_the_alerts_outboxes_and_their_kept_ids_are_in_the_data_folder():
    from fabric_desktop import schedule_service, shortcut_watch
    from fabric_desktop.workloads import notify
    assert notify.GONE_SUFFIX == ".sent-not-removed.json"
    assert schedule_service.SCHEDULE_ALERTS == "schedule_alerts.sqlite3"
    assert "Outbox(self.root / SCHEDULE_ALERTS)" in _source("schedule_service.py")
    assert 'Outbox(self.root / "alerts_outbox.sqlite")' in _source("incident_service.py")
    assert 'self.root / "platform_outbox.sqlite"' in _source("platform_notify_service.py")
    assert 'self.root / "optimize" / "outbox.sqlite"' in _source("optimize_service.py")
    # The kept ids are a file beside each outbox, so they go wherever the outbox goes.
    assert "self.path.with_name(self.path.name + GONE_SUFFIX)" in _source("workloads/notify.py")
    removed = [p for paths in REMOVED.values() for p in paths]
    for outbox in ("alerts_outbox.sqlite", "platform_outbox.sqlite", "schedule_alerts.sqlite3", "optimize/outbox.sqlite"):
        assert outbox in removed and outbox + notify.GONE_SUFFIX in removed, outbox
    # The agent's outboxes are in its home, not the data folder: kept, with the reason.
    agent = _source("agent.py")
    assert 'Outbox(self.config.home / "alerts.sqlite3"' in agent
    assert 'Outbox(self.config.home / "job_alerts.sqlite3"' in agent
    assert shortcut_watch.LEDGER == "shortcut_alerts.sqlite3"


def test_the_other_seeded_stores_are_where_the_engine_writes_them():
    from fabric_desktop import retention, risk_service, service
    assert set(retention.CACHE_FOLDERS) | {retention.PREVIEWS} == {"files", "staging", "onelake-cache", "replays", "preview-files"}
    assert risk_service._ACKS == "risk_acknowledgements.jsonl"
    assert 'self.root / "platform" / "keys.sqlite"' in _source("central/service.py")
    assert 'self.root / "platform" / f"platform-{env}.sqlite"' in _source("central/service.py")
    assert 'ReceiptStore(self.root / "receipts")' in _source("prove_service.py")
    assert 'LocalData(self.root / "local-data")' in _source("localdata_service.py")
    assert 'NotebookShelf(self.root / "notebooks"' in _source("service.py")
    assert service.data_dir().name == "FabricDesktop"
    # The incident list is in the environment's control area, never in the data folder: kept, with the reason.
    from fabric_desktop.workloads import incidents
    assert incidents.KEY == "incidents/incidents.json"


# ---- Profile > Delete my profile data, as the app calls it


def test_delete_my_profile_data_leaves_no_profile_file(plain):  # noqa: F811
    call(plain, "profile_save", identity={"display_name": PERSON, "role": "Data engineer"},
         report_summary=f"Report by {PERSON}")
    call(plain, "profile_avatar_set", png_base64=base64.b64encode(png()).decode())
    folder = plain.root / "profile"
    seeded = [p for p in folder.rglob("*") if p.is_file()]
    assert {p.name for p in seeded} >= {"profile.json", "avatar.png"}
    assert any(PERSON in p.read_text(encoding="utf-8") for p in seeded if p.suffix == ".json")
    assert call(plain, "profile_delete") == {"deleted": True}
    assert [p for p in folder.rglob("*") if p.is_file()] == []
    view = call(plain, "profile_get")
    assert view["identity"]["display_name"] == "" and view["avatar"] is None


# ---- uninstall-kratosforge.ps1 -RemoveData, on Windows


def _seed(path: Path) -> None:
    """``path`` with PERSON in it, in the store's own shape where that matters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".sent-not-removed.json"):
        path.write_text(json.dumps([[1, 1763633700.0]]), encoding="utf-8")  # only ids and times
    elif path.name in OUTBOXES:
        from fabric_desktop.workloads.notify import Outbox
        Outbox(path).add(f"Orders failed, assigned to {PERSON}", [f"Owner: {PERSON}"], "Teams didn't answer")
    elif path.suffix in (".sqlite", ".sqlite3"):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE people (who TEXT)")
            db.execute("INSERT INTO people VALUES (?)", (PERSON,))
        db.close()
    else:
        path.write_text(f"by {PERSON}", encoding="utf-8")


def _ps(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(SCRIPT), *args], capture_output=True, text=True, timeout=180)


@pytest.mark.skipif(sys.platform != "win32", reason="runs the uninstall script, which is Windows PowerShell")
def test_on_windows_remove_data_leaves_no_store_but_the_audit_trails(tmp_path):
    data, webview = tmp_path / "FabricDesktop", tmp_path / "com.kratosbi.fabricdesktop"
    tag = uuid.uuid4().hex[:8]
    cred = f"KratosForge-test/{tag}"
    files = [p for paths in REMOVED.values() for p in paths if p != CREDENTIAL]

    def place(rel: str) -> Path:
        return webview / rel[len(WEBVIEW):] if rel.startswith(WEBVIEW) else data / rel

    # The kept-ids files last: a new Outbox clears the one beside it, as a fresh outbox has no kept ids.
    for rel in sorted((*files, *AUDIT_TRAILS), key=lambda r: r.endswith(".sent-not-removed.json")):
        _seed(place(rel))
    assert all(place(rel).is_file() for rel in files)
    subprocess.run(["cmdkey.exe", f"/generic:{cred}", "/user:u", f"/pass:{PERSON}"], check=True,
                   capture_output=True, timeout=60)
    common = ["-DataDir", str(data), "-TaskFolder", f"\\KFTest{tag}\\", "-LinkKey",
              f"HKCU:\\Software\\Classes\\kratosforge-test-{tag}", "-Credentials", cred, "-WebViewDir", str(webview)]
    try:
        done = _ps(*common, "-RemoveData")
        assert done.returncode == 0, done.stdout + done.stderr
        left = [rel for rel in files if place(rel).exists()]
        assert left == [], f"delete-my-data left these stores: {left}"
        assert not webview.exists()
        assert "NONE" in subprocess.run(["cmdkey.exe", f"/list:{cred}"], capture_output=True, text=True,
                                        timeout=60).stdout
        # Nothing but the audit trails holds anything, personal data or not.
        assert sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()) == sorted(AUDIT_TRAILS)
        assert "Kept the audit trail" in done.stdout
        everything = _ps(*common, "-RemoveData", "-RemoveAuditTrail")
        assert everything.returncode == 0, everything.stdout + everything.stderr
        assert not data.exists()
    finally:
        subprocess.run(["cmdkey.exe", f"/delete:{cred}"], capture_output=True, timeout=60)


def test_the_seeded_shapes_hold_personal_data(tmp_path):
    """The Windows run's seeds are real stores with the person in them, so 'gone' means something."""
    for rel in ("alerts_outbox.sqlite", "schedule_alerts.sqlite3", "optimize/outbox.sqlite", "workloads.sqlite"):
        _seed(tmp_path / rel)
        with sqlite3.connect(tmp_path / rel) as db:
            table = "outbox" if Path(rel).name in OUTBOXES else "people"
            rows = db.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - a fixed name
        db.close()
        assert rows and PERSON in repr(rows), rel
    _seed(tmp_path / "alerts_outbox.sqlite.sent-not-removed.json")
    assert json.loads((tmp_path / "alerts_outbox.sqlite.sent-not-removed.json").read_text()) == [[1, 1763633700.0]]
    _seed(tmp_path / "history.jsonl")
    assert PERSON in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
