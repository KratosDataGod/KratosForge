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
from pathlib import Path
from urllib.parse import urlsplit

import msal

from .redact import describe

_log = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/organizations"
# Microsoft's Fabric sign-in app (Fabric CLI's AUTH_DEFAULT_CLIENT_ID): no app registration needed.
MICROSOFT_CLIENT_ID = "5814bfb4-2705-4994-b8d6-39aabeb5eaeb"

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
GRAPH_USER_SCOPES = ["https://graph.microsoft.com/User.ReadBasic.All"]
# Listing capacities (creating a workspace, the cost view's capacity size).
CAPACITY_SCOPES = ["https://api.fabric.microsoft.com/Capacity.Read.All"]
# Importing Fabric's domains and subdomains (Fabric's admin API). Only a Fabric administrator
# can use it, and only they are asked for it, when they choose Import from Fabric.
TENANT_READ_SCOPES = ["https://api.fabric.microsoft.com/Tenant.Read.All"]
# Adding someone to a workspace by email (Power BI's API, the same Power BI Service permission
# as Workspace.ReadWrite.All above), so no directory permission is needed for it.
POWERBI_WORKSPACE_SCOPES = ["https://analysis.windows.net/powerbi/api/Workspace.ReadWrite.All"]


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



class SignInError(RuntimeError):
    """Microsoft sign-in returned no token. The message starts with Entra's code (``AADSTS50076``)
    when there is one, so the app can say what happened and what to do; see docs/troubleshooting.md."""

    def __init__(self, code: str, description: str):
        self.code, self.description = code, description
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
                       description[code.end():].lstrip(": ").strip() if code and code.start() == 0 else description)

class Authenticator:
    def __init__(self, client_id: str = "", cache_path: Path | None = None, interactive: bool = True):
        self.client_id = client_id or MICROSOFT_CLIENT_ID
        self.microsoft = self.client_id == MICROSOFT_CLIENT_ID  # no app registration
        self.cache_path = cache_path or default_cache_path()
        self.interactive = interactive  # False for scheduled runs: nobody is there to sign in
        self._short_lived = False  # the tenant issues tokens with less than STORAGE_MIN_SECONDS to live
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            try:
                self.cache.deserialize(self.cache_path.read_text())
            except ValueError:  # a damaged cache file: sign in again rather than fail every call
                pass
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

    def _save(self) -> None:
        if self.cache.has_state_changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Write a temporary file and swap it in: the app and scheduled runs share this cache,
            # and a half-written file would stop every sign-in.
            tmp = self.cache_path.with_name(f"{self.cache_path.name}.{os.getpid()}.tmp")
            tmp.write_text(self.cache.serialize())
            if os.name != "nt":
                tmp.chmod(0o600)  # refresh tokens: this user only
            os.replace(tmp, self.cache_path)

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
            raise RuntimeError("Your Fabric sign-in has expired. Open KratosForge and sign in, "
                               "and scheduled runs will work again.")
        if not result:
            if self.broker:
                result = self.app.acquire_token_interactive(scopes, parent_window_handle=_window_handle())
            else:
                result = self.app.acquire_token_interactive(scopes)
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

    def sign_out(self) -> None:
        for account in self.app.get_accounts():
            self.app.remove_account(account)
        self._save()

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
