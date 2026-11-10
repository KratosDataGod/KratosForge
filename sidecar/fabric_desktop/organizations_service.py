"""Settings > Account > Switch organization, and "Guest of another company?" on the sign-in prompt
(organizations.py). Preview: not yet tried on a live tenant with a guest account."""

from __future__ import annotations

import logging
from typing import Any

from . import app_settings, organizations
from .redact import describe

_log = logging.getLogger(__name__)

METHODS = {"organization_status", "organization_check", "organization_switch"}


class OrganizationsMixin:
    _tables: Any  # Service's table list, read again after a switch
    def _organization_of(self, value: str) -> dict[str, Any] | None:
        """The organization the sign-in setting names, from what this PC already knows (no network)."""
        if not value:
            return None
        value = value.strip().lower()
        tenant_id = getattr(getattr(self, "_auth", None), "tenant_id", None)
        for row in organizations.recent(self.root):  # type: ignore[attr-defined]
            if row["tenant_id"] in (value, tenant_id) or (row["domain"] and row["domain"] == value):
                return row
        domain = "" if organizations.is_tenant_id(value) else value
        return {"tenant_id": tenant_id or (value if not domain else ""), "domain": domain, "name": domain or value}

    def _guest_label(self) -> str:
        """The organization's name when the account is a guest there (the app bar's Guest tag); else ""."""
        auth = getattr(self, "_auth", None)
        if auth is None or not getattr(auth, "is_guest", lambda: False)():
            return ""
        return organizations.label(self._organization_of(str(app_settings.value("sign_in_tenant") or "")))

    def organization_status(self) -> dict[str, Any]:
        """Which organization sign-in goes to, whether IT fixed it, whether the account is a guest there,
        and the organizations used recently on this PC. Reads only; never signs in or calls Microsoft."""
        setting = app_settings.load()["sign_in_tenant"]
        user = home = tenant = None
        guest = False
        try:
            auth = self.auth  # type: ignore[attr-defined]
            user, home, tenant = auth.signed_in_user(), auth.home_tenant(), auth.signed_in_tenant()
            guest = bool(auth.is_guest())
        except Exception as exc:  # noqa: BLE001 - offline or no sign-in yet: the page still shows the choice
            _log.info("Organization status without the sign-in: %s", describe(exc))
        home_domain = user.rsplit("@", 1)[1].lower() if user and "@" in user else ""
        return {"setting": setting["value"], "fixed": setting["source"] == "policy",
                "current": self._organization_of(str(setting["value"] or "")),
                "user": user, "tenant": tenant, "home_tenant": home, "home_domain": home_domain, "guest": guest,
                "recent": [r for r in organizations.recent(self.root)  # type: ignore[attr-defined]
                           if r["tenant_id"] != (home or "")]}

    def organization_check(self, text: str = "") -> dict[str, Any]:
        """Checks a domain or tenant id before anyone signs in: the organization Microsoft has there, or
        a plain reason it can't be used. Sends only that domain or id to Microsoft's public metadata."""
        org = organizations.lookup(text)
        if not app_settings.tenant_allowed(org["tenant_id"]):
            raise PermissionError(f"Your organization only allows signing in to its own tenants, and "
                                  f"{organizations.label(org)} isn't one of them. Ask IT.")
        return org

    def organization_switch(self, tenant: str = "", domain: str = "") -> dict[str, Any]:
        """Signs in to another organization (a guest's host) or back to the account's own (tenant ""). The
        organization is checked first, then Microsoft signs the account in to it. If that sign-in doesn't
        finish, the choice goes back to what it was, so the app is never left pointing at an organization
        it can't reach."""
        current = app_settings.load()["sign_in_tenant"]
        if current["source"] == "policy":
            raise PermissionError("IT chose the organization KratosForge signs in to, so it can't be changed here. "
                                  "To change it, ask IT.")
        org = self.organization_check(tenant) if tenant else None
        if org and domain and not org["domain"]:
            try:
                org = {**org, "domain": organizations.normalize(domain)}
            except ValueError:
                pass  # shown by its tenant id instead
        previous = current["value"] if current["source"] == "user" else ""
        target = org["tenant_id"] if org else ""
        app_settings.save({"sign_in_tenant": target})
        self._tables = None  # another organization's tables
        try:
            self.sign_in()  # type: ignore[attr-defined]
        except BaseException:
            try:
                app_settings.save({"sign_in_tenant": previous})
            except (OSError, ValueError) as exc:
                _log.warning("Couldn't put the sign-in organization back: %s", describe(exc))
            raise
        auth = self.auth  # type: ignore[attr-defined]
        if org and auth.home_tenant() != org["tenant_id"]:
            organizations.remember(self.root, org)  # type: ignore[attr-defined]
        _log.info("Signed in to %s", "another organization" if auth.is_guest() else "the account's own organization")
        return self.organization_status()
