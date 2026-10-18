"""Who runs each workload and with what credential (plan items 71, 78, 79): credential_facts.py and workloads/run_as.py."""

import datetime as dt
import json
import types

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from fabric_desktop import audit, credential_facts
from fabric_desktop.credentials import ServicePrincipal
from fabric_desktop.service import Service
from fabric_desktop.workloads.run_as import build

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def certificate(not_after):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(not_after - dt.timedelta(days=365)).not_valid_after(not_after)
            .sign(key, hashes.SHA256()))
    return key, cert


# ---- what an agent publishes about its credential

def test_a_certificate_publishes_its_expiry_and_nothing_else(tmp_path):
    key, cert = certificate(dt.datetime(2027, 2, 1, tzinfo=UTC))
    pem = tmp_path / "agent.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()) + cert.public_bytes(serialization.Encoding.PEM))
    auth = {"type": "service_principal", "tenant_id": "t", "client_id": "c", "certificate_path": str(pem)}
    out = credential_facts.facts(auth, None)
    assert out == {"kind": "certificate", "expires_at": "2027-02-01T00:00:00Z", "last_token_at": None, "last_token_error": None}
    assert str(pem) not in json.dumps(out) and "BEGIN" not in json.dumps(out)


def test_a_password_protected_pfx_is_read_with_its_password(tmp_path, monkeypatch):
    key, cert = certificate(dt.datetime(2027, 3, 1, tzinfo=UTC))
    pfx = tmp_path / "agent.pfx"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b"a", key, cert, None,
                                                          serialization.BestAvailableEncryption(b"pw")))
    monkeypatch.setenv("AGENT_PFX_PW", "pw")
    assert credential_facts.certificate_expiry(str(pfx), "AGENT_PFX_PW") == "2027-03-01T00:00:00Z"
    monkeypatch.setenv("AGENT_PFX_PW", "wrong")
    pfx.touch()
    credential_facts._cache.clear()
    assert credential_facts.certificate_expiry(str(pfx), "AGENT_PFX_PW") is None  # unknown, not an error


def test_a_secret_or_managed_identity_has_no_expiry_to_show():
    assert credential_facts.facts({"type": "service_principal", "client_secret_env": "S"}, None)["expires_at"] is None
    assert credential_facts.facts({"type": "managed_identity"}, None)["kind"] == "managed_identity"


def test_token_requests_are_recorded_without_the_token_or_its_ids():
    class App:
        result: dict = {"access_token": "eyJ-secret"}

        def acquire_token_for_client(self, scopes):
            return self.result
    sp = ServicePrincipal("tenant", "client", app=App())
    sp.fabric_token()
    assert sp.last_token_at and sp.last_token_error is None
    sp.app.result = {"error": "invalid_client", "error_description":
                     "AADSTS7000222: secret expired for app 0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b, chris@contoso.com. "
                     "See https://login.microsoftonline.com/error?code=7000222&tenant=abc"}
    with pytest.raises(RuntimeError):
        sp.storage_token()
    facts = credential_facts.facts({"type": "service_principal", "client_secret_env": "S"}, sp)
    err = facts["last_token_error"]["error"]
    assert "AADSTS7000222" in err and "0f1e2d3c" not in err and "chris@contoso.com" not in err
    assert "login.microsoftonline.com" not in err and "[url]" in err
    assert "eyJ-secret" not in json.dumps(facts)


# ---- the Run as rows

def agent(name, live=True, **kw):
    beat = NOW - (dt.timedelta(seconds=30) if live else dt.timedelta(minutes=20))
    return {"kind": "agent_status", "agent": name, "state": "running", "heartbeat_at": iso(beat), "host": f"{name}-vm",
            "stale_after": iso(beat + dt.timedelta(minutes=3)), **kw}


def test_each_agent_credential_gets_a_state_and_the_urgent_ones_come_first():
    records = {"prod": [
        agent("etl-01", identity="service_principal", credential={"kind": "certificate", "expires_at": iso(NOW + dt.timedelta(days=25)),
                                                                   "last_token_at": iso(NOW), "last_token_error": None}),
        agent("etl-02", live=False, identity="managed_identity",
              credential={"kind": "managed_identity", "expires_at": None, "last_token_at": None,
                          "last_token_error": {"at": iso(NOW), "error": "AuthError: AADSTS700016"}}),
        agent("etl-03", identity="service_principal", credential={"kind": "certificate", "expires_at": iso(NOW - dt.timedelta(days=1))}),
        agent("etl-04", identity="service_principal", credential={"kind": "secret", "last_token_at": iso(NOW)}),
        agent("etl-old", identity="user"),
    ]}
    out = build("chris@contoso.com", records, [], {}, NOW)
    by = {r["place"]["name"]: r for r in out["rows"]}
    assert {n: by[n]["credential"]["state"] for n in ("etl-01", "etl-02", "etl-03", "etl-04", "etl-old")} == \
        {"etl-01": "expiring", "etl-02": "failed", "etl-03": "expired", "etl-04": "ok", "etl-old": "unknown"}
    assert by["etl-02"]["credential"]["error"] == "AuthError: AADSTS700016" and by["etl-02"]["live"] is False
    assert [a["place"]["name"] for a in out["attention"]] == ["etl-03", "etl-02", "etl-01"]
    assert by["etl-01"]["runs_as"] == {"kind": "service_principal", "name": "Service principal"}
    assert "etl-01-vm" in by["etl-01"]["boundary"] and "Prod OneLake" in by["etl-01"]["boundary"]


def test_an_agent_in_several_environments_is_one_row_from_its_newest_record():
    old = agent("etl-01", live=False, identity="service_principal", credential={"kind": "secret"})
    new = agent("etl-01", identity="service_principal", credential={"kind": "secret", "last_token_at": iso(NOW)})
    out = build("", {"test": [old], "prod": [new]}, [], {}, NOW)
    [row] = [r for r in out["rows"] if r["place"]["kind"] == "agent"]
    assert row["live"] and row["credential"]["state"] == "ok" and row["env"] is None
    assert "Test, Prod OneLake" in row["boundary"]


def test_this_pc_is_you_or_signed_out_and_fabric_schedules_run_as_their_owner():
    scheds = [{"enabled": True, "name": "Run all", "workspace_id": "ws-t", "workspace_name": "Sales Test",
               "owner": {"id": "3f2a9c1e-0000-0000-0000-000000000000", "type": "User"}, "text": "Daily at 22:00"},
              {"enabled": True, "name": "No owner", "workspace_id": "ws-t", "workspace_name": "Sales Test"},
              {"enabled": False, "name": "Off", "workspace_id": "ws-t"}]
    out = build("chris@contoso.com", {}, scheds, {"ws-t": "test"}, NOW)
    by = {r["place"]["name"]: r for r in out["rows"]}
    assert by["This PC"]["runs_as"] == {"kind": "you", "name": "chris@contoso.com"}
    assert by["Sales Test · Run all"]["runs_as"] == {"kind": "schedule_owner", "name": "User 3f2a9c1e…"}
    assert by["Sales Test · Run all"]["env"] == "test" and by["Sales Test · Run all"]["credential"]["state"] == "not_visible"
    assert by["Sales Test · No owner"]["runs_as"]["kind"] == "unknown" and "Sales Test · Off" not in by
    signed_out = build("", {}, [], {}, NOW)["rows"][0]
    assert signed_out["runs_as"]["kind"] == "signed_out" and signed_out["credential"]["state"] == "signed_out"
    assert out["attention"] == []


def test_run_as_list_reads_without_calling_fabric(tmp_path):
    svc = Service(root=tmp_path / "app")
    calls = []
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: calls.append("token"), storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    fresh = iso(dt.datetime.now(UTC) + dt.timedelta(minutes=3))
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {"kind": "agent_status", "agent": "etl-01", "heartbeat_at": iso(dt.datetime.now(UTC)), "stale_after": fresh,
         "identity": "managed_identity", "credential": {"kind": "managed_identity", "last_token_at": fresh}}).encode())
    out = svc.run_as_list()
    assert calls == [] and out["sources"]["fabric_schedules"] == {"state": "not_read"}
    assert [r["place"]["name"] for r in out["rows"]] == ["This PC", "Fabric jobs from this app", "etl-01"]
    assert "run_as_list" in audit.READ_ONLY


def test_the_organizations_policy_bounds_this_pc_and_fabric_jobs():
    out = build("chris@contoso.com", {}, [], {}, NOW,
                {"allow_fabric_runs": False, "allowed_tenants": ["t1"], "local_retention_days": 30})
    pc, fabric = out["rows"][0], out["rows"][1]
    assert "kept 30 days" in pc["detail"] and "1 tenant." in pc["detail"]
    assert fabric["allowed"] is False and "Turned off by your organization's policy" in fabric["boundary"]
    assert build("x", {}, [], {}, NOW)["rows"][1]["allowed"] is True
