"""The AI tools' Export and Import preview withhold security role filters and members (model_ossie_redact).

A READ_METADATA client (an AI agent through the local API or MCP) gets each role's filter expressions and each
member's name and id as a length and keyed fingerprint, in the fidelity report and in the YAML; the app's own Semantic model
tab (the meta_model_ossie_* methods) still shows them in full."""

import hashlib
import json
import logging

import pytest
import yaml

from fabric_desktop import keyed_fingerprint, model_ossie, model_ossie_redact, ossie_validate
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.registry import ReadOnlyAuthorizer, Registry
from fabric_desktop.semantic_parity import _fingerprint
from test_model_ossie import _bim, svc  # noqa: F401 - svc is a fixture

FILTER = '[country] = "Ruritania-West-7731"'
UPN = "ana.secret@contoso.example"
MEMBER_ID = "5f0c1d2e-member-id-91ab"
SECRETS = (FILTER, "Ruritania-West-7731", UPN, MEMBER_ID)
EXPORT = "fabric_desktop_export_semantic_model_ossie"
PREVIEW = "fabric_desktop_preview_ossie_import"


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _secured():
    bim = _bim()
    role = bim["model"]["roles"][0]
    role["tablePermissions"][0]["filterExpression"] = FILTER
    role["members"] = [{"memberName": UPN, "memberId": MEMBER_ID, "identityProvider": "AzureAD"}]
    return bim


def _stash(doc):
    return next(json.loads(e["data"]) for e in doc["custom_extensions"] if e.get("vendor_name") == "POWER_BI")


def _restash(doc, stash):
    doc["custom_extensions"] = [e for e in doc["custom_extensions"] if e.get("vendor_name") != "POWER_BI"] + [
        {"vendor_name": "POWER_BI", "data": json.dumps(stash)}]
    return doc


def _invoke(svc, cap_id, args):
    reg = Registry(ReadOnlyAuthorizer())
    register_core(reg)
    return reg.invoke(svc, cap_id, args, CallContext(Caller("agent", "test"), timeout=60))


def _leaks(result):
    text = json.dumps(result, ensure_ascii=False)
    return [s for s in SECRETS if s in text or json.dumps(s)[1:-1] in text]


@pytest.fixture
def secured(svc):  # noqa: F811 - the fixture imported above
    svc.fake.bim = _secured()
    return svc


def test_export_through_the_tool_quotes_no_filter_or_member_and_says_so(secured):
    out = _invoke(secured, EXPORT, {"env": "test", "name": "SM_Gold"})
    assert _leaks(out) == []
    assert out["note"] == model_ossie_redact.NOTE and "note" not in yaml.safe_load(out["yaml"])
    role = _stash(yaml.safe_load(out["yaml"]))["roles"][0]
    assert role["name"] == "EU only" and role["modelPermission"] == "read"
    assert role["tablePermissions"][0]["name"] == "dim_customer"
    assert role["tablePermissions"][0]["filterExpression"] == _fingerprint(FILTER)
    assert role["members"] == [{"memberName": _fingerprint(UPN), "memberId": _fingerprint(MEMBER_ID),
                                "identityProvider": "AzureAD"}]
    assert any(o["object"] == "role 'EU only'" for o in out["fidelity"]["objects"])


def test_the_withheld_yaml_is_still_valid_ossie_and_converts_back(secured):
    text = _invoke(secured, EXPORT, {"env": "test", "name": "SM_Gold"})["yaml"]
    local = secured.meta_model_ossie_export("test", "SM_Gold")["yaml"]
    assert ossie_validate.validate(text)["status"] == ossie_validate.validate(local)["status"]
    assert ossie_validate.validate(text)["diagnostics"] == ossie_validate.validate(local)["diagnostics"]
    back, _ = model_ossie.to_tmsl(text)
    role = back["model"]["roles"][0]
    assert role["name"] == "EU only" and role["tablePermissions"][0]["name"] == "dim_customer"


def test_the_apps_own_export_still_shows_the_role_in_full(secured):
    local = secured.meta_model_ossie_export("test", "SM_Gold")
    assert "note" not in local
    role = _stash(yaml.safe_load(local["yaml"]))["roles"][0]
    assert role["tablePermissions"][0]["filterExpression"] == FILTER
    assert role["members"][0]["memberName"] == UPN and role["members"][0]["memberId"] == MEMBER_ID
    direct = model_ossie.export(_secured())["yaml"]
    direct_role = _stash(yaml.safe_load(direct))["roles"][0]
    assert direct_role["tablePermissions"][0]["filterExpression"] == FILTER and UPN in direct


def test_preview_through_the_tool_quotes_no_filter_or_member(secured):
    text = model_ossie.export(_secured())["yaml"]
    assert UPN in text  # the file the tool sends has them
    out = _invoke(secured, PREVIEW, {"env": "test", "text": text, "name": "SM_Gold"})
    assert _leaks(out) == [] and out["note"] == model_ossie_redact.NOTE
    assert any(o["object"] == "role 'EU only'" for o in out["fidelity"]["objects"])
    local = secured.meta_model_ossie_preview("test", text, "SM_Gold")
    assert "note" not in local and local["fidelity"] == secured.meta_model_ossie_preview("test", text, "SM_Gold")["fidelity"]


@pytest.mark.parametrize("new_filter", ["", "TRUE()"], ids=["emptied", "changed"])
def test_a_role_that_came_through_changed_names_no_filter_or_member(new_filter):
    bim = _secured()
    doc = yaml.safe_load(model_ossie.export(bim)["yaml"])
    stash = _stash(doc)
    stash["roles"][0]["tablePermissions"][0]["filterExpression"] = new_filter
    stash["roles"][0]["members"] = [{"memberName": "bo.other@contoso.example", "memberId": "id-bo-0001"}]
    doc = _restash(doc, stash)
    rows = [{"object": "role 'EU only'", "kind": "lost", "message": f"filter {FILTER} for {UPN}"}]
    full = model_ossie.fidelity(bim, doc, rows)
    assert "Ruritania-West-7731" in json.dumps(full) and UPN in json.dumps(full)  # the local report quotes them
    out = model_ossie_redact.fidelity(full, bim, doc)
    assert _leaks(out) == [] and "bo.other@contoso.example" not in json.dumps(out)
    role = next(o for o in out["objects"] if o["object"] == "role 'EU only'")
    assert role["class"] == "partially_converted"
    assert any(_fingerprint(FILTER) in c and "dim_customer" in c for c in role["changes"])
    assert "1 member(s) gone" in role["changes"] and "1 member(s) added" in role["changes"]
    first = out["warnings"][0]
    assert first["object"] == "role 'EU only'" and first["severity"] == "security"
    assert first["message"] == "This security role came through changed: " + "; ".join(role["changes"])
    assert [e for e in role["evidence"] if "withheld" in e] == [{"object": "role 'EU only'", "kind": "lost",
                                                                  "withheld": role["evidence"][-1]["withheld"]}]
    assert [m["change"] for m in out["metadata_changes"] if m["object"] == "role 'EU only'"][:len(role["changes"])] \
        == role["changes"]


def test_a_role_changed_on_import_names_no_filter_or_member():
    text = model_ossie.export(_secured())["yaml"]
    back, report = model_ossie.to_tmsl(text)
    role = back["model"]["roles"][0]
    role["tablePermissions"][0]["filterExpression"] = "FALSE()"
    role["members"] = []
    full = model_ossie.fidelity(model_ossie.parse(text), back, report)
    assert UPN in json.dumps(full)
    out = model_ossie_redact.fidelity(full, model_ossie.parse(text), back)
    assert _leaks(out) == []
    obj = next(o for o in out["objects"] if o["object"] == "role 'EU only'")
    assert obj["class"] == "partially_converted" and "1 member(s) gone" in obj["changes"]


def test_the_tools_call_the_withheld_methods():
    class Svc:
        def __init__(self):
            self.calls = []

        def ossie_export_roles_withheld(self, env, name, translate_dax):
            self.calls.append(("export", env, name, translate_dax))
            return {"yaml": "", "counts": {}, "report": []}

        def ossie_preview_roles_withheld(self, env, text, name, mode, remove):
            self.calls.append(("preview", env, text, name, mode, remove))
            return {"changes": [], "report": []}

    svc = Svc()
    _invoke(svc, EXPORT, {"env": "prod", "name": "SM_Gold"})
    _invoke(svc, PREVIEW, {"env": "test", "text": "name: m"})
    assert svc.calls == [("export", "prod", "SM_Gold", True), ("preview", "test", "name: m", "", "update", [])]


def test_a_guessed_member_or_filter_cant_be_confirmed_by_hashing_it(secured):
    """A tool with a list of the org's UPNs, or a directory listing, can't confirm who is in a role: the
    fingerprint is keyed, so a plain sha256 of the right guess appears nowhere in the result."""
    exported = json.dumps(_invoke(secured, EXPORT, {"env": "test", "name": "SM_Gold"}), ensure_ascii=False)
    sent = model_ossie.export(_secured())["yaml"]
    previewed = json.dumps(_invoke(secured, PREVIEW, {"env": "test", "text": sent, "name": "SM_Gold"}),
                           ensure_ascii=False)
    for text in (exported, previewed):
        for value in (UPN, MEMBER_ID, FILTER):
            plain = hashlib.sha256(value.encode()).hexdigest()
            assert plain[:12] not in text and plain not in text
    assert keyed_fingerprint.digest(UPN) in exported


def test_the_key_is_made_once_and_kept(tmp_path, monkeypatch):
    path = tmp_path / "keys" / "fingerprint.key"
    monkeypatch.setattr(keyed_fingerprint, "key_path", lambda: path)
    first = keyed_fingerprint.fingerprint(UPN)
    assert len(path.read_bytes()) == keyed_fingerprint.KEY_BYTES
    keyed_fingerprint._key_for.cache_clear()
    assert keyed_fingerprint.fingerprint(UPN) == first  # read back, not made again
    assert first == f"{len(UPN)} characters, fingerprint {keyed_fingerprint.digest(UPN)}"


def test_another_key_gives_another_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(keyed_fingerprint, "key_path", lambda: tmp_path / "a.key")
    a = keyed_fingerprint.digest(UPN)
    monkeypatch.setattr(keyed_fingerprint, "key_path", lambda: tmp_path / "b.key")
    assert keyed_fingerprint.digest(UPN) != a


def test_a_folder_that_cant_hold_the_key_uses_a_key_for_this_run(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(keyed_fingerprint, "key_path", lambda: blocker / "fingerprint.key")
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.keyed_fingerprint"):
        a = keyed_fingerprint.digest(UPN)
    assert keyed_fingerprint.digest(UPN) == a and a != hashlib.sha256(UPN.encode()).hexdigest()[:12]
    assert "this run only" in caplog.text


def test_a_damaged_key_file_is_not_used_as_a_key(tmp_path, monkeypatch):
    path = tmp_path / "fingerprint.key"
    path.write_bytes(b"short")
    monkeypatch.setattr(keyed_fingerprint, "key_path", lambda: path)
    assert len(keyed_fingerprint.digest(UPN)) == 12
    assert path.read_bytes() == b"short"  # left for someone to look at, never overwritten
