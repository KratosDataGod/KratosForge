"""What the organization allows App Studio to send to AI models (AS-06), from the IT policy file.

The same file as every other IT setting (app_settings.policy_path(), usually
``%ProgramData%\\FabricDesktop\\policy.json``), read with app_settings.read_policy(), with these keys
(app_settings.APPSTUDIO_KEYS):

    appstudio_models        false: App Studio can't use, add or test a model connection on this PC.
                            Everything else in App Studio (projects, Design, the local preview) still
                            works (default: the person decides)
    appstudio_providers     the providers a connection may use, by id, for example
                            ["azure-openai", "ollama"]; [] allows none (default: any)
    appstudio_hosts         the exact host names an Azure OpenAI or OpenAI-compatible connection may
                            reach, for example ["contoso.openai.azure.com"]; [] allows none of them
                            (default: any https host). Hosted providers keep their fixed address and
                            Ollama only reaches this PC, so this key doesn't apply to them
    appstudio_sample_rows   false: no connection may send sample rows, whatever its consent says;
                            schema and metadata only (default: each connection's consent decides)

They fail closed: a policy file that exists but can't be read turns model connections off, and a key with
the wrong kind of value takes its most restrictive value (no providers, no hosts, no sample rows). The
file is read on every check, so nothing a model, a project or the page sends can change them.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .. import app_settings
from . import providers as prov

KEYS = ("appstudio_models", "appstudio_providers", "appstudio_hosts", "appstudio_sample_rows")
# The providers whose address the person chooses, so the hosts list applies to them.
CHOSEN_HOST = ("azure-openai", "openai-compatible")
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$")
OFF = "Your organization's policy turns off AI model connections in App Studio."


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def current() -> dict[str, Any]:
    """The policy as it applies now: ``enabled``, ``providers`` (None: any), ``hosts`` (None: any),
    ``sample_rows`` and the problems found in the file."""
    data, error = app_settings.read_policy()
    raw = {k: data[k] for k in KEYS if k in data}
    if error:
        return {"enabled": False, "providers": [], "hosts": [], "sample_rows": False, "problems": [error], "set": list(KEYS)}
    out: dict[str, Any] = {"enabled": True, "providers": None, "hosts": None, "sample_rows": True, "problems": [],
                           "set": sorted(raw)}
    if "appstudio_models" in raw:
        if not isinstance(raw["appstudio_models"], bool):
            out["problems"].append("appstudio_models should be true or false, so model connections are off")
            out["enabled"] = False
        else:
            out["enabled"] = raw["appstudio_models"]
    if "appstudio_providers" in raw:
        value = raw["appstudio_providers"]
        if not _strings(value):
            out["problems"].append("appstudio_providers should be a list of provider ids, so no provider is allowed")
            out["providers"] = []
        else:
            unknown = [v for v in value if v not in prov.PROVIDERS]
            if unknown:
                out["problems"].append(f"appstudio_providers names providers App Studio doesn't have: "
                                       f"{', '.join(u[:40] for u in unknown[:5])} (it has {', '.join(prov.PROVIDERS)})")
            out["providers"] = [v for v in value if v in prov.PROVIDERS]
    if "appstudio_hosts" in raw:
        value = raw["appstudio_hosts"]
        if not _strings(value):
            out["problems"].append("appstudio_hosts should be a list of host names, so no chosen address is allowed")
            out["hosts"] = []
        else:
            bad = [v for v in value if not _HOST.fullmatch(v.lower())]
            if bad:
                out["problems"].append(f"appstudio_hosts entries must be exact host names, like contoso.openai.azure.com; "
                                       f"skipped: {', '.join(b[:60] for b in bad[:5])}")
            out["hosts"] = [v.lower() for v in value if _HOST.fullmatch(v.lower())]
    if "appstudio_sample_rows" in raw:
        if not isinstance(raw["appstudio_sample_rows"], bool):
            out["problems"].append("appstudio_sample_rows should be true or false, so no sample rows are sent")
            out["sample_rows"] = False
        else:
            out["sample_rows"] = raw["appstudio_sample_rows"]
    return out


def refusals(conn: dict[str, Any], pol: dict[str, Any] | None = None) -> list[str]:
    """Why the organization's policy doesn't let ``conn`` be saved or used; [] when it does."""
    pol = pol if pol is not None else current()
    if not pol["enabled"]:
        return [OFF]
    out: list[str] = []
    provider = conn.get("provider")
    if pol["providers"] is not None and provider not in pol["providers"]:
        label = prov.PROVIDERS.get(str(provider), {}).get("label", str(provider)[:40])
        out.append(f"Your organization's policy doesn't allow {label} connections"
                   + (f" (allowed: {', '.join(pol['providers'])})." if pol["providers"] else "."))
    if provider in CHOSEN_HOST and pol["hosts"] is not None:
        host = (urlsplit(str(conn.get("endpoint") or "")).hostname or "").lower()
        if host not in pol["hosts"]:
            out.append(f"Your organization's policy doesn't allow models at {host or 'that address'}"
                       + (f" (allowed: {', '.join(pol['hosts'])})." if pol["hosts"] else "."))
    if (conn.get("consent") or {}).get("sample_rows") and not pol["sample_rows"]:
        out.append("Your organization's policy doesn't let App Studio send sample rows to a model; turn sample rows off.")
    return out


def check(conn: dict[str, Any]) -> None:
    """Raise ProviderError when the policy doesn't allow ``conn``."""
    found = refusals(conn)
    if found:
        raise prov.ProviderError(" ".join(found))
