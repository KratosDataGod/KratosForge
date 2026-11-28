"""The deployment record of Import from Ossie (OSSIE-015b, scenarios 15 and 16): one record per confirmed import,
started before the write and finished ok or failed, never quoting a role filter, a member or an expression."""

import copy
import json
import logging

import pytest
import yaml

from fabric_desktop import model_ossie, model_ossie_deployments as deploys
from test_integrations_ossie_redaction import _secured
from test_model_ossie import _ossie, svc  # noqa: F401 - svc is a fixture

FILTER = '[country] = "Ruritania-West-7731"'
UPN = "ana.secret@contoso.example"
MEMBER_ID = "5f0c1d2e-member-id-91ab"
MEASURE = "CALCULATE(SUM('fact_sales'[amount]), 'dim_customer'[city] = \"Zenda-Secret-4417\")"
SECRETS = (FILTER, "Ruritania-West-7731", UPN, MEMBER_ID, "Zenda-Secret-4417")
ACCOUNT = "deployer@contoso.example"


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


class _Auth:
    def signed_in_user(self):
        return ACCOUNT


@pytest.fixture
def signed_in(svc):  # noqa: F811 - the fixture imported above
    svc._auth = _Auth()
    return svc


def _log(svc):
    return svc.root / deploys.FILE


def _lines(svc):
    return [json.loads(line) for line in _log(svc).read_text(encoding="utf-8").splitlines()]


def _secured_file(svc):
    """An Ossie file exported from a model with a secret filter, a member and a measure quoting a secret."""
    bim = _secured()
    bim["model"]["tables"][0]["measures"][1]["expression"] = MEASURE
    svc.fake.bim = bim
    return svc.meta_model_ossie_export("test", "SM_Gold")["yaml"]


def test_a_confirmed_import_is_recorded_started_then_ok(signed_in):
    done = signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    lines = _lines(signed_in)
    assert [line["phase"] for line in lines] == ["started", "finished"]
    assert lines[0]["id"] == lines[1]["id"] == done["deployment_id"]
    [record] = signed_in.ossie_deployments("test")
    assert record["outcome"] == "ok" and record["who"] == ACCOUNT and record["env"] == "test"
    assert record["workspace"] == "WS Test" and record["model"] == "SM_Gold" and record["mode"] == "update"
    assert record["ossie_version"] == model_ossie.OSSIE_VERSION and record["profile"] == "ossie-0.2"
    assert record["counts"] == done["counts"] and record["needs_dax"] == 0 and record["created"] is False
    assert record["at"].endswith("+00:00") and record["finished_at"]
    assert set(record["fidelity"]["counts"]) == set(model_ossie.CLASSES)
    assert "objects" not in record["fidelity"] and "by_class" not in record["fidelity"]


def test_needs_dax_is_a_count(signed_in):
    window = {"name": "ranked", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(amount) OVER (PARTITION BY channel_sk)"}]}}
    done = signed_in.meta_model_ossie_import("test", _ossie(window), "SM_Gold", confirmed=True)
    [record] = signed_in.ossie_deployments("test", "SM_Gold")
    assert record["needs_dax"] == len(done["needs_dax"]) == 1


def test_fabric_raising_is_recorded_failed_and_leaves_the_model_as_it_was(signed_in, monkeypatch):
    before = copy.deepcopy(signed_in.fake.bim)

    def refuse(*a, **k):
        raise RuntimeError("Fabric said 400: the definition is invalid")
    monkeypatch.setattr(signed_in.fake, "save_semantic_model", refuse)
    with pytest.raises(RuntimeError, match="definition is invalid"):
        signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert signed_in.fake.bim == before and signed_in.fake.saved == []
    [record] = signed_in.ossie_deployments("test")
    assert record["outcome"] == "failed" and record["who"] == ACCOUNT
    assert record["error"] == {"class": "RuntimeError", "message": "Fabric said 400: the definition is invalid"}


def test_an_import_that_never_finished_still_says_started(signed_in, monkeypatch):
    real = deploys.finished

    def crash(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(deploys, "finished", crash)
    out = signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert "still says started" in out["record_warning"]
    monkeypatch.setattr(deploys, "finished", real)
    assert [r["outcome"] for r in signed_in.ossie_deployments("test")] == ["started"]


def test_no_record_no_import(signed_in, monkeypatch):
    def full(*a, **k):
        raise OSError("no space")
    monkeypatch.setattr(deploys, "started", full)
    with pytest.raises(RuntimeError, match="deployment record couldn't be written"):
        signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert signed_in.fake.saved == []


@pytest.mark.parametrize("call", ["preview", "unconfirmed"])
def test_the_dry_run_writes_no_record(signed_in, call):
    if call == "preview":
        assert signed_in.meta_model_ossie_preview("test", _ossie(), "SM_Gold")["changes"]
    else:
        assert "confirm" in signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold")
    assert not _log(signed_in).exists() and signed_in.ossie_deployments("test") == []
    assert signed_in.fake.saved == []


@pytest.mark.parametrize("outcome", ["ok", "failed"])
def test_the_record_holds_no_filter_member_expression_or_yaml(signed_in, monkeypatch, outcome):
    text = _secured_file(signed_in)
    assert all(s in text or json.dumps(s)[1:-1] in text for s in SECRETS)  # the file does hold them
    if outcome == "failed":
        def echo(ws, name, parts, *a, **k):  # an error that quotes what it was sent
            raise ValueError(f"Fabric refused {parts['model.bim'][:4000]} for {FILTER} by {UPN} / {MEMBER_ID}")
        monkeypatch.setattr(signed_in.fake, "save_semantic_model", echo)
        with pytest.raises(ValueError):
            signed_in.meta_model_ossie_import("test", text, "SM_Gold", confirmed=True)
    else:
        signed_in.meta_model_ossie_import("test", text, "SM_Gold", confirmed=True)
    raw = _log(signed_in).read_text(encoding="utf-8")
    assert [s for s in SECRETS if s in raw or json.dumps(s)[1:-1] in raw] == []
    assert "datasets" not in raw and "version: " not in raw  # no YAML
    [record] = signed_in.ossie_deployments("test")
    assert record["outcome"] == outcome
    if outcome == "failed":
        assert len(record["error"]["message"]) < deploys.MAX_MESSAGE + 40
        assert record["error"]["class"] == "ValueError"


def test_the_log_is_capped_to_its_newest_lines(signed_in, monkeypatch):
    monkeypatch.setattr(deploys, "TRIM_BYTES", 3000)
    monkeypatch.setattr(deploys, "KEEP_ROWS", 4)
    ids = [signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)["deployment_id"]
           for _ in range(5)]
    lines = _lines(signed_in)
    assert len(lines) < 2 * len(ids)  # trimmed: past TRIM_BYTES only the newest lines stay
    assert _log(signed_in).stat().st_size <= 3000 + max(len(json.dumps(line)) + 1 for line in lines)
    records = signed_in.ossie_deployments("test")
    assert records and [r["id"] for r in records] == [i for i in ids[::-1] if i in {r["id"] for r in records}]
    assert records[0]["id"] == ids[-1] and records[0]["outcome"] == "ok"


def test_a_damaged_line_is_skipped_and_the_rest_read(signed_in):
    signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    with _log(signed_in).open("a", encoding="utf-8") as f:
        f.write('{"id": "half", "phase": "sta\n[1, 2]\n"not an object"\n'
                + json.dumps({"id": "orphan", "phase": "finished", "outcome": "ok"}) + "\n")
    signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    records = signed_in.ossie_deployments("test")
    assert [r["outcome"] for r in records] == ["ok", "ok"]
    assert {r["id"] for r in records}.isdisjoint({"half", "orphan"})


def test_reading_filters_by_environment_model_and_limit(signed_in):
    signed_in.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    signed_in.meta_model_ossie_import("test", _ossie(), "", mode="new", confirmed=True)
    assert [r["model"] for r in signed_in.ossie_deployments("test")] == ["orders", "SM_Gold"]
    assert [r["model"] for r in signed_in.ossie_deployments("test", "SM_Gold")] == ["SM_Gold"]
    assert len(signed_in.ossie_deployments("test", limit=1)) == 1
    assert signed_in.ossie_deployments("prod") == []
    [new] = signed_in.ossie_deployments("test", "orders")
    assert new["mode"] == "new" and new["created"] is True


def test_no_one_signed_in_is_recorded_as_empty(svc):  # noqa: F811
    svc.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert svc.ossie_deployments("test")[0]["who"] == ""


def test_the_reader_is_not_an_rpc_method():
    from fabric_desktop import model_ossie_service, server
    assert "ossie_deployments" not in model_ossie_service.METHODS and "ossie_deployments" not in server.METHODS


def test_a_profile_and_fidelity_summary_take_odd_input():
    assert deploys.fidelity_summary(None) == {"status": "none"}
    summary = deploys.fidelity_summary({"status": "complete", "counts": {"unsupported": 2},
                                        "warnings": [{"severity": "security"}, {"severity": "warning"}, "x"],
                                        "flagged": {"roles": ["role 'A'"]}})
    assert summary["counts"]["unsupported"] == 2 and summary["warnings"] == 2 and summary["security_warnings"] == 1
    assert summary["flagged_roles"] == 1 and "role 'A'" not in json.dumps(summary)
    assert yaml.safe_load(_ossie())["version"] == model_ossie.OSSIE_VERSION
