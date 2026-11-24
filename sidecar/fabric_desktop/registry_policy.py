"""Group Policy in the registry: what the ADMX templates in deploy/policy set, read as policy settings.

The templates write ``HKLM\\Software\\Policies\\KratosBI\\KratosForge`` (policy_schema.REGISTRY_KEY), one
value per setting, named as the policy file's key:

- on/off settings: a DWORD, 1 or 0;
- numbers: a DWORD;
- choices and text: a string;
- lists: a subkey named after the setting, with one string value per item, named 1, 2, 3 and so on
  (read up to the first missing number). A subkey with no items sets nothing.

Only the machine's key is read, never HKCU's: a person can change their own HKCU. A value here wins over
the same key in policy.json (app_settings._policy_file), since Group Policy is the newer way IT manages
a PC. A value of the wrong type, or a name that isn't a policy setting, is left out and reported
(``problems``), and the policy file's value, if any, applies instead; a restriction then takes its most
restrictive value. A key (or a list subkey) that exists but can't be read leaves what it sets unknown
(``read_all``): with the whole key unreadable, each on/off restriction takes its most restrictive value (as
for a policy file that can't be read); app_settings keeps the other settings as they are, refuses changes that
aren't tightening, and applies no setting that widens what KratosForge trusts until the next read succeeds. Structured settings (``platform``,
``incident_sla``) can't be set here. Off Windows nothing is read.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

MAX_ITEMS = 1000  # list items read from one subkey; Group Policy numbers them from 1

# One read of the key: {value name: (data, registry type)} and {list subkey: [its items], or the error
# that kept it from being read}.
Raw = tuple[dict[str, tuple[Any, int]], dict[str, list[Any] | OSError]]
ALL = "*"  # in read_all's unknown set: the whole key couldn't be read
REG_SZ, REG_EXPAND_SZ, REG_DWORD = 1, 2, 4


def _winreg_read() -> Raw:
    """The key's values and its list subkeys. Nothing (no key) when Group Policy sets none."""
    if sys.platform != "win32":  # _reader() never hands this out off Windows; mypy (on Linux) skips the rest
        return {}, {}
    import winreg

    from .policy_schema import REGISTRY_KEY
    values: dict[str, tuple[Any, int]] = {}
    lists: dict[str, list[Any] | OSError] = {}
    view = winreg.KEY_READ | winreg.KEY_WOW64_64KEY  # the 64-bit view, which Group Policy writes
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY, 0, view)
    except FileNotFoundError:
        return values, lists
    with key:
        count_keys, count_values, _ = winreg.QueryInfoKey(key)
        for i in range(count_values):
            name, data, kind = winreg.EnumValue(key, i)
            values[name] = (data, kind)
        for i in range(count_keys):
            sub = winreg.EnumKey(key, i)
            try:  # one subkey that can't be read leaves only its own setting unknown
                with winreg.OpenKey(key, sub, 0, view) as child:
                    items: dict[int, Any] = {}
                    for j in range(min(winreg.QueryInfoKey(child)[1], MAX_ITEMS)):
                        name, data, kind = winreg.EnumValue(child, j)
                        if name.isdigit():  # an item of another type is kept as None: read_all refuses it by name
                            items[int(name)] = data if kind in (REG_SZ, REG_EXPAND_SZ) else None
                    found: list[Any] = []
                    while len(found) + 1 in items:  # 1, 2, 3, ... up to the first gap, as the shell reads them
                        found.append(items[len(found) + 1])
                    lists[sub] = found
            except OSError as exc:
                lists[sub] = exc
    return values, lists


UNREADABLE = ("Until they can, each on/off restriction takes its most restrictive value, as it does when the policy "
              "file can't be read, and settings that widen what KratosForge trusts don't apply. Other settings Group "
              "Policy can manage stay as they are and can only be made stricter here. Sign-in still works. Ask IT if "
              "this doesn't clear.")


def read(reader: Callable[[], Raw] | None = None) -> tuple[dict[str, Any], list[str]]:
    """The settings Group Policy sets, typed by the policy schema, and what was left out and why.
    ({}, []) off Windows or with no key."""
    out, problems, _ = read_all(reader)
    return out, problems


def read_all(reader: Callable[[], Raw] | None = None) -> tuple[dict[str, Any], list[str], frozenset[str]]:
    """``read``, and which settings Group Policy may set but couldn't be read: {ALL} when the key itself
    couldn't, or the names of list subkeys that couldn't. Read again each time, so a passing error clears."""
    reader = reader or _reader()
    if reader is None:
        return {}, [], frozenset()
    from . import policy_schema
    try:
        values, lists = reader()
    except OSError as exc:
        # What it sets is unknown: each on/off restriction takes its most restrictive value, as for a policy file
        # that can't be read (Chris, 2026-10-18: people can still sign in and work, so the tenant list isn't one).
        from .app_settings import RESTRICTED as SAFE
        return ({k: v for k, v in {**SAFE, **_safest()}.items() if not isinstance(v, list)},
                [f"The Group Policy settings couldn't be read ({exc}). {UNREADABLE}"], frozenset({ALL}))
    from .app_settings import RESTRICTED
    strict = {**_safest(), **{k: v for k, v in RESTRICTED.items() if not isinstance(v, list)}}
    kinds = {st.key: st for st in policy_schema.settings() if st.kind != "json"}
    out: dict[str, Any] = {}
    problems: list[str] = []
    unknown: set[str] = set()
    restricted: set[str] = set()  # set to their most restrictive value; a readable subkey can't undo that

    def bad(name: str, why: str) -> None:
        """A value Group Policy sets but that can't be read. A restriction then takes its most restrictive
        value, as a policy file's would: IT meant to set it, and the safe side is the restriction."""
        if name in strict:
            restricted.add(name)
            out[name] = strict[name]
            problems.append(f"Group Policy value {name} {why}, so its most restrictive value applies")
        else:
            problems.append(f"Group Policy value {name} {why}, so it's ignored")

    for name, (data, kind) in sorted(values.items()):
        st = kinds.get(name)
        if st is not None and st.kind == "list" and name in strict:
            bad(name, "should be a subkey of items")
            continue
        if st is None or st.kind == "list":
            problems.append(f"Group Policy value {name[:80]!r} isn't a setting KratosForge reads, so it's ignored")
            continue
        if st.kind in ("bool", "int"):
            if kind != REG_DWORD or not isinstance(data, int) or isinstance(data, bool):
                bad(name, "should be a DWORD")
                continue
            if st.kind == "bool":
                if data not in (0, 1):
                    bad(name, "should be 1 or 0")
                    continue
                out[name] = bool(data)
            else:
                out[name] = data
        else:  # enum, text
            if kind not in (REG_SZ, REG_EXPAND_SZ) or not isinstance(data, str):
                bad(name, "should be a string")
                continue
            out[name] = data
    for name, items in sorted(lists.items()):
        st = kinds.get(name)
        if isinstance(items, OSError) and st is not None and st.kind == "list":
            unknown.add(name)
            if name in strict:
                out[name] = strict[name]
                problems.append(f"Group Policy key {name} couldn't be read ({items}), so its most restrictive value "
                                "applies until it can be read")
            elif name in RESTRICTED:  # no lockout (Chris, 2026-10-18): the file's value or the default applies
                problems.append(f"Group Policy key {name} couldn't be read ({items}), so the restriction it sets may "
                                f"not apply. Until the key can be read, {name} comes from the policy file or its default.")
            else:
                problems.append(f"Group Policy key {name} couldn't be read ({items}), so {name} is kept as it is and "
                                "can't be changed here until the key can be read")
            continue
        if st is None or st.kind != "list" or isinstance(items, OSError):
            problems.append(f"Group Policy key {name[:80]!r} isn't a list setting KratosForge reads, so it's ignored")
            continue
        if name in restricted:  # a bad value under the same name already gave it its most restrictive value
            continue
        kept = [item for item in items if isinstance(item, str)]
        if len(kept) < len(items):  # IT meant to set the items it can't read: the file's rules for a bad list
            if name in strict or not kept:
                bad(name, "has items that aren't strings")
                continue
            problems.append(f"Group Policy key {name} has {len(items) - len(kept)} items that aren't strings, so "
                            "they're left out")
        if not kept:  # a subkey left with no items (Group Policy set to Not Configured) sets nothing
            problems.append(f"Group Policy key {name} has no items, so it sets nothing and the policy file's value applies")
            continue
        out[name] = kept
    return out, problems, frozenset(unknown)


def _safest() -> dict[str, Any]:
    """The most restrictive value of each restriction integrations/policy.py and appstudio/policy.py read, as each
    takes it for a policy file value of the wrong kind: off, nothing allowed, every receipt part left out."""
    from .receipts import REDACTIONS
    return {"integrations": False, "integrations_http": False, "integrations_editors": False,
            "integrations_max_permissions": [], "integrations_extensions": [], "receipt_redactions": list(REDACTIONS),
            "appstudio_models": False, "appstudio_sample_rows": False, "appstudio_providers": [], "appstudio_hosts": []}


def fingerprint(data: dict[str, Any]) -> str | None:
    """A SHA-256 of the settings Group Policy sets, for the record of policy changes; None for none."""
    if not data:
        return None
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# Rights that let someone change Group Policy's values for everyone: set or add values, add subkeys,
# delete the key, or take over its permissions. Tried on the key and on its parent (whose owner could
# replace it).
_KEY_RIGHTS = (0x2, 0x4, 0x10000, 0x40000, 0x80000)  # KEY_SET_VALUE, KEY_CREATE_SUB_KEY, DELETE, WRITE_DAC, WRITE_OWNER
_PARENT_RIGHTS = (0x4, 0x40000, 0x80000)


def user_can_change(opener: Callable[[str, int], bool] | None = None) -> bool:
    """Whether the account running the app could change the Group Policy key, which only administrators
    and SYSTEM should. An administrator, who can change it anyway, isn't counted. Each right is only asked
    for: nothing is written. False off Windows, or with no key."""
    opener = opener or _opener()
    if opener is None:
        return False
    from .policy_schema import REGISTRY_KEY
    parent = REGISTRY_KEY.rsplit("\\", 1)[0]
    if not opener(REGISTRY_KEY, 0x20019):  # KEY_READ: no key, nothing to change
        return False
    return (any(opener(REGISTRY_KEY, right) for right in _KEY_RIGHTS)
            or any(opener(parent, right) for right in _PARENT_RIGHTS))


def _reader() -> Callable[[], Raw] | None:
    """How this PC's Group Policy key is read: None off Windows."""
    return _winreg_read if sys.platform == "win32" else None


def _opener() -> Callable[[str, int], bool] | None:
    """How rights on the key are tried: None off Windows, and for an administrator (counted as able
    to change it anyway, as app_settings.user_can_change counts them)."""
    if sys.platform != "win32":
        return None
    import ctypes
    if ctypes.windll.shell32.IsUserAnAdmin():  # Windows only: mypy (on Linux) skips this after the platform return
        return None
    return _opens


def _opens(path: str, access: int) -> bool:
    if sys.platform != "win32":  # _opener() never hands this out off Windows; mypy (on Linux) skips the rest
        return False
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, access | winreg.KEY_WOW64_64KEY).Close()
        return True
    except OSError:
        return False
