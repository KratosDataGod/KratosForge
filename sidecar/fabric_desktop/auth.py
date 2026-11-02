"""Delegated sign-in with MSAL: the app only ever acts as the person who signed in.

One interactive sign-in yields two tokens: one for the Fabric REST API and
one for the Storage audience, which OneLake requires. The MSAL token cache is
persisted per user so later runs refresh silently.

By default nobody registers an app: Fabric Desktop signs in through Microsoft's own Fabric
sign-in app (the one Fabric CLI's ``fab auth login`` uses), which every tenant already trusts
for Fabric and OneLake. It asks for each resource's ``/.default``, since that app's access is
set by Microsoft, and signs in through Windows (the Web Account Manager) when it can.
An organization can still name its own app registration (policy file, environment, Settings);
then the app asks for the narrow scopes below, each the first time a feature needs it.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import msal

from . import dpapi, jsonfile
from .leases import _file_lock
from .redact import describe

_log = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/organizations"
# Microsoft's Fabric sign-in app (Fabric CLI's AUTH_DEFAULT_CLIENT_ID): no app registration needed.
MICROSOFT_CLIENT_ID = "5814bfb4-2705-4994-b8d6-39aabeb5eaeb"

# How long a browser sign-in may stay open before the call gives up (the person can sign in again).
# Windows sign-in (WAM) takes no limit; it returns when its window is closed.
SIGN_IN_SECONDS = 600

FABRIC_SCOPES = [
    "https://api.fabric.microsoft.com/Workspace.ReadWrite.All",
    "https://api.fabric.microsoft.com/Item.ReadWrite.All",
]
# Running an item in Fabric (a notebook, the runner, table maintenance), now or on a schedule.
# Asked for the first time you run or schedule something there.
EXECUTE_SCOPES = FABRIC_SCOPES + ["https://api.fabric.microsoft.com/Item.Execute.All"]
STORAGE_SCOPES = ["https://storage.azure.com/user_impersonation"]
# Asked for the first time a feature needs them (one extra consent prompt, as you):
# OneLake shortcuts and data access roles, cloud connections, and finding a person by email.
ONELAKE_SCOPES = ["https://api.fabric.microsoft.com/OneLake.ReadWrite.All"]
# A OneLake write keeps the token it started with until it commits, so a storage token is handed
# out only with at least this long left (HD-034): one that has less is refreshed first.
STORAGE_MIN_SECONDS = 30 * 60
CONNECTION_SCOPES = ["https://api.fabric.microsoft.com/Connection.Read.All"]
# Gateway names on the Data Factory Connections tab (Preview), asked for the first time it opens.
GATEWAY_SCOPES = ["https://api.fabric.microsoft.com/Gateway.Read.All"]
GRAPH_USER_SCOPES = ["https://graph.microsoft.com/User.ReadBasic.All"]
# Finding an Entra ID group by name, and checking which suggested groups exist (Platform > Set up >
# Domains, Access groups). Reads only; it needs an Entra admin's consent, and linking by object ID works without it.
GRAPH_GROUP_SCOPES = ["https://graph.microsoft.com/Group.Read.All"]
# Listing capacities (creating a workspace, the cost view's capacity size).
CAPACITY_SCOPES = ["https://api.fabric.microsoft.com/Capacity.Read.All"]
# Importing Fabric's domains and subdomains (Fabric's admin API). Only a Fabric administrator
# can use it, and only they are asked for it, when they choose Import from Fabric.
TENANT_READ_SCOPES = ["https://api.fabric.microsoft.com/Tenant.Read.All"]
# Creating a domain in Fabric (New domain on Data mesh > Domains). Also a Fabric administrator's
# alone, asked for only when they choose to create one there.
TENANT_WRITE_SCOPES = ["https://api.fabric.microsoft.com/Tenant.ReadWrite.All"]
# Adding someone to a workspace by email (Power BI's API, the same Power BI Service permission
# as Workspace.ReadWrite.All above), so no directory permission is needed for it.
POWERBI_WORKSPACE_SCOPES = ["https://analysis.windows.net/powerbi/api/Workspace.ReadWrite.All"]
# Reading endorsement with Power BI's admin tenant scan (api.powerbi.com, the same Power BI Service
# Tenant.Read.All as above, asked for Power BI's address). Only a Fabric administrator can use it, and
# only they are asked for it, when they choose Scan now on a data product (Preview).
POWERBI_TENANT_READ_SCOPES = ["https://analysis.windows.net/powerbi/api/Tenant.Read.All"]
# Embedding Power BI content in Embed Studio (Preview): the token the embed frame hands to Power BI, and
# reading a report's or dashboard's embed URL, pages and tiles. Reads only. Asked the first time it opens.
POWERBI_EMBED_SCOPES = ["https://analysis.windows.net/powerbi/api/Report.Read.All",
                        "https://analysis.windows.net/powerbi/api/Dashboard.Read.All",
                        "https://analysis.windows.net/powerbi/api/Dataset.Read.All"]
# Queries on a Data warehouse or SQL analytics endpoint (SQL-004): its SQL connection takes a token for
# Azure SQL's resource. Asked for the first time you run a query on a warehouse.
WAREHOUSE_SCOPES = ["https://database.windows.net/user_impersonation"]
# Reading a workspace's monitoring KQL database (WH-19): a Fabric Eventhouse takes a token for
# Kusto's resource. Asked for the first time a warehouse's Monitoring tab reads it.
KUSTO_SCOPES = ["https://kusto.kusto.windows.net/user_impersonation"]


def resource_of(scope: str) -> str:
    """The resource a delegated scope belongs to: ``https://api.fabric.microsoft.com/Item.ReadWrite.All``
    is ``https://api.fabric.microsoft.com``; ``https://analysis.windows.net/powerbi/api/Tenant.Read.All``
    is ``https://analysis.windows.net/powerbi/api``."""
    parts = urlsplit(scope)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"{scope!r} isn't a scope URL")
    path = parts.path.rstrip("/")
    head, _, last = path.rpartition("/")
    path = head if last else path
    return f"{parts.scheme}://{parts.netloc}{path}"


def default_scope(scopes: list[str]) -> str:
    resources = {resource_of(s) for s in scopes}
    if len(resources) != 1:
        raise ValueError(f"One token can't cover several resources: {sorted(resources)}")
    return resources.pop() + "/.default"


def _broker_available() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("pymsalruntime") is not None


def _window_handle() -> int | object:
    """The window sign-in opens over: Fabric Desktop's, which is in front when someone clicks Sign in."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
        if handle:
            return int(handle)
    return msal.PublicClientApplication.CONSOLE_WINDOW_HANDLE


def default_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "FabricDesktop" / "msal_cache.bin"


# A cache file that starts with this is encrypted with Windows DPAPI for the signed-in Windows account.
# Anything else is the plain JSON earlier versions wrote, which is read once and saved encrypted.
DPAPI_HEADER = b"KFDPAPI1\n"


def read_cache(path: Path) -> tuple[str | None, bool]:
    """The cache's JSON, and whether it should be written again encrypted (a plain file on Windows).
    None when there is nothing usable: no file, or one this Windows account can't decrypt."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, False
    if raw.startswith(DPAPI_HEADER):
        if not dpapi.available():
            return None, False
        try:
            return dpapi.unprotect(raw[len(DPAPI_HEADER):]).decode("utf-8"), False
        except (OSError, UnicodeDecodeError) as exc:  # another account or PC, or damaged: sign in again
            _log.warning("The saved sign-in can't be read by this Windows account; signing in again: %s", describe(exc))
            return None, False
    return raw.decode("utf-8", errors="replace"), dpapi.available()


def cache_bytes(text: str) -> bytes:
    """What goes on disk: encrypted for this Windows account on Windows; elsewhere the JSON in a 0600 file."""
    data = text.encode("utf-8")
    return DPAPI_HEADER + dpapi.protect(data) if dpapi.available() else data


def write_cache(path: Path, text: str) -> None:
    """Swap ``text`` in as the cache file, encrypted as cache_bytes() says. A temporary file is written
    and swapped in, so the app and scheduled runs never read half a file. OSError when DPAPI refuses
    (nothing is written then, never a plain file) or the file can't be replaced."""
    data = cache_bytes(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    # Refresh tokens: this user only, from the moment the file exists (POSIX; Windows ignores the mode).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.name != "nt":
        tmp.chmod(0o600)  # a temporary file left by an earlier run keeps its old mode
    jsonfile.replace(tmp, path)


_UNREAD = object()


class SharedTokenCache(msal.SerializableTokenCache):
    """The token cache the app and scheduled runs share, as msal-extensions' PersistedTokenCache does
    it: each change is made under a lock file (``msal_cache.bin.lock``) to the file's latest contents
    and written straight away, and a lookup first re-reads the file if another process changed it. So
    one process never writes back a whole copy that is out of date: a sign-in elsewhere stays.

    A sign-out elsewhere stays too. A process that was already refreshing a token for that account
    when it was signed out would add it back; its accounts the file lost since this process last read
    it (``removed_elsewhere``) are dropped from what it adds, until this process signs in itself."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.lock_path = path.with_name(f"{path.name}.lock")
        self.removed_elsewhere: set[str] = set()
        self.migrate = False  # a plain file from an earlier version was read: write it again encrypted
        self._seen: object = _UNREAD  # the file's _signature() when last read or written
        self._depth = 0
        self._reload()

    def _signature(self) -> tuple[int, int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size  # each write swaps in a new file

    def _accounts(self) -> set[str]:
        return {a.get("home_account_id") or "" for a in msal.TokenCache.search(self, self.CredentialType.ACCOUNT)}

    def _reload(self, strict: bool = False) -> None:
        """Read the file again if it changed since this process last read or wrote it. A read that
        fails (Windows: a virus scan, or a reader with it open) keeps what's in memory, and _seen, so
        the next lookup reads it again; ``strict`` raises the OSError instead."""
        seen = self._signature()
        if seen == self._seen:
            return
        before = self._accounts()
        try:
            text, self.migrate = read_cache(self.path)
        except OSError as exc:
            if strict:
                raise
            _log.warning("Couldn't read the saved sign-in just now; trying again at the next lookup: %s", describe(exc))
            return
        try:
            self.deserialize(text)
        except ValueError:  # a damaged cache file: sign in again rather than fail every call
            self.deserialize(None)
            self.migrate = False
        self._seen = seen
        after = self._accounts()
        self.removed_elsewhere = (self.removed_elsewhere | (before - after)) - after

    @contextmanager
    def locked(self) -> Iterator[bool]:
        """Holds the lock file (once per process: nested calls share it). True for the outermost call,
        which writes the change."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield False
                finally:
                    self._depth -= 1
                return
            with _file_lock(self.lock_path):
                self._depth = 1
                try:
                    self._reload_for_change()
                    yield True
                finally:
                    self._depth = 0

    def _reload_for_change(self) -> None:
        """A change must start from the file's latest contents, or writing it would undo what another
        process wrote (a sign-out). The read is tried for about 1.5 s, as jsonfile.replace waits; then
        the OSError is raised and nothing is changed or written."""
        for attempt in range(jsonfile.REPLACE_ATTEMPTS):
            try:
                self._reload(strict=True)
                return
            except OSError:
                if attempt == jsonfile.REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(jsonfile.REPLACE_BACKOFF * (2 ** attempt))

    def write(self) -> None:
        """Write the cache as it is now; call it holding ``locked()``. OSError as write_cache says."""
        write_cache(self.path, self.serialize())
        self._seen = self._signature()

    def forget_changes(self) -> None:
        """Drop what this process changed but couldn't write: the next lookup reads the file again."""
        self._seen = _UNREAD
        self.has_state_changed = False

    def write_or_retry_later(self) -> None:
        try:
            self.write()
        except OSError as exc:  # keep the token; the change is written with the next one
            self.has_state_changed = True
            _log.warning("Couldn't save the sign-in, so it wasn't updated: %s", describe(exc))

    def search(self, credential_type, target=None, query=None, *, now=None):  # MSAL's signature
        with self._lock:
            if not self._depth:
                self._reload()
            return super().search(credential_type, target=target, query=query, now=now)

    def add(self, event, **kwargs) -> None:  # MSAL's signature
        with self.locked() as outer:
            before = self._accounts()
            super().add(event, **kwargs)
            for home in (self._accounts() - before) & self.removed_elsewhere:
                self.forget(home)
            if outer:
                self.write_or_retry_later()

    def modify(self, credential_type, old_entry, new_key_value_pairs=None) -> None:
        with self.locked() as outer:
            super().modify(credential_type, old_entry, new_key_value_pairs)
            if outer:
                self.write_or_retry_later()

    def forget(self, home: str) -> None:
        """Every entry of the account ``home``, which another process signed out."""
        kinds = (self.CredentialType.ACCESS_TOKEN, self.CredentialType.REFRESH_TOKEN,
                 self.CredentialType.ID_TOKEN, self.CredentialType.ACCOUNT)
        for kind in kinds:
            for entry in list(msal.TokenCache.search(self, kind, query={"home_account_id": home})):
                msal.TokenCache.modify(self, kind, entry)


class SignInError(RuntimeError):
    """Microsoft sign-in returned no token. The message starts with Entra's code (``AADSTS50076``)
    when there is one, so the app can say what happened and what to do; see docs/troubleshooting.md."""

    def __init__(self, code: str, description: str, error: str = ""):
        # ``error`` is the OAuth error (``server_error``, ``temporarily_unavailable``, ...), kept
        # when ``code`` is Entra's AADSTS code, so a passing failure can be told from a refusal.
        self.code, self.description, self.error = code, description, error or code
        super().__init__(f"{code}: {description}" if description else code)


class SignInCancelled(SignInError):
    """The person closed or cancelled the sign-in window: nothing to report."""


def sign_in_error(result: dict) -> SignInError:
    """The error for an MSAL result without a token. A cancel is recognised from the Windows sign-in's
    UserCanceled status or Entra's ``error_subcode=cancel`` (unverified on a live tenant)."""
    description = str(result.get("error_description") or "").strip()
    error = str(result.get("error") or "unknown_error")
    status = str(result.get("_broker_status") or "")
    if "usercancel" in (status + description).replace("_", "").lower() or result.get("error_subcode") == "cancel":
        return SignInCancelled("cancelled", "The sign-in window was closed before signing in.")
    code = re.search(r"\bAADSTS\d+\b", description)
    return SignInError(code.group(0) if code else error,
                       description[code.end():].lstrip(": ").strip() if code and code.start() == 0 else description, error)

class Authenticator:
    def __init__(self, client_id: str = "", cache_path: Path | None = None, interactive: bool = True):
        self.client_id = client_id or MICROSOFT_CLIENT_ID
        self.microsoft = self.client_id == MICROSOFT_CLIENT_ID  # no app registration
        self.cache_path = cache_path or default_cache_path()
        self.interactive = interactive  # False for scheduled runs: nobody is there to sign in
        self._short_lived = False  # the tenant issues tokens with less than STORAGE_MIN_SECONDS to live
        self.cache = SharedTokenCache(self.cache_path)
        if self.cache.migrate:  # a plain file from an earlier version: encrypt it now
            try:
                self._save(force=True)
            except OSError as exc:  # e.g. the folder is read-only: still signed in, try again next save
                _log.warning("Couldn't encrypt the saved sign-in yet: %s", describe(exc))
        # Windows sign-in (WAM) for Microsoft's app, whose broker redirect Microsoft registered.
        # It uses the Windows work account, so there's often no prompt, and it passes
        # "compliant device" Conditional Access. Elsewhere, or if it can't start, the browser.
        self.broker = False
        if self.microsoft and _broker_available():
            try:
                self.app = msal.PublicClientApplication(self.client_id, authority=AUTHORITY, token_cache=self.cache,
                                                        enable_broker_on_windows=True)
                self.broker = True
            except Exception as exc:  # noqa: BLE001  # the broker's runtime failed to load: sign in with the browser
                _log.warning("Windows sign-in broker didn't start; using the browser: %s", describe(exc))
        if not self.broker:
            self.app = msal.PublicClientApplication(self.client_id, authority=AUTHORITY, token_cache=self.cache)

    def scopes_for(self, scopes: list[str]) -> list[str]:
        """What to ask for: the narrow scopes for an organization's own app; for Microsoft's app,
        whose access Microsoft sets, the resource's /.default."""
        return [default_scope(scopes)] if self.microsoft else scopes

    def _save(self, force: bool = False) -> None:
        """Each change is written as it's made (SharedTokenCache). This writes what is left: a plain
        file being encrypted (force), or a change whose write failed."""
        if self.cache.has_state_changed or force:
            try:
                with self.cache.locked():
                    self.cache.write()
            except OSError as exc:
                if force:
                    raise
                _log.warning("Couldn't save the sign-in, so it wasn't updated: %s", describe(exc))

    def _token(self, scopes: list[str], min_seconds: int = 0) -> str:
        scopes = self.scopes_for(scopes)
        result = None
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(scopes, account=accounts[0])
            if (result and "access_token" in result and not self._short_lived
                    and int(result.get("expires_in") or min_seconds) < min_seconds):
                # MSAL hands out a cached token until 5 minutes before it expires. Through Windows
                # sign-in the broker keeps its own cache and may still return the same token.
                fresh = self.app.acquire_token_silent(scopes, account=accounts[0], force_refresh=True)
                if fresh and "access_token" in fresh:
                    # A new token that is still short: the tenant's tokens live shorter than that, so
                    # don't refresh on every call. The same token back (the broker's cache) says nothing.
                    self._short_lived = (fresh["access_token"] != result["access_token"]
                                         and int(fresh.get("expires_in") or min_seconds) < min_seconds)
                    result = fresh
        if not result and not self.interactive:
            # Said to scheduled runs and to AI tools alike (both are headless): never signed in, or expired.
            raise RuntimeError("KratosForge isn't signed in to Fabric, or the sign-in has expired. Open KratosForge "
                               "and sign in, then try again.")
        if not result:
            self.cache.removed_elsewhere.clear()  # signing in here: the account may come back
            if self.broker:  # Windows sign-in returns when its window is closed
                result = self.app.acquire_token_interactive(scopes, parent_window_handle=_window_handle())
            else:
                # A browser sign-in whose tab was closed would otherwise wait forever, holding every
                # engine call behind it.
                try:
                    result = self.app.acquire_token_interactive(scopes, timeout=SIGN_IN_SECONDS)
                except msal.BrowserInteractionTimeoutError:
                    raise SignInError("timeout", f"The sign-in in your browser wasn't finished within "
                                      f"{SIGN_IN_SECONDS // 60} minutes. Sign in again.") from None
        self._save()
        if "access_token" not in result:
            raise sign_in_error(result)
        self._check_tenant(result)
        return result["access_token"]

    def refreshed_token(self, scopes: list[str]) -> str:
        """A new token, not MSAL's cached one: for a call Fabric refused with 401 although the cached
        token hadn't expired (revoked, or the tenant ended the session). Signs in again only the way
        ``_token`` would."""
        accounts = self.app.get_accounts()
        fresh = self.app.acquire_token_silent(self.scopes_for(scopes), account=accounts[0], force_refresh=True) if accounts else None
        if fresh and "access_token" in fresh:
            self._save()
            self._check_tenant(fresh)
            return fresh["access_token"]
        return self._token(scopes)

    def _check_tenant(self, result: dict) -> None:
        """An organization can limit sign-in to its own tenants (policy ``allowed_tenants``). Another
        tenant's account is signed out again before its token is used."""
        from . import app_settings
        tenant = (result.get("id_token_claims") or {}).get("tid") or self.signed_in_tenant()
        if app_settings.tenant_allowed(tenant):
            return
        self.sign_out()
        raise RuntimeError("Your organization only allows signing in with its own work accounts, and this account "
                           "belongs to another organization. Sign in with your work account.")

    def silent_token(self, scopes: list[str]) -> str | None:
        """A token for scopes the user has already consented to, without any prompt; None otherwise."""
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(self.scopes_for(scopes), account=accounts[0]) if accounts else None
        self._save()
        if result and "access_token" in result:
            try:
                self._check_tenant(result)
            except RuntimeError:
                return None  # signed out: the next call that needs sign-in says why
        return result.get("access_token") if result else None

    def fabric_token(self) -> str:
        return self._token(FABRIC_SCOPES)

    def storage_token(self) -> str:
        return self._token(STORAGE_SCOPES, STORAGE_MIN_SECONDS)

    def warehouse_token(self) -> str:
        return self._token(WAREHOUSE_SCOPES)

    def kusto_token(self) -> str:
        return self._token(KUSTO_SCOPES)

    def sign_out(self) -> None:
        """Every account and its tokens, removed in one change under the cache's lock, so a token
        refresh in another process lands before it or after it, never in between. If the file can't be
        written, nothing is signed out and the error says so, rather than seeming to sign out."""
        try:
            with self.cache.locked() as outer:
                for account in self.app.get_accounts():
                    self.app.remove_account(account)
                    self.cache.forget(account.get("home_account_id") or "")
                if outer:
                    self.cache.write()
        except OSError as exc:
            self.cache.forget_changes()
            raise RuntimeError(f"Couldn't sign out, because the saved sign-in couldn't be changed ({describe(exc)}). "
                               "Try again.") from exc

    def signed_in_user(self) -> str | None:
        accounts = self.app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def signed_in_object_id(self) -> str | None:
        """Microsoft Entra object ID of the signed-in account, from the same "<object id>.<tenant id>"."""
        accounts = self.app.get_accounts()
        home = accounts[0].get("home_account_id", "") if accounts else ""
        return home.split(".", 1)[0] if "." in home else None

    def signed_in_tenant(self) -> str | None:
        """Directory (tenant) ID of the signed-in account; MSAL keeps it as "<object id>.<tenant id>"."""
        accounts = self.app.get_accounts()
        home = accounts[0].get("home_account_id", "") if accounts else ""
        return home.split(".", 1)[1] if "." in home else None
