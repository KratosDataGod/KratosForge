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
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import msal

from . import dpapi, http_session, jsonfile
from .leases import _file_lock
from .redact import describe

_log = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/organizations"
# Seconds MSAL waits for each sign-in call to Entra (MSAL's own default is to wait forever, so a
# proxy that swallows the call hung sign-in with no message). The browser or Windows prompt itself
# isn't bounded by this: only the calls MSAL makes.
SIGN_IN_TIMEOUT = http_session.DEFAULT_TIMEOUT
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
# Which items changed in a workspace and in its Git branch, and their conflicts (Get Status), asked
# for the first time item status opens. Microsoft has no read-only scope for it: this one also lets
# Update from Git run, which the app does only behind a preview, a fresh status check and a confirm.
GIT_STATUS_SCOPES = ["https://api.fabric.microsoft.com/Workspace.GitUpdate.All"]
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
# Listing a workspace's older dataflows (Gen1 and Dataflow Gen2 without CI/CD), which Fabric's items
# API leaves out, before the Estate X-ray removes a staging lakehouse (Power BI's API; on first use).
POWERBI_DATAFLOW_SCOPES = ["https://analysis.windows.net/powerbi/api/Dataflow.Read.All"]
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
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # on disk before the swap, so a power cut can't leave an empty cache in place
        if os.name != "nt":
            tmp.chmod(0o600)  # a temporary file left by an earlier run keeps its old mode
        jsonfile.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # an encrypted copy of the cache isn't left lying next to it
        raise


_UNREAD = object()
# Seconds to wait for another process (the app, a scheduled run, an agent) to let go of the sign-in
# file's lock. One that hangs holding it doesn't hang this one: a new sign-in is kept in memory and
# written with the next change.
LOCK_SECONDS = 15
# After that, seconds before this process waits for it again, so a hung holder costs one wait, not one per call.
BUSY_SECONDS = 60
# The sections MSAL keeps in the cache; each maps a key to one entry (an object).
_SECTIONS = ("AccessToken", "RefreshToken", "IdToken", "Account", "AppMetadata")


def _usable(text: str | None) -> bool:
    """Whether ``text`` is a cache MSAL can read: one JSON object whose known sections are objects of
    objects. Anything else (truncated, another program's file, a hand edit) is treated as no sign-in,
    so the person signs in once rather than every call failing."""
    if not text:
        return True  # empty: no sign-in yet
    import json
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    for section in _SECTIONS:
        entries = data.get(section, {})
        if not isinstance(entries, dict) or not all(isinstance(e, dict) for e in entries.values()):
            return False
    return True


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
        self._busy_until = 0.0  # time.monotonic() before which the file isn't waited for again (_in_memory)
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
            if not _usable(text):
                raise ValueError("not a sign-in cache")
            self.deserialize(text)
        except ValueError:  # a damaged cache file: sign in again rather than fail every call
            _log.warning("The saved sign-in is damaged, so it isn't used; signing in again replaces it")
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
            if time.monotonic() < self._busy_until:
                raise TimeoutError("the saved sign-in was busy a moment ago")
            with _file_lock(self.lock_path, timeout=LOCK_SECONDS):
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

    def search(self, credential_type: Any, target: Any = None, query: Any = None, *,
               now: Any = None) -> Any:  # MSAL's signature
        with self._lock:
            if not self._depth:
                self._reload()
            return super().search(credential_type, target=target, query=query, now=now)

    def add(self, event: Any, **kwargs: Any) -> None:  # MSAL's signature
        try:
            with self.locked() as outer:
                before = self._accounts()
                super().add(event, **kwargs)
                for home in (self._accounts() - before) & self.removed_elsewhere:
                    self.forget(home)
                if outer:
                    self.write_or_retry_later()
        except OSError as exc:  # the file is locked or can't be read just now: keep the sign-in, write it later
            self._in_memory(exc, lambda: super(SharedTokenCache, self).add(event, **kwargs))

    def modify(self, credential_type: Any, old_entry: Any, new_key_value_pairs: Any = None) -> None:
        try:
            with self.locked() as outer:
                super().modify(credential_type, old_entry, new_key_value_pairs)
                if outer:
                    self.write_or_retry_later()
        except OSError as exc:
            self._in_memory(exc, lambda: super(SharedTokenCache, self).modify(credential_type, old_entry, new_key_value_pairs))

    def _in_memory(self, exc: OSError, change: Any) -> None:
        """Make ``change`` in memory only, when the file couldn't be locked or read to make it there:
        the person stays signed in, and the change is written with the next one that can be (_save).
        Never raised to MSAL, which would lose a sign-in the person just finished."""
        _log.warning("Couldn't update the saved sign-in just now, so it's kept in memory and saved later: %s", describe(exc))
        with self._lock:
            change()
            self.has_state_changed = True
            self._busy_until = time.monotonic() + BUSY_SECONDS

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

class SignInRequired(RuntimeError):
    """The saved sign-in can't be used any more: it expired, or it was ended (a password change, an
    admin revoking sessions, a disabled account). Only signing in again fixes it, so nothing retries it.
    A RuntimeError, so callers that already report a sign-in failure report this one the same way."""


# Entra's codes for a refresh token that will never work again, whatever scope it is used for: expired
# by inactivity or a sign-in frequency rule, revoked, or ended by a password change or a disabled account.
# Any other refusal (consent for a new scope, MFA a policy now asks for) is about that request only.
ENDED_CODES = frozenset({"AADSTS50173", "AADSTS50133", "AADSTS50132", "AADSTS700082", "AADSTS70008",
                         "AADSTS70043", "AADSTS50057", "AADSTS50055", "AADSTS50034", "AADSTS50053"})
# Seconds an app identity's refused credential is answered from memory, so an agent whose secret
# expired doesn't ask Entra again on every call (Entra throttles a client that keeps doing so).
REFUSED_WAIT = 300

SIGNED_OUT_HEADLESS = ("KratosForge isn't signed in to Fabric, or the sign-in has expired. Open KratosForge "
                       "and sign in, then try again.")
# Conditional Access refusals of an interactive sign-in that another window straight away can't change:
# a device check, a block by location, app or policy, or multi-factor setup the person hasn't done. After
# one, no other call opens a window for REFUSED_WAIT seconds, unless someone signs in from the app
# (``retry_sign_in``) or the saved sign-in changes: one refusal, not one window per call.
BLOCKED_CODES = frozenset({"AADSTS53000", "AADSTS53001", "AADSTS53002", "AADSTS53003", "AADSTS53004",
                           "AADSTS530003", "AADSTS530032"})
# The ones that check this PC, which only Windows sign-in can show to Entra.
DEVICE_CODES = frozenset({"AADSTS53000", "AADSTS53001", "AADSTS530003"})
BROWSER_NOTE = ("Windows sign-in didn't start, so this sign-in used the browser, which can't show your "
                "organization that this PC is managed.")
SIGNED_OUT_APP = ("Your sign-in to Fabric has expired or was ended (for example by a password change, or "
                  "your organization signing you out). Sign in again.")


def headless_refusal(refusal: dict) -> str:
    """What a scheduled run or AI tool is told when the saved sign-in can't be used without a person:
    signed out or expired, or (``interaction_required``) the organization's rules ask for a step only a
    person can take, such as multi-factor authentication or a check of this PC."""
    if refusal.get("error") != "interaction_required":
        return SIGNED_OUT_HEADLESS
    code = re.search(r"\bAADSTS\d+\b", str(refusal.get("error_description") or ""))
    return ("Your organization's sign-in rules ask for a step only a person can take, such as multi-factor "
            f"authentication or a check of this PC{f' ({code.group(0)})' if code else ''}. Open KratosForge "
            "and sign in, then try again.")


def ended(result: dict | None) -> str:
    """Entra's code when an MSAL refusal says the refresh token is finished for good; "" otherwise."""
    if not result or "error" not in result:
        return ""
    description = str(result.get("error_description") or "")
    code = re.search(r"\bAADSTS\d+\b", description)
    if code and code.group(0) in ENDED_CODES:
        return code.group(0)
    if result.get("error") == "invalid_grant" and result.get("suberror") == "bad_token":
        return "bad_token"
    return ""


class Authenticator:
    def __init__(self, client_id: str = "", cache_path: Path | None = None, interactive: bool = True):
        self.client_id = client_id or MICROSOFT_CLIENT_ID
        self.microsoft = self.client_id == MICROSOFT_CLIENT_ID  # no app registration
        self.cache_path = cache_path or default_cache_path()
        self.interactive = interactive  # False for scheduled runs: nobody is there to sign in
        self._short_lived = False  # the tenant issues tokens with less than STORAGE_MIN_SECONDS to live
        # The cache file as it was when Entra said the saved sign-in is finished (``ended``); None while
        # it isn't. Until the file changes (someone signs in, here or in the app), nothing asks Entra to
        # refresh it again: one refusal, not one per call.
        self._ended_at: object = None
        self._sign_in_lock = threading.Lock()  # one sign-in window at a time; the others wait for it
        # The last interactive refusal in BLOCKED_CODES: (its error, when, the cache file as it was then).
        self._blocked: tuple[SignInError, float, object] | None = None
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
                                                        enable_broker_on_windows=True, timeout=SIGN_IN_TIMEOUT)
                self.broker = True
            except Exception as exc:  # noqa: BLE001 - the broker's runtime failed to load: sign in with the browser
                _log.warning("Windows sign-in broker didn't start; using the browser: %s", describe(exc))
        if not self.broker:
            self.app = msal.PublicClientApplication(self.client_id, authority=AUTHORITY, token_cache=self.cache,
                                                    timeout=SIGN_IN_TIMEOUT)

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

    def _silent(self, scopes: list[str], account: dict, force_refresh: bool = False,
                refusal: dict | None = None) -> dict | None:
        """MSAL's silent lookup, with its refusal kept: a refusal that says the saved sign-in is finished
        drops it (``_end``) and gives None, as no token does. Any other refusal gives None too, and is
        copied into ``refusal`` when one is passed (its ``claims`` say what Conditional Access asks for)."""
        if self._ended():
            return None
        lookup = getattr(self.app, "acquire_token_silent_with_error", None)
        if lookup is None:  # a stand-in without it
            more = {"force_refresh": True} if force_refresh else {}
            return self.app.acquire_token_silent(scopes, account=account, **more)
        result = lookup(scopes, account=account, force_refresh=force_refresh)
        if result and "error" in result:
            code = ended(result)
            if code:
                self._end(account, code)
            elif refusal is not None:
                refusal.update(result)
            return None
        return result

    def _ended(self) -> bool:
        """Whether Entra said the saved sign-in is finished, and nobody has signed in since."""
        if self._ended_at is None:
            return False
        if self.cache._signature() != self._ended_at:
            self._ended_at = None  # the file changed: a sign-in, here or in another process
            return False
        return True

    def _end(self, account: dict, code: str) -> None:
        """Entra refused the saved sign-in for good (``code``): drop the account's tokens, so no caller
        sends one Fabric will refuse or asks Entra to refresh one it won't. The account stays, so the
        app still knows who to sign in again. Logged once with the code, never a token."""
        home = account.get("home_account_id") or ""
        _log.warning("The saved sign-in was refused (%s); signing in again is needed", code)
        try:
            with self.cache.locked() as outer:
                for kind in (self.cache.CredentialType.ACCESS_TOKEN, self.cache.CredentialType.REFRESH_TOKEN):
                    for entry in list(msal.TokenCache.search(self.cache, kind, query={"home_account_id": home})):
                        msal.TokenCache.modify(self.cache, kind, entry)
                if outer:
                    self.cache.write()
        except OSError as exc:  # still refused in memory below: nothing here asks Entra again
            self.cache.forget_changes()
            _log.warning("Couldn't drop the refused sign-in from the saved file: %s", describe(exc))
        self._ended_at = self.cache._signature()

    def _token(self, scopes: list[str], min_seconds: int = 0) -> str:
        scopes = self.scopes_for(scopes)
        result = None
        why: dict = {}
        accounts = self.app.get_accounts()
        if accounts:
            result = self._silent(scopes, accounts[0], refusal=why)
            if (result and "access_token" in result and not self._short_lived
                    and int(result.get("expires_in") or min_seconds) < min_seconds):
                # MSAL hands out a cached token until 5 minutes before it expires. Through Windows
                # sign-in the broker keeps its own cache and may still return the same token.
                fresh = self._silent(scopes, accounts[0], force_refresh=True)
                if fresh and "access_token" in fresh:
                    # A new token that is still short: the tenant's tokens live shorter than that, so
                    # don't refresh on every call. The same token back (the broker's cache) says nothing.
                    self._short_lived = (fresh["access_token"] != result["access_token"]
                                         and int(fresh.get("expires_in") or min_seconds) < min_seconds)
                    result = fresh
                elif self._ended():
                    result = None  # the token in hand is one Entra just said won't be renewed
        if not result and not self.interactive:
            # Said to scheduled runs and to AI tools alike (both are headless): never signed in, or expired.
            raise SignInRequired(headless_refusal(why))
        if not result:
            result = self._sign_in(scopes)
        self._save()
        if "access_token" not in result:
            raise sign_in_error(result)
        self._check_tenant(result)
        return result["access_token"]

    def _sign_in(self, scopes: list[str], refused: str = "", claims: str = "") -> dict:
        """The interactive sign-in. One window at a time: a call that waited for another's sign-in uses
        the token it brought, rather than opening a second window. ``refused``: a token the platform just
        refused, which never counts as one a sign-in brought (it is still in the cache when the refusal
        wasn't one that ends the sign-in, such as a policy now asking for MFA). What Conditional Access
        asked for when it refused the saved sign-in (its claims challenge) goes to the window, so the
        window asks for that step; ``claims`` is one a caller already has (a forced refresh's refusal).
        A refusal in BLOCKED_CODES is answered again without a window."""
        with self._sign_in_lock:
            why: dict = {}
            accounts = self.app.get_accounts()
            if accounts and not self._ended():
                done = self._silent(scopes, accounts[0], refusal=why)
                if done and "access_token" in done and done["access_token"] != refused:
                    return done
            blocked = self._still_blocked()
            if blocked is not None:
                raise blocked
            self.cache.removed_elsewhere.clear()  # signing in here: the account may come back
            asked = why.get("claims") or claims
            more = {"claims_challenge": asked} if isinstance(asked, str) and asked else {}
            if self.broker:  # Windows sign-in returns when its window is closed
                result = self.app.acquire_token_interactive(scopes, parent_window_handle=_window_handle(), **more)
            else:
                # A browser sign-in whose tab was closed would otherwise wait forever, holding every
                # engine call behind it.
                try:
                    result = self.app.acquire_token_interactive(scopes, timeout=SIGN_IN_SECONDS, **more)
                except msal.BrowserInteractionTimeoutError:
                    raise SignInError("timeout", f"The sign-in in your browser wasn't finished within "
                                      f"{SIGN_IN_SECONDS // 60} minutes. Sign in again.") from None
            if result and "access_token" in result:
                self._ended_at = None
                self._blocked = None
            elif result:
                result = self._refused(result)
            return result

    def _refused(self, result: dict) -> dict:
        """An interactive sign-in Entra refused. A device check refused in the browser says that Windows
        sign-in, which could have shown the device, didn't start; a refusal in BLOCKED_CODES is kept."""
        error = sign_in_error(result)
        if error.code in DEVICE_CODES and self.microsoft and not self.broker and sys.platform == "win32":
            result = {**result, "error_description": f"{result.get('error_description') or ''} {BROWSER_NOTE}".strip()}
            error = sign_in_error(result)
        if error.code in BLOCKED_CODES:
            self._blocked = (error, time.monotonic(), self.cache._signature())
        return result

    def _still_blocked(self) -> SignInError | None:
        """The error to answer with, rather than open another window that Conditional Access just refused."""
        if self._blocked is None:
            return None
        error, at, signature = self._blocked
        if time.monotonic() - at > REFUSED_WAIT or self.cache._signature() != signature:
            self._blocked = None
            return None
        return SignInError(error.code, f"{error.description} No new sign-in window was opened: sign in again "
                           "from KratosForge once the cause is fixed.", error.error)

    def retry_sign_in(self) -> None:
        """Someone chose Sign in: open the window even if Conditional Access refused one a moment ago."""
        self._blocked = None

    def refreshed_token(self, scopes: list[str]) -> str:
        """A new token, not MSAL's cached one: for a call Fabric refused with 401 although the cached
        token hadn't expired (revoked, or the tenant ended the session). Never the cached token again:
        when no new one can be had silently, the person signs in again (the app), or SignInRequired
        says to (headless)."""
        accounts = self.app.get_accounts()
        why: dict = {}
        fresh = self._silent(self.scopes_for(scopes), accounts[0], force_refresh=True, refusal=why) if accounts else None
        if fresh and "access_token" in fresh:
            self._save()
            self._check_tenant(fresh)
            return fresh["access_token"]
        if not self.interactive:
            raise SignInRequired(headless_refusal(why))
        cached = self._silent(self.scopes_for(scopes), accounts[0]) if accounts else None
        refused = (cached or {}).get("access_token") or ""  # the token the caller sent: MSAL's cached one
        claims = why.get("claims")
        result = self._sign_in(self.scopes_for(scopes), refused, claims if isinstance(claims, str) else "")
        self._save()
        if "access_token" not in result:
            raise sign_in_error(result)
        self._check_tenant(result)
        return result["access_token"]

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
        result = self._silent(self.scopes_for(scopes), accounts[0]) if accounts else None
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
