"""The headless agent's app identities (credentials.py): what an agent.toml [auth] table builds, how a
certificate is read, and what a failed token request leaves for the agent's status record.

MSAL is stood in for, so nothing signs in; the certificates are made here."""

import datetime as dt

import msal
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from fabric_desktop import credentials
from fabric_desktop.credentials import ManagedIdentity, ServicePrincipal, from_config

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(scope="module")
def key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kratosforge-agent")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    return key, cert


def _pem(key, cert, password=None):
    enc = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
            + cert.public_bytes(serialization.Encoding.PEM))


class ConfidentialApp:
    """msal.ConfidentialClientApplication: records how it was made."""
    made = []

    def __init__(self, client_id, authority=None, client_credential=None, timeout=None):
        assert timeout, "every sign-in call has a timeout"
        ConfidentialApp.made.append({"client_id": client_id, "authority": authority, "credential": client_credential})

    def acquire_token_for_client(self, scopes):
        return {"access_token": f"token for {scopes[0]}"}


@pytest.fixture
def msal_apps(monkeypatch):
    ConfidentialApp.made = []
    monkeypatch.setattr(msal, "ConfidentialClientApplication", ConfidentialApp)
    return ConfidentialApp.made


# --------------------------------------------------------------------------- certificates

def test_a_pem_with_its_key_gives_msal_the_key_thumbprint_and_certificate(tmp_path, key_and_cert):
    key, cert = key_and_cert
    path = tmp_path / "agent.pem"
    path.write_bytes(_pem(key, cert))
    out = credentials._certificate(path, None)
    assert out["thumbprint"] == cert.fingerprint(hashes.SHA1()).hex()  # noqa: S303 (Entra uses SHA-1)
    assert out["private_key"].startswith("-----BEGIN PRIVATE KEY-----")  # unencrypted PKCS8, in memory only
    assert out["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")


def test_a_password_protected_pem_needs_its_password(tmp_path, key_and_cert):
    key, cert = key_and_cert
    path = tmp_path / "agent.pem"
    path.write_bytes(_pem(key, cert, b"s3cret"))
    assert credentials._certificate(path, "s3cret")["thumbprint"] == cert.fingerprint(hashes.SHA1()).hex()  # noqa: S303
    with pytest.raises((TypeError, ValueError)):
        credentials._certificate(path, None)
    with pytest.raises(ValueError):
        credentials._certificate(path, "wrong")


@pytest.mark.parametrize("suffix", [".pfx", ".P12"])
def test_a_pfx_is_read_with_its_password(tmp_path, key_and_cert, suffix):
    key, cert = key_and_cert
    path = tmp_path / f"agent{suffix}"
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"agent", key, cert, None,
                                                           serialization.BestAvailableEncryption(b"pfx-pw")))
    out = credentials._certificate(path, "pfx-pw")
    assert out["thumbprint"] == cert.fingerprint(hashes.SHA1()).hex()  # noqa: S303
    with pytest.raises(ValueError):
        credentials._certificate(path, "wrong")


def test_a_pfx_without_a_key_is_refused_by_name(tmp_path, key_and_cert):
    _, cert = key_and_cert
    path = tmp_path / "only-cert.pfx"
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"agent", None, None, [cert],
                                                           serialization.NoEncryption()))
    with pytest.raises(ValueError, match="no private key and certificate"):
        credentials._certificate(path, None)


# --------------------------------------------------------------------------- service principals

def test_a_certificate_principal_signs_in_to_its_own_tenant(tmp_path, monkeypatch, key_and_cert, msal_apps):
    key, cert = key_and_cert
    path = tmp_path / "agent.pfx"
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"agent", key, cert, None,
                                                           serialization.BestAvailableEncryption(b"pfx-pw")))
    monkeypatch.setenv("KF_CERT_PW", "pfx-pw")
    sp = from_config({"type": "service_principal", "tenant_id": TENANT, "client_id": CLIENT,
                      "certificate_path": str(path), "certificate_password_env": "KF_CERT_PW",
                      "client_secret_env": "KF_SECRET"})  # the certificate wins over a secret
    assert isinstance(sp, ServicePrincipal)
    made = msal_apps[0]
    assert made["client_id"] == CLIENT
    assert made["authority"] == f"https://login.microsoftonline.com/{TENANT}"  # never /organizations or /common
    assert made["credential"]["thumbprint"] == cert.fingerprint(hashes.SHA1()).hex()  # noqa: S303
    assert sp.signed_in_tenant() == TENANT and sp.signed_in_user() == f"service principal {CLIENT}"
    assert sp.fabric_token() == "token for https://api.fabric.microsoft.com/.default"
    assert sp.storage_token() == "token for https://storage.azure.com/.default"


def test_a_secret_principal_reads_the_secret_from_the_environment_only(monkeypatch, msal_apps):
    monkeypatch.setenv("KF_SECRET", "the-secret")
    sp = from_config({"type": "service_principal", "tenant_id": TENANT, "client_id": CLIENT,
                      "client_secret_env": "KF_SECRET"})
    assert msal_apps[0]["credential"] == "the-secret"
    assert "the-secret" not in repr(vars(sp))  # kept by MSAL, not on the credential
    monkeypatch.setenv("KF_SECRET", "")
    with pytest.raises(ValueError, match="KF_SECRET") as empty:
        from_config({"type": "service_principal", "tenant_id": TENANT, "client_id": CLIENT,
                     "client_secret_env": "KF_SECRET"})
    assert "the-secret" not in str(empty.value)
    monkeypatch.delenv("KF_SECRET")
    with pytest.raises(ValueError, match="KF_SECRET"):
        from_config({"type": "service_principal", "tenant_id": TENANT, "client_id": CLIENT,
                     "client_secret_env": "KF_SECRET"})


@pytest.mark.parametrize("auth, message", [
    ({"tenant_id": TENANT}, "tenant_id and client_id"),
    ({"client_id": CLIENT}, "tenant_id and client_id"),
    ({"tenant_id": TENANT, "client_id": CLIENT}, "certificate_path or client_secret_env"),
])
def test_an_incomplete_principal_is_refused_before_anything_is_read(auth, message, msal_apps):
    with pytest.raises(ValueError, match=message):
        from_config({"type": "service_principal"} | auth)
    assert msal_apps == []


# --------------------------------------------------------------------------- managed identities

class ManagedClient:
    made = []

    def __init__(self, identity, http_client=None):
        ManagedClient.made.append({"identity": identity, "http": http_client})

    def acquire_token_for_client(self, resource):
        return {"access_token": f"mi token for {resource}"}


@pytest.fixture
def mi_clients(monkeypatch):
    ManagedClient.made = []
    monkeypatch.setattr(msal, "ManagedIdentityClient", ManagedClient)
    return ManagedClient.made


def test_a_system_assigned_identity_asks_for_the_resource_without_default(mi_clients):
    mi = from_config({"type": "managed_identity"})
    assert isinstance(mi, ManagedIdentity) and mi.signed_in_user() == "system-assigned managed identity"
    assert isinstance(mi_clients[0]["identity"], msal.SystemAssignedManagedIdentity)
    assert mi.signed_in_tenant() is None
    assert mi.fabric_token() == "mi token for https://api.fabric.microsoft.com"


def test_a_user_assigned_identity_names_its_client_id(mi_clients):
    mi = from_config({"type": "managed_identity", "client_id": CLIENT, "tenant_id": TENANT})
    identity = mi_clients[0]["identity"]
    assert isinstance(identity, msal.UserAssignedManagedIdentity) and CLIENT in str(dict(identity).values())
    assert mi.signed_in_user() == f"managed identity {CLIENT}" and mi.signed_in_tenant() == TENANT


# --------------------------------------------------------------------------- the rest of [auth]

def test_user_sign_in_is_the_desktop_authenticator_without_prompts(monkeypatch):
    made = []

    class Authenticator:
        def __init__(self, client_id, interactive=True):
            made.append((client_id, interactive))
    monkeypatch.setattr("fabric_desktop.auth.Authenticator", Authenticator)
    monkeypatch.setattr("fabric_desktop.app_settings.value", lambda key: "policy-client-id" if key == "client_id" else None)
    from_config({"type": "user", "client_id": CLIENT})
    from_config({"type": "user"})
    assert made == [(CLIENT, False), ("policy-client-id", False)]  # an agent never opens a sign-in window


@pytest.mark.parametrize("kind", ["", "password", "Service_Principal", None])
def test_an_unknown_auth_type_is_refused(kind):
    with pytest.raises(ValueError, match='"service_principal", "managed_identity" or "user"'):
        from_config({"type": kind} if kind is not None else {})


# --------------------------------------------------------------------------- the status record

def test_a_failed_request_is_recorded_without_the_token_and_cleared_by_the_next(monkeypatch):
    answers = [RuntimeError("network down"), {"error": "invalid_client", "error_description": "AADSTS7000215"},
               {"access_token": "tok"}]

    class App:
        def acquire_token_for_client(self, scopes):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
    sp = ServicePrincipal(TENANT, CLIENT, app=App())
    assert sp.silent_token(["https://api.fabric.microsoft.com/Item.ReadWrite.All"]) is None
    assert sp.last_token_error[1] == "RuntimeError: network down" and sp.last_token_at is None
    with pytest.raises(RuntimeError, match="AADSTS7000215"):
        sp.fabric_token()
    assert "AADSTS7000215" in sp.last_token_error[1]
    clock = [credentials.time.monotonic() + credentials.REFUSED_WAIT]  # a refused secret is asked again later
    monkeypatch.setattr(credentials.time, "monotonic", lambda: clock[0])
    assert sp.fabric_token() == "tok"
    assert sp.last_token_error is None and sp.last_token_at is not None


def test_the_base_credential_has_no_way_to_get_a_token():
    with pytest.raises(NotImplementedError):
        credentials._AppCredential()._acquire("https://api.fabric.microsoft.com/.default")
