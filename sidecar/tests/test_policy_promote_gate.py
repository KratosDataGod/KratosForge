"""GOV-003: Block policies stop promote. The promote gate's reasons gain each finding of a Block policy that
no exception covers, naming the policy and the finding; Warn and Advisory policies never stop it; an exception
lets it go ahead and is named; and when the policies can't be read, a promote to Prod stops (fail closed)
while one to Test only warns."""

import datetime as dt
import json
import shutil

import pytest
import requests

from fabric_desktop.central import core, policy, policy_package as pp
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service, data_dir


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    s = Service(root=data_dir())
    s.meta_init(str(tmp_path / "crm"), "3")
    assert {r["status"] for r in s.meta_run()} == {"ok"}
    s.meta_set_environment("test", target="local")
    s.meta_set_environment("prod", target="local")
    return s


def mark_pii(svc):
    """One column marked PII and labelled internal: the labels policy finds it."""
    path = svc._project().root / "metadata" / "entities" / "silver.customers.toml"
    col = next(iter(svc._project().entities["silver.customers"].columns))
    path.write_text(path.read_text() + f'\n[catalog.columns.{col}]\ntags = ["PII"]\n')
    svc._project().reload()
    assert {r["status"] for r in svc.meta_run()} == {"ok"}  # the change is run, so the last run is green again
    return col


def labels_policy(svc, enforcement):
    svc.governance_template_use("sensitive-columns-labelled", id="labels")
    svc.governance_publish("labels")
    svc.governance_assign("labels", "enterprise", settings={"enforcement": enforcement})


def except_labels(svc, days=30):
    until = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    req = svc.platform_request("exception", "labels", "Masked copy not ready",
                               {"policy": "labels", "scope": "project", "scope_target": "crm", "until": until})
    svc.platform_decide(req["id"], True)
    return until


def unreachable(svc):
    def down():
        raise requests.ConnectionError("The platform can't be reached")
    svc._catalog = down


def policy_errors(check):
    return [e for e in check["errors"] if "policy" in e.lower()]


def to_test(svc):
    """Promote to Test and run it there, so Test's last runs are green for a promote to Prod."""
    result = svc.meta_promote("dev", "test")
    data = svc._project().local_data  # Test reads the same landing files as Dev
    shutil.copytree(data / "dev" / "LH_Bronze" / "Files", data / "test" / "LH_Bronze" / "Files", dirs_exist_ok=True)
    assert {r["status"] for r in svc.meta_run(env="test")} == {"ok"}
    return result


# --------------------------------------------------------------------------- 1. Block stops promote


def test_an_unexcepted_finding_of_a_block_policy_stops_promote_and_names_the_policy_and_the_finding(svc):
    col = mark_pii(svc)
    labels_policy(svc, "block")
    check = svc.meta_promote_check("dev", "test")
    assert len(check["errors"]) == 1
    error = check["errors"][0]
    assert error.startswith('Block policy "Sensitive columns are labelled": ')
    assert f"silver.customers#{col}: " in error
    with pytest.raises(GateError, match="Sensitive columns are labelled") as raised:
        to_test(svc)
    assert f"silver.customers#{col}" in str(raised.value)
    assert svc._store(svc._project(), "test").meta("deployments").height == 0  # nothing was promoted


def test_a_block_policy_with_nothing_found_lets_promote_go_ahead(svc):
    labels_policy(svc, "block")  # no column is marked PII, so nothing is found
    assert svc.meta_promote_check("dev", "test")["errors"] == []
    assert to_test(svc)["version"]


def test_a_block_policy_that_applies_to_prod_only_doesnt_stop_test(svc):
    mark_pii(svc)
    d = policy.BUILTIN_TEMPLATES["sensitive-columns-labelled"]
    svc.governance_create({"id": "prod-labels", "name": "Prod labels", "category": "classification", "severity": "high",
                           "enforcement": "block", "rule": d["rule"], "params": d["params"],
                           "applies_to": {"environments": ["prod"]}})
    svc.governance_publish("prod-labels")
    svc.governance_assign("prod-labels", "enterprise")
    assert policy_errors(svc.meta_promote_check("dev", "test")) == []
    to_test(svc)
    assert [e.split(":")[0] for e in policy_errors(svc.meta_promote_check("test", "prod"))] == ['Block policy "Prod labels"']


# --------------------------------------------------------------------------- 2. Warn and Advisory never stop it


@pytest.mark.parametrize("enforcement", ["warn", "advisory"])
def test_a_warn_or_advisory_policy_never_stops_promote(svc, enforcement):
    mark_pii(svc)
    labels_policy(svc, enforcement)
    assert svc.governance_check_now()["counts"]["to_look_at"] == 1  # Check now still lists the finding
    assert svc.meta_promote_check("dev", "test")["errors"] == []
    to_test(svc)
    assert svc.meta_promote_check("test", "prod")["errors"] == []
    assert svc.meta_promote("test", "prod")["version"]


# --------------------------------------------------------------------------- 3. an exception lets it through


def test_an_exception_covering_the_finding_lets_promote_go_ahead_and_is_named(svc):
    mark_pii(svc)
    labels_policy(svc, "block")
    until = except_labels(svc)
    check = svc.meta_promote_check("dev", "test")
    assert check["errors"] == []
    note = next(n for n in check["notes"] if n.startswith('Block policy "Sensitive columns are labelled"'))
    assert "1 finding let through by an exception" in note and f"Excepted until {until}: Masked copy not ready" in note
    assert check["policy_exceptions"][0]["policy"] == "labels"
    result = to_test(svc)
    excepted = result["policies"]["excepted"]
    assert [e["policy"] for e in excepted] == ["labels"] and len(excepted[0]["findings"]) == 1
    assert excepted[0]["exception"]["until"] == until and excepted[0]["exception"]["reason"] == "Masked copy not ready"


def test_an_ended_exception_no_longer_lets_it_through(svc):
    mark_pii(svc)
    labels_policy(svc, "block")
    except_labels(svc)
    ex = svc.governance_policy("labels")["exceptions"][0]
    svc.governance_end_exception(ex["id"], "The masked copy is live now")
    assert policy_errors(svc.meta_promote_check("dev", "test"))


# --------------------------------------------------------------------------- 4. fail closed


def test_policies_that_cant_be_read_stop_prod_and_warn_on_test(svc):
    labels_policy(svc, "block")
    to_test(svc)
    unreachable(svc)  # and no policy package on this PC
    prod = svc.meta_promote_check("test", "prod")
    assert [e for e in prod["errors"] if e.startswith("The governance policies couldn't be read, so Prod can't be promoted to")]
    assert "The platform can't be reached" in prod["errors"][-1] and "no policy package for crm" in prod["errors"][-1]
    with pytest.raises(GateError, match="governance policies couldn't be read"):
        svc.meta_promote("test", "prod")
    test = svc.meta_promote_check("dev", "test")
    assert test["errors"] == []
    assert any(n.startswith("The governance policies couldn't be read, so they weren't checked") for n in test["notes"])
    assert svc.meta_promote("dev", "test")["version"]


def test_unreachable_platform_uses_the_package_and_a_block_finding_still_stops(svc):
    col = mark_pii(svc)
    labels_policy(svc, "block")
    svc.governance_check_now()  # keeps the package
    unreachable(svc)
    check = svc.meta_promote_check("dev", "test")
    assert [e for e in policy_errors(check) if f"silver.customers#{col}" in e]
    assert any(n.startswith("Block policies: Checked offline with the policy package taken") for n in check["notes"])


def test_unreachable_platform_with_a_package_and_no_findings_lets_prod_go_ahead(svc):
    labels_policy(svc, "block")
    svc.governance_check_now()
    to_test(svc)
    unreachable(svc)
    assert svc.meta_promote_check("test", "prod")["errors"] == []


def test_a_changed_package_is_never_used_so_prod_stops(svc):
    labels_policy(svc, "block")
    svc.governance_check_now()
    to_test(svc)
    path = svc.root / "governance_packages" / "crm.json"
    pkg = json.loads(path.read_text())
    pkg["assignments"] = []  # someone took the Block policy out by hand
    path.write_text(json.dumps(pkg))
    unreachable(svc)
    errors = svc.meta_promote_check("test", "prod")["errors"]
    assert any("was changed after it was taken" in e for e in errors)
    assert svc.meta_promote_check("dev", "test")["errors"] == []


def test_a_platform_that_answers_with_an_error_isnt_stood_in_for_by_the_package(svc, monkeypatch):
    labels_policy(svc, "block")
    svc.governance_check_now()  # a package is on this PC
    to_test(svc)

    def refuse(*_, **__):
        raise core.PlatformError("The platform couldn't resolve policies: refused")
    monkeypatch.setattr(svc, "governance_resolve", refuse)
    errors = svc.meta_promote_check("test", "prod")["errors"]
    assert errors[-1].startswith("The governance policies couldn't be read, so Prod can't be promoted to")
    assert "refused" in errors[-1] and "policy package on this PC can't be used" not in errors[-1]
    assert not any("Checked offline" in n for n in svc.meta_promote_check("dev", "test")["notes"])


def test_no_policies_at_all_changes_nothing(svc):
    check = svc.meta_promote_check("dev", "test")
    assert check["errors"] == [] and check["policy_exceptions"] == []
    assert "policies" not in to_test(svc)


def test_the_gate_keeps_check_nows_last_result_and_takes_no_package(svc):
    mark_pii(svc)
    labels_policy(svc, "block")
    svc.meta_promote_check("dev", "test")
    assert svc.governance_last_check()["result"] is None
    assert not (svc.root / pp.FOLDER / "crm.json").exists()
