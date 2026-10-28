"""GOV-002 (PB-057): policy packages and the offline policy check. A project's policies are taken from the
platform into one file with their stored SHA-256s; Check now and ``fabric-desktop governance check`` use it
when the platform can't be reached, refuse it when its hashes don't match, and call no platform, Fabric or
OneLake while they do."""

import datetime as dt
import json

import pytest
import requests

from fabric_desktop import cli, onelake
from fabric_desktop.central import client, policy, policy_package as pp
from fabric_desktop.service import Service, data_dir


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    s = Service(root=data_dir())
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def publish(svc, template, pid):
    svc.governance_template_use(template, id=pid)
    svc.governance_publish(pid)
    svc.governance_assign(pid, "enterprise")


def mark_pii(svc):
    """One column marked PII and labelled internal: the labels policy (high, so Error) finds it."""
    path = svc._project().root / "metadata" / "entities" / "silver.customers.toml"
    col = next(iter(svc._project().entities["silver.customers"].columns))
    path.write_text(path.read_text() + f'\n[catalog.columns.{col}]\ntags = ["PII"]\n')
    svc._project().reload()
    return col


def except_labels(svc, days=30):
    until = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    req = svc.platform_request("exception", "labels", "Masked copy not ready",
                               {"policy": "labels", "scope": "project", "scope_target": "crm", "until": until})
    svc.platform_decide(req["id"], True)
    return until


def package_file(svc):
    return svc.root / "governance_packages" / "crm.json"


def unreachable(svc):
    def down():
        raise requests.ConnectionError("The platform can't be reached")
    svc._catalog = down


def labels(result):
    return next(p for p in result["policies"] if p["id"] == "labels")


# --------------------------------------------------------------------------- 1. the package


def test_check_now_keeps_a_package_with_versions_hashes_assignments_and_exceptions(svc, tmp_path):
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    publish(svc, "table-names", "table-names")
    svc.governance_create({"id": "elsewhere", "name": "Other domain", "category": "schema"})
    svc.governance_publish("elsewhere")
    svc.governance_assign("elsewhere", "project", "someone-else")
    until = except_labels(svc)
    out = svc.governance_check_now()
    assert out["offline"] is False and out["package_note"] == "Policies read from the platform; the policy package on this PC was refreshed."
    pkg = json.loads(package_file(svc).read_text())
    assert pkg["format"] == "kratosforge-policy-package" and pkg["project"] == "crm"
    assert pkg["platform"] == {"kind": "local", "name": "platform-prod.sqlite"}
    assert dt.datetime.fromisoformat(pkg["taken_at"]) <= dt.datetime.now(dt.timezone.utc)
    stored = {v["version"]: v["hash"] for v in svc.governance_policy("labels")["versions"] if v["status"] == "published"}
    assert {p["id"]: (p["version"], p["sha256"]) for p in pkg["resolved"]} == {
        "labels": (1, stored[1]), "table-names": (1, pkg["versions"]["table-names@1"]["sha256"])}
    for v in pkg["versions"].values():
        assert v["sha256"] == policy.definition_hash(v["definition"])
    assert "elsewhere" not in pkg["policies"], "only what the project's chain names"
    assert [(a["policy_id"], a["scope"]) for a in pkg["assignments"]] == [("labels", "enterprise"), ("table-names", "enterprise")]
    assert [(e["policy_id"], e["until"]) for e in pkg["exceptions"]] == [("labels", until)]
    assert pkg["sha256"] == pp.package_hash(pkg) and len(pkg["sha256"]) == 64
    # Taken on request too, and written where CI reads it.
    got = svc.governance_package_export(out=str(tmp_path / "ci" / "policies.json"))
    assert got["file"] == str(tmp_path / "ci" / "policies.json") and got["kept"] == str(package_file(svc))
    assert pp.read(tmp_path / "ci" / "policies.json")["sha256"] == got["sha256"]


def test_a_version_that_doesnt_match_its_stored_hash_is_never_packaged(svc):
    publish(svc, "table-names", "table-names")
    resolved = svc.governance_resolve()
    snap = svc._catalog().call("policy_snapshot")
    snap["versions"]["table-names@1"]["definition"]["description"] = "edited in the table"
    with pytest.raises(pp.PackageError, match="doesn't match its stored SHA-256 in the platform"):
        pp.take("crm", {"kind": "local"}, resolved, snap, [])


# --------------------------------------------------------------------------- 2. tamper check


def test_a_changed_package_is_refused_and_never_used(svc, tmp_path):
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    first = svc.governance_check_now()
    good = json.loads(package_file(svc).read_text())
    # Any edit without the hash: the package's own SHA-256 no longer matches.
    bad = json.loads(json.dumps(good))
    bad["resolved"][0]["version"] = 2
    package_file(svc).write_text(json.dumps(bad))
    with pytest.raises(pp.PackageError, match="was changed after it was taken: its SHA-256 is"):
        svc.governance_check_from_package(remember=True)
    # A definition changed and the package hash recomputed: the version's stored SHA-256 still catches it.
    bad = json.loads(json.dumps(good))
    bad["versions"]["labels@1"]["definition"]["severity"] = "low"
    bad["sha256"] = pp.package_hash(bad)
    package_file(svc).write_text(json.dumps(bad))
    with pytest.raises(pp.PackageError, match="version 1 of labels doesn't match its stored SHA-256"):
        svc.governance_check_from_package()
    # Not JSON, or not a package.
    package_file(svc).write_text("{not json")
    with pytest.raises(pp.PackageError, match="can't be read"):
        svc.governance_check_from_package()
    with pytest.raises(pp.PackageError, match="isn't a KratosForge policy package"):
        pp.verify({"sha256": "x"})
    # Offline Check now refuses it too: no result is made from it, and the last one stays.
    unreachable(svc)
    with pytest.raises(pp.PackageError):
        svc.governance_check_now()
    assert svc.governance_last_check()["result"]["checked_at"] == first["checked_at"]


def test_a_package_for_another_project_is_refused(svc, tmp_path):
    publish(svc, "table-names", "table-names")
    svc.governance_check_now()
    pkg = json.loads(package_file(svc).read_text())
    pkg["project"] = "hr"
    pkg["sha256"] = pp.package_hash(pkg)
    (tmp_path / "hr.json").write_text(json.dumps(pkg))
    with pytest.raises(pp.PackageError, match="is for hr, not crm"):
        svc.governance_check_from_package(str(tmp_path / "hr.json"))


# --------------------------------------------------------------------------- 3. offline Check now


def test_check_now_without_the_platform_uses_the_latest_package_and_says_which(svc):
    col = mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    online = svc.governance_check_now()
    taken = json.loads(package_file(svc).read_text())["taken_at"]
    unreachable(svc)
    out = svc.governance_check_now()
    assert out["offline"] is True and out["package"]["file"] == str(package_file(svc))
    assert out["package"]["taken_at"] == taken and out["package"]["age"] == "0 min"
    assert out["package_note"].startswith("Checked offline with the policy package taken 0 min ago")
    assert "the platform on this PC (platform-prod.sqlite)" in out["package_note"]
    assert out["problems"][0].startswith("The platform couldn't be reached, so the policies came from the policy package")
    assert [f["object"] for f in labels(out)["findings"]] == [f["object"] for f in labels(online)["findings"]] == [f"silver.customers#{col}"]
    assert labels(out)["findings"][0]["severity"] == "Error"
    assert svc.governance_last_check()["result"]["checked_at"] == out["checked_at"]


def test_no_package_and_no_platform_says_so(svc):
    unreachable(svc)
    with pytest.raises(requests.ConnectionError):
        svc.governance_check_now()
    with pytest.raises(pp.PackageError, match="no policy package for crm on this PC yet"):
        svc.governance_check_from_package()


def test_the_package_is_refreshed_when_the_platform_answers(svc):
    publish(svc, "table-names", "table-names")
    svc.governance_check_now()
    old = json.loads(package_file(svc).read_text())
    old["taken_at"] = "2026-01-01T00:00:00+00:00"
    old["sha256"] = pp.package_hash(old)
    package_file(svc).write_text(json.dumps(old))
    publish(svc, "sensitive-columns-labelled", "labels")
    svc.governance_check_now()
    new = json.loads(package_file(svc).read_text())
    assert new["taken_at"] > old["taken_at"] and {p["id"] for p in new["resolved"]} == {"table-names", "labels"}


def test_offline_resolution_matches_the_platform_and_drops_an_ended_exception(svc):
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    until = except_labels(svc, days=3)
    svc.governance_check_now()
    pkg = pp.read(package_file(svc))
    online = svc.governance_resolve()
    offline = pp.resolve_offline(pkg)
    assert offline["policies"] == online["policies"] and offline["chain"] == online["chain"]
    assert offline["policies"][0]["exception"]["until"] == until
    later = (dt.date.fromisoformat(until) + dt.timedelta(days=1)).isoformat()
    assert "exception" not in pp.resolve_offline(pkg, today=later)["policies"][0]


def test_age_in_words():
    now = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone.utc)
    assert pp.age("2026-10-28T11:48:00+00:00", now) == "12 min"
    assert pp.age("2026-10-28T07:00:00+00:00", now) == "5 h"
    assert pp.age("2026-10-25T12:00:00+00:00", now) == "3 days"
    assert pp.age("yesterday", now) == "of unknown age"


# --------------------------------------------------------------------------- 4. the CLI


def test_cli_check_prints_findings_and_fails_on_an_unexcepted_error(svc, tmp_path, capsys):
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    publish(svc, "table-names", "table-names")
    svc.governance_package_export(out=str(tmp_path / "policies.json"))
    capsys.readouterr()
    project = str(tmp_path / "crm")
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--project", project, "--package", str(tmp_path / "policies.json")])
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "Checked offline with the policy package taken" in out
    assert "findings: " in out and "(labels v1, " in out
    assert "  Error: silver.customers#" in out and "1 Error findings aren't excepted." in out
    # The latest package on this PC, without --package; --json for tools.
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--json"])
    assert stop.value.code == 1 and json.loads(capsys.readouterr().out)["unexcepted_errors"] == 1
    # An exception covers it: exit 0 once the package carries it.
    except_labels(svc)
    svc.governance_package_export(out=str(tmp_path / "policies.json"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--package", str(tmp_path / "policies.json"), "--project", project])
    assert stop.value.code == 0
    assert "[Excepted until" in capsys.readouterr().out
    # The CLI keeps no result as the app's last check.
    assert svc.governance_last_check()["result"] is None


def test_cli_refuses_a_changed_package_with_exit_2(svc, tmp_path, capsys):
    publish(svc, "table-names", "table-names")
    svc.governance_package_export(out=str(tmp_path / "policies.json"))
    pkg = json.loads((tmp_path / "policies.json").read_text())
    pkg["exceptions"].append({"policy_id": "table-names", "scope": "project", "target": "crm", "until": "2099-01-01"})
    (tmp_path / "policies.json").write_text(json.dumps(pkg))
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--package", str(tmp_path / "policies.json")])
    assert stop.value.code == 2 and "was changed after it was taken" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--project", str(tmp_path / "nowhere")])
    assert stop.value.code == 2 and "isn't a metadata project folder" in capsys.readouterr().out


# --------------------------------------------------------------------------- 5. no platform, Fabric or OneLake call


def test_the_offline_check_and_the_cli_call_nothing_outside_this_pc(svc, tmp_path, monkeypatch, capsys):
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    publish(svc, "data-contract", "contract")
    svc.mesh_save_product({"name": "customer_360", "description": "Customers", "owner": "me", "status": "published",
                           "access": "public", "ports": [{"name": "customers", "entity": "gold.dim_customer"}]})
    svc.governance_package_export(out=str(tmp_path / "policies.json"))
    blocked = _block(monkeypatch)
    out = svc.governance_check_from_package()
    assert out["offline"] and labels(out)["status"] == "findings"
    contract = next(p for p in out["policies"] if p["id"] == "contract")
    reasons = {f["rule_id"]: f["description"] for f in contract["unknown"]}
    assert reasons["contract.freshness"] == "Checked offline from the policy package: the run logs weren't read."
    assert reasons["contract.terms_linked"] == "Checked offline from the policy package: the platform's catalog wasn't read."
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        cli.main(["governance", "check", "--package", str(tmp_path / "policies.json"), "--project", str(tmp_path / "crm")])
    assert stop.value.code == 1
    assert blocked == []


def _block(monkeypatch):
    calls: list[str] = []

    def refuse(what):
        def boom(*a, **k):
            calls.append(what)
            raise AssertionError(f"offline path called {what}")
        return boom

    monkeypatch.setattr(requests.Session, "request", refuse("HTTP"))
    monkeypatch.setattr(client.FabricPlatform, "call", refuse("UDF_Platform"))
    monkeypatch.setattr(client.LocalPlatform, "call", refuse("the platform"))
    monkeypatch.setattr(Service, "_catalog", refuse("the platform"))
    monkeypatch.setattr(Service, "_platform", refuse("the platform"))
    monkeypatch.setattr(Service, "auth", property(refuse("sign-in")))
    monkeypatch.setattr(Service, "fabric", property(refuse("the Fabric API")))
    monkeypatch.setattr(Service, "meta_runs_all", refuse("run logs in OneLake"))
    monkeypatch.setattr(onelake.FileStore, "__init__", refuse("OneLake"))
    return calls
