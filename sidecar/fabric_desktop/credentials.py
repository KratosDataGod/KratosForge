"""Unattended sign-in for the headless agent: a service principal or a managed identity.

The desktop app signs in as you (:class:`fabric_desktop.auth.Authenticator`). An agent has
nobody to sign in, so it uses an app identity instead. Both classes here answer the same
calls the rest of the engine makes on ``Authenticator`` (``fabric_token``, ``storage_token``,
``_token(scopes)``, ``silent_token``, ``signed_in_user``, ``signed_in_tenant``), so every
store, client and mixin works with either without change.

App identities can't hold delegated scopes such as ``Item.ReadWrite.All``; they get what
their roles in each workspace allow. So each request asks for the scope's resource with
``/.default``, and access comes from adding the principal to the workspace (Contributor or
higher) and from the Fabric tenant setting "Service principals can use Fabric APIs".
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import msal

from .auth import AUTHORITY, FABRIC_SCOPES, REFUSED_WAIT, STORAGE_SCOPES, SignInRequired, default_scope, resource_of  # noqa: F401  (re-exported)
from . import http_session
from .redact import describe

_log = logging.getLogger(__name__)


class Credential(Protocol):
    def fabric_token(self) -> str: ...
    def storage_token(self) -> str: ...
    def _token(self, scopes: list[str]) -> str: ...
    def silent_token(self, scopes: list[str]) -> str | None: ...
    def signed_in_user(self) -> str | None: ...
    def signed_in_tenant(self) -> str | None: ...


class _AppCredential:
    """What both app identities share: one token per resource, cached by MSAL until near expiry."""

    tenant_id: str | None = None
    name = "app"
    # The last token request, for the agent's status record (credential_facts.py): when one last
    # succeeded, and (when, error) if the latest failed. Never the token.
    last_token_at: float | None = None
    last_token_error: tuple[float, str] | None = None

    def __init__(self) -> None:
        self._lock = threading.Lock()  # MSAL apps aren't safe to call from several threads at once
        self._refused: tuple[float, str] | None = None  # (when, message): Entra refused the credential itself

    def _acquire(self, resource_scope: str) -> dict[str, Any]:
        raise NotImplementedError

    def _token(self, scopes: list[str]) -> str:
        scope = default_scope(scopes)
        try:
            with self._lock:
                refused = self._refused
                if refused and time.monotonic() - refused[0] < REFUSED_WAIT:
                    raise SignInRequired(refused[1])  # asked again only after REFUSED_WAIT, or a restart
                result = self._acquire(scope)
                if "access_token" not in result:
                    message = refusal(self.name, result)
                    if message:
                        self._refused = (time.monotonic(), message)
                        raise SignInRequired(message)
                    raise RuntimeError(f"{self.name} couldn't get a token for {scope}: "
                                       f"{result.get('error')}: {result.get('error_description')}")
                self._refused = None
        except Exception as exc:
            self.last_token_error = (time.time(), f"{type(exc).__name__}: {exc}")
            raise
        self.last_token_at, self.last_token_error = time.time(), None
        return result["access_token"]

    def fabric_token(self) -> str:
        return self._token(FABRIC_SCOPES)

    def storage_token(self) -> str:
        return self._token(STORAGE_SCOPES)

    def silent_token(self, scopes: list[str]) -> str | None:
        try:
            return self._token(scopes)
        except Exception as exc:
            _log.info("No token for the agent's credential: %s", describe(exc))
            return None

    def signed_in_user(self) -> str | None:
        return self.name

    def signed_in_tenant(self) -> str | None:
        return self.tenant_id


# Entra's codes for an app identity whose credential itself was refused, and what to do about each. These
# don't pass with time: the agent stops asking (REFUSED_WAIT) and says what to change.
_REFUSED = {
    "AADSTS7000222": "its client secret has expired. Create a new secret for the app registration and set it "
                     "in the agent's environment",
    "AADSTS7000215": "its client secret is wrong or was deleted. Set the app registration's current secret in "
                     "the agent's environment",
    "AADSTS700027": "its certificate isn't one the app registration accepts (expired, removed or replaced). "
                    "Upload the agent's current certificate to the app registration, or point "
                    "auth.certificate_path at the one it holds",
    "AADSTS700024": "its certificate's assertion is outside its valid time. Check the machine's clock and the "
                    "certificate's expiry date",
    "AADSTS7000112": "the app registration is disabled in your tenant. Ask an Entra admin to enable it",
    "AADSTS700016": "the app registration isn't in this tenant any more. Check auth.client_id and auth.tenant_id",
    "AADSTS7000229": "the app registration has no service principal in this tenant. Ask an Entra admin to "
                     "create one",
}


def refusal(name: str, result: dict[str, Any]) -> str:
    """The message for an app identity's credential that Entra refused for good; "" for any other
    failure (throttling, an outage), which is tried again as usual."""
    description = str(result.get("error_description") or "")
    for code, what in _REFUSED.items():
        if code in description:
            return (f"Microsoft Entra refused the agent's sign-in as {name} ({code}): {what}. Then restart the "
                    f"agent; until then it doesn't ask Entra again for {REFUSED_WAIT // 60} minutes at a time.")
    return ""


def key_file_open(auth: dict[str, Any]) -> bool:
    """Whether the service principal's key file (``auth.certificate_path``) can be read by every account on
    this machine. The agent docs set it to 640; this is a warning, not a refusal, because a container's
    secret mount is often 0444 or 0644 and holds only the agent's account. Windows ACLs aren't modes: False."""
    path = auth.get("certificate_path") if auth.get("type") == "service_principal" else ""
    if not path or os.name == "nt":
        return False
    try:
        return bool(Path(path).stat().st_mode & 0o004)
    except (OSError, ValueError):
        return False


KEY_FILE_OPEN = ("auth.certificate_path can be read by every account on this machine, and it holds the agent's "
                 "private key: chmod 640 it, owned by root and the agent's group (docs/agent.md)")


def _certificate(path: Path, password: str | None) -> dict[str, Any]:
    """MSAL's client_credential for a certificate: a PEM with the private key and the certificate,
    or a PFX/P12 file."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    data = path.read_bytes()
    pw = password.encode() if password else None
    if path.suffix.lower() in (".pfx", ".p12"):
        key, cert, _ = pkcs12.load_key_and_certificates(data, pw)
        if key is None or cert is None:
            raise ValueError(f"{path} has no private key and certificate")
    else:
        key = serialization.load_pem_private_key(data, pw)
        cert = x509.load_pem_x509_certificate(data)
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return {"private_key": pem_key, "thumbprint": cert.fingerprint(hashes.SHA1()).hex(),  # noqa: S303 (Entra uses SHA-1)
            "public_certificate": cert.public_bytes(serialization.Encoding.PEM).decode()}


class ServicePrincipal(_AppCredential):
    """An Entra app registration signing in as itself, with a certificate (preferred) or a secret."""

    def __init__(self, tenant_id: str, client_id: str, certificate_path: str = "",
                 certificate_password_env: str = "", client_secret_env: str = "",
                 app: Any | None = None) -> None:
        super().__init__()
        if not tenant_id or not client_id:
            raise ValueError("A service principal needs tenant_id and client_id")
        self.tenant_id, self.client_id = tenant_id, client_id
        self.name = f"service principal {client_id}"
        if app is not None:  # tests
            self.app = app
            return
        if certificate_path:
            password = os.environ.get(certificate_password_env) if certificate_password_env else None
            credential: Any = _certificate(Path(certificate_path), password)
        elif client_secret_env:
            credential = os.environ.get(client_secret_env, "")
            if not credential:
                raise ValueError(f"The environment variable {client_secret_env} (the client secret) is empty")
        else:
            raise ValueError("A service principal needs certificate_path or client_secret_env")
        authority = AUTHORITY.rsplit("/", 1)[0] + "/" + tenant_id
        self.app = msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=credential,
                                                      timeout=http_session.DEFAULT_TIMEOUT)

    def _acquire(self, resource_scope: str) -> dict[str, Any]:
        return self.app.acquire_token_for_client(scopes=[resource_scope])


class ManagedIdentity(_AppCredential):
    """The Azure VM's or Arc server's managed identity: no secret on the machine at all."""

    def __init__(self, client_id: str = "", tenant_id: str = "", client: Any | None = None) -> None:
        super().__init__()
        self.tenant_id = tenant_id or None
        self.name = f"managed identity {client_id}" if client_id else "system-assigned managed identity"
        if client is not None:  # tests
            self.client = client
            return
        identity = msal.UserAssignedManagedIdentity(client_id=client_id) if client_id \
            else msal.SystemAssignedManagedIdentity()
        # A session with a timeout: the machine's identity endpoint that doesn't answer fails, not hangs.
        self.client = msal.ManagedIdentityClient(identity, http_client=http_session.new())

    def _acquire(self, resource_scope: str) -> dict[str, Any]:
        return self.client.acquire_token_for_client(resource=resource_scope.removesuffix("/.default"))


def from_config(auth: dict[str, Any]) -> Credential:
    """The credential an agent.toml ``[auth]`` table describes."""
    kind = auth.get("type", "")
    if kind == "service_principal":
        return ServicePrincipal(auth.get("tenant_id", ""), auth.get("client_id", ""),
                                auth.get("certificate_path", ""), auth.get("certificate_password_env", ""),
                                auth.get("client_secret_env", ""))
    if kind == "managed_identity":
        return ManagedIdentity(auth.get("client_id", ""), auth.get("tenant_id", ""))
    if kind == "user":
        from . import app_settings
        from .auth import Authenticator
        return Authenticator(auth.get("client_id", "") or app_settings.value("client_id"),
                             interactive=False)
    raise ValueError('[auth] type is "service_principal", "managed_identity" or "user"')
