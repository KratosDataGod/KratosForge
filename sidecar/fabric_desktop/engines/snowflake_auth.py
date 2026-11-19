"""Sign-in for a Snowflake connection's SQL API calls (SF-001, Preview; tested against fakes, not a live account).

A connection profile never holds a key or token (connprofiles.py). When a call is made, the credential
comes from where the person keeps it, through the mechanisms Snowflake documents for its SQL API:

    keypair   a JSON Web Token signed on this PC with the user's RSA private key (the key file named by
              its full path, its passphrase, if any, from a secret reference). Snowflake checks it
              against the public key registered on the user (ALTER USER ... SET RSA_PUBLIC_KEY).
              Header X-Snowflake-Authorization-Token-Type: KEYPAIR_JWT.
    pat       a programmatic access token, read from its reference (env:NAME, or credman:TARGET in
              Windows Credential Manager). Header X-Snowflake-Authorization-Token-Type:
              PROGRAMMATIC_ACCESS_TOKEN.

Browser sign-in (oauth-local) and Snowflake CLI connections (cli-connection) are refused here by name
for now: the profile keeps them, and a later increment adds them.

Tokens live in memory for this provider only and are never written anywhere. A key-pair token lasts
under an hour, Snowflake's limit, and is made again shortly before it lapses. No error repeats a
secret, a passphrase or a key: messages say which method failed and what to check.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import connprofiles

JWT_LIFETIME = 59 * 60          # Snowflake refuses a key-pair token that lives longer than an hour
REFRESH_SECONDS = 120           # a token this close to lapsing is made again before it is sent
MAX_KEY_BYTES = 64 * 1024       # a PEM private key is a few KB; anything far bigger isn't one
METHODS = ("keypair", "pat")
TOKEN_TYPE = {"keypair": "KEYPAIR_JWT", "pat": "PROGRAMMATIC_ACCESS_TOKEN"}
_LATER = {"oauth-local": "Browser sign-in to Snowflake", "cli-connection": "A Snowflake CLI connection"}


class SnowflakeAuthError(RuntimeError):
    """A credential couldn't be had; the message says what the person can do. Never holds a secret."""


def jwt_account(account: str) -> str:
    """The account part of a key-pair token's names, as Snowflake documents it: upper case, and for an
    account locator with a region (xy12345.east-us-2.azure), the locator alone."""
    if not connprofiles._SF_ACCOUNT.fullmatch(account or ""):
        raise SnowflakeAuthError("The connection's account isn't a Snowflake account identifier, like myorg-myaccount.")
    return account.split(".", 1)[0].upper()


def public_key_fingerprint(private_key: Any) -> str:
    """``SHA256:`` and the base64 SHA-256 of the public key's DER form, as Snowflake shows it in
    DESC USER (RSA_PUBLIC_KEY_FP)."""
    from cryptography.hazmat.primitives import serialization

    der = private_key.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")


def load_private_key(path: str, passphrase: str | None) -> Any:
    """The RSA private key in ``path`` (PEM, PKCS#8, encrypted or not)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    problem = connprofiles.key_file_problem(path)
    if problem:
        raise SnowflakeAuthError(f"Key-pair sign-in: {problem}.")
    try:
        file = Path(path)
        if file.stat().st_size > MAX_KEY_BYTES:
            raise SnowflakeAuthError("Key-pair sign-in: the private key file is too large to be a key.")
        data = file.read_bytes()
    except OSError:
        raise SnowflakeAuthError("Key-pair sign-in: the private key file can't be read. "
                                 "Check the path in the connection and that you can open the file.") from None
    try:
        key = serialization.load_pem_private_key(data, passphrase.encode("utf-8") if passphrase else None)
    except TypeError:  # encrypted without a passphrase, or a passphrase for an unencrypted key
        raise SnowflakeAuthError("Key-pair sign-in: the private key is encrypted and the connection names no passphrase, "
                                 "or it isn't encrypted and a passphrase was given.") from None
    except ValueError:  # a wrong passphrase, or not a PEM key
        raise SnowflakeAuthError("Key-pair sign-in: the private key couldn't be opened. Check the passphrase, "
                                 "and that the file is a PEM private key (rsa_key.p8).") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise SnowflakeAuthError("Key-pair sign-in: Snowflake takes an RSA key; this file holds another kind.")
    return key


def keypair_token(account: str, user: str, private_key: Any, now: float) -> tuple[str, float]:
    """A key-pair JSON Web Token for ``user`` in ``account``, and when it expires."""
    import jwt

    if not user:
        raise SnowflakeAuthError("Key-pair sign-in needs the Snowflake user name in the connection.")
    qualified = f"{jwt_account(account)}.{user.upper()}"
    expires = int(now) + JWT_LIFETIME
    payload = {"iss": f"{qualified}.{public_key_fingerprint(private_key)}", "sub": qualified,
               "iat": int(now), "exp": expires}
    return jwt.encode(payload, private_key, algorithm="RS256"), float(expires)


def _secret(ref: str, environ: Mapping[str, str] | None, what: str) -> str:
    from .databricks_auth import DatabricksAuthError, resolve_secret

    try:
        return resolve_secret(ref, environ)
    except DatabricksAuthError as exc:  # the same references; the message names the variable or target only
        raise SnowflakeAuthError(f"{what}: {exc}") from None


class TokenProvider:
    """``headers()`` gives the sign-in headers for a SQL API call, making a key-pair token again only
    when it is near expiry. A token Snowflake refused (401) is dropped and never sent again."""

    def __init__(self, account: str, method: str, fetch: Callable[[], tuple[str, float | None]],
                 clock: Callable[[], float] = time.time):
        self.account = account
        self.method = method
        self._fetch = fetch
        self._clock = clock
        self._token = ""
        self._expires: float | None = None
        self._refused: set[str] = set()
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if not self._token or (self._expires is not None and self._clock() >= self._expires - REFRESH_SECONDS):
                token, expires = self._fetch()
                if not token:
                    raise SnowflakeAuthError("No Snowflake credential was produced.")
                if token in self._refused:
                    raise SnowflakeAuthError("Snowflake refused this programmatic access token earlier, so it isn't "
                                             "sent again. Make a new token in Snowsight and save it where the "
                                             "connection's reference points.")
                self._token, self._expires = token, expires
            return self._token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}",
                "X-Snowflake-Authorization-Token-Type": TOKEN_TYPE[self.method]}

    def drop(self, token: str) -> None:
        """Forget ``token``, which Snowflake refused. A key-pair token is made again next time (the
        registered public key may have changed); a PAT is refused until its reference holds a new one."""
        with self._lock:
            if self.method == "pat":
                self._refused.add(token)
            if self._token == token:
                self._token, self._expires = "", None


def provider_for(connection: dict[str, Any], *, environ: Mapping[str, str] | None = None,
                 clock: Callable[[], float] = time.time) -> TokenProvider:
    """The token provider for a Snowflake connection from a connection profile."""
    account = str(connection.get("account") or "")
    jwt_account(account)  # refuse a bad identifier before any secret is read
    cred = connection.get("credential") or {}
    method = cred.get("method")
    if method in _LATER:
        raise SnowflakeAuthError(f"{_LATER[method]} isn't available for Snowflake calls yet. "
                                 "Use key-pair sign-in or a programmatic access token for this connection.")
    if method == "keypair":
        user = str(connection.get("user") or "")
        path = str(cred.get("private_key_file") or "")
        passphrase_ref = cred.get("passphrase")

        def fetch_keypair() -> tuple[str, float | None]:
            passphrase = _secret(passphrase_ref, environ, "Key-pair sign-in") if passphrase_ref else None
            return keypair_token(account, user, load_private_key(path, passphrase), clock())

        return TokenProvider(account, method, fetch_keypair, clock)
    if method == "pat":
        ref = cred.get("secret")
        if not ref:
            raise SnowflakeAuthError("A programmatic access token needs a secret reference (env:NAME or credman:TARGET).")

        def fetch_pat() -> tuple[str, float | None]:
            return _secret(ref, environ, "Programmatic access token"), None

        return TokenProvider(account, method, fetch_pat, clock)
    raise SnowflakeAuthError("The connection has no Snowflake sign-in method. Choose key pair or programmatic access token.")
