"""A person's own settings never loosen what their organization's policy sets (WR-421).

For each restriction (app_settings.POLICY_RESTRICTS) with an on/off or list value, the organization's
policy file, or Group Policy, sets its restrictive value and the person's own app-settings.json its
permissive one: the policy's value is the one that applies, and Settings won't save the other. The keys
come from app_settings, so a new restriction is covered as soon as it is listed; one whose kind or
reader this file doesn't know fails with what to add. Restrictions only the shell reads are checked in
the shell's source: it reads the person's file only for preferences, and lays Group Policy over the
policy file for each. The shell's policy settings are each classified too, as the ADMX template's are
(test_policy_classes.py). The registry and the folders are stand-ins under tmp_path."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fabric_desktop import app_settings, policy_schema, registry_policy
from fabric_desktop.appstudio import policy as appstudio_policy
from fabric_desktop.appstudio.providers import PROVIDERS
from fabric_desktop.integrations import policy as integrations_policy
from fabric_desktop.integrations.registry import PERMISSIONS
from fabric_desktop.receipts import REDACTIONS

from test_policy_loosening import gp, policy  # noqa: F401 - fixtures

REPO = Path(__file__).resolve().parents[2]
SHELL = REPO / "app" / "src-tauri" / "src"
EFFECTS = {"loosens", "restricts", "preference", "neutral"}
DWORD = registry_policy.REG_DWORD
TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

KINDS = {st.key: st.kind for st in policy_schema.settings()}
# Each on/off restriction's restrictive value, as the readers take it when they can't read the policy.
SAFEST = {**registry_policy._safest(), **app_settings.RESTRICTED}
# Each list restriction: a narrow value (one item, since a Group Policy list subkey with none sets
# nothing) and a wide one the person might prefer.
NARROW = {"allowed_tenants": [TENANT], "integrations_max_permissions": ["READ_METADATA"],
          "integrations_extensions": ["notebook-outline"], "receipt_redactions": [next(iter(REDACTIONS))],
          "appstudio_providers": ["ollama"]}
WIDE = {"allowed_tenants": [TENANT, OTHER_TENANT], "integrations_max_permissions": list(PERMISSIONS),
        "integrations_extensions": ["notebook-outline", "another-extension"], "receipt_redactions": [],
        "appstudio_providers": list(PROVIDERS)}
# Where the sidecar's readers put each restriction they read themselves.
INTEGRATION_FIELDS = {"integrations": "enabled", "integrations_max_permissions": "max_permissions",
                      "integrations_extensions": "extensions", "integrations_editors": "editors",
                      "integrations_http": "http"}
APPSTUDIO_FIELDS = {"appstudio_models": "enabled", "appstudio_providers": "providers",
                    "appstudio_sample_rows": "sample_rows"}

SHELL_ONLY = tuple(k for k in app_settings.POLICY_RESTRICTS if k in app_settings.SHELL_KEYS and k not in app_settings.DEFAULTS)
SIDECAR_RESTRICTS = tuple(k for k in app_settings.POLICY_RESTRICTS if k not in SHELL_ONLY)


def _values(key: str) -> tuple[object, object]:
    """(restrictive, permissive) for ``key``."""
    kind = KINDS.get(key)
    if kind == "bool":
        strict = SAFEST.get(key, False)
        assert isinstance(strict, bool), f"{key}: its most restrictive value should be true or false, not {strict!r}"
        return strict, not strict
    if kind == "list":
        if key not in NARROW or key not in WIDE:
            pytest.fail(f"{key} is a list restriction this test has no values for: add a one-item restrictive value "
                        "to NARROW and a wider one to WIDE")
        return NARROW[key], WIDE[key]
    pytest.fail(f"{key} restricts with a {kind!r} value, which this test doesn't handle: add how to set its "
                "restrictive and permissive values to _values")


def _effective(key: str) -> object:
    """The value of ``key`` that applies now, from the reader that enforces it."""
    if key in app_settings.DEFAULTS:
        got = app_settings.load()[key]
        assert got["source"] == "policy", f"{key} should show as set by the organization's policy, not {got['source']}"
        return got["value"]
    if key == "receipt_redactions":
        return integrations_policy.receipt_redactions()
    if key in INTEGRATION_FIELDS:
        return integrations_policy.current()[INTEGRATION_FIELDS[key]]
    if key in APPSTUDIO_FIELDS:
        return appstudio_policy.current()[APPSTUDIO_FIELDS[key]]
    pytest.fail(f"{key} is a restriction this test can't read: add the reader that enforces it to _effective")


def _person_prefers(key: str, value: object) -> None:
    path = app_settings.user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: value}), encoding="utf-8")


def _settings_refuses(key: str, value: object) -> None:
    if key in app_settings.USER_KEYS:
        with pytest.raises(ValueError, match="Your organization sets"):
            app_settings.save({key: value})
        assert _effective(key) == _values(key)[0]


def test_every_restriction_is_checked_here_or_in_the_shell():
    assert set(SIDECAR_RESTRICTS) | set(SHELL_ONLY) == set(app_settings.POLICY_RESTRICTS)
    assert SIDECAR_RESTRICTS and SHELL_ONLY


@pytest.mark.parametrize("key", SIDECAR_RESTRICTS)
def test_the_persons_own_setting_doesnt_loosen_the_policy_files(key, policy, gp):  # noqa: F811
    strict, loose = _values(key)
    policy({key: strict})
    _person_prefers(key, loose)
    assert _effective(key) == strict, f"{key}: the person's {loose!r} applied over the policy file's {strict!r}"
    _settings_refuses(key, loose)


@pytest.mark.parametrize("key", SIDECAR_RESTRICTS)
def test_the_persons_own_setting_doesnt_loosen_group_policys(key, policy, gp):  # noqa: F811
    strict, loose = _values(key)
    if isinstance(strict, bool):
        gp({key: (int(strict), DWORD)})
    else:
        gp({}, {key: strict})
    _person_prefers(key, loose)
    assert _effective(key) == strict, f"{key}: the person's {loose!r} applied over Group Policy's {strict!r}"
    _settings_refuses(key, loose)


def test_the_policy_file_wins_with_every_restriction_set_at_once(policy, gp):  # noqa: F811
    pairs = {key: _values(key) for key in SIDECAR_RESTRICTS}
    policy({key: strict for key, (strict, _) in pairs.items()})
    path = app_settings.user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: loose for key, (_, loose) in pairs.items()}), encoding="utf-8")
    loosened = {key: _effective(key) for key, (strict, _) in pairs.items() if _effective(key) != strict}
    assert not loosened, f"the person's own settings applied over the policy file's: {loosened}"


# ---- the shell (main.rs, engine_folder.rs, policy_registry.rs) and updater.py

def _rust() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(SHELL.glob("*.rs"))}


def _updater() -> str:
    return (SHELL / "updater.py").read_text(encoding="utf-8")


def _shell_policy_keys() -> set[str]:
    """The policy settings the shell reads: Group Policy's table (policy_registry.rs SHELL_KEYS), each
    key read from the policy text in main.rs, engine_folder.rs's KEY, and the policy file's keys
    updater.py reads."""
    rust = _rust()
    keys = set(re.findall(r'\(\s*"([a-z_]+)",\s*Kind::\w+\s*\)', rust["policy_registry.rs"]))
    keys |= set(re.findall(r'\bvalue\.get\("([a-z_]+)"\)', rust["main.rs"]))
    keys |= set(re.findall(r'\bsetting\(policy, "([a-z_]+)"\)', rust["main.rs"]))
    keys |= set(re.findall(r'pub const KEY: &str = "([a-z_]+)"', rust["engine_folder.rs"]))
    keys |= set(re.findall(r'_policy\([^)]*\)\)?\.get\("([a-z_]+)"\)', _updater()))
    return keys


def _keys_read_from_the_persons_file() -> set[str]:
    """The keys the shell and updater.py read from the person's own app-settings.json."""
    keys: set[str] = set()
    for text in (*_rust().values(), _updater()):
        for name in re.findall(r'let (\w+)\s*=[^;]*"app-settings\.json"', text):
            keys |= set(re.findall(rf'\bsetting\(&?{name}, "([a-z_]+)"\)', text))
            keys |= set(re.findall(rf'\b{name}\.get\("([a-z_]+)"\)', text))
        for name in re.findall(r'(\w+) = [^\n]*"app-settings\.json"', text):
            keys |= set(re.findall(rf'_settings\({name}\)\.get\("([a-z_]+)"\)', text))
    return keys


def test_the_shell_reads_every_setting_listed_as_the_shells():
    found = _shell_policy_keys()
    missing = set(app_settings.SHELL_KEYS) - found
    assert not missing, f"app_settings.SHELL_KEYS lists {sorted(missing)}, but this test didn't find the shell reading them"


@pytest.mark.parametrize("key", sorted(_shell_policy_keys()))
def test_every_policy_setting_the_shell_reads_says_what_it_does(key):
    assert key in app_settings.POLICY_KEYS, f"the shell reads {key} from the policy, but app_settings.POLICY_KEYS doesn't list it"
    assert app_settings.policy_effect(key) in EFFECTS, \
        f"{key}: the shell reads it, so list it in LOOSENING, SHELL_LOOSENING, POLICY_RESTRICTS or POLICY_NEUTRAL"
    assert key in app_settings.DEFAULTS or key in app_settings.SHELL_KEYS, \
        f"{key} is read only by the shell: list it in app_settings.SHELL_KEYS, so policy_problems() can report it"


def test_the_shell_reads_the_persons_own_file_only_for_preferences():
    read = _keys_read_from_the_persons_file()
    assert "channel" in read  # the scan finds what it should, so an empty result can't pass by mistake
    assert not [k for k in read if app_settings.policy_effect(k) != "preference"], \
        f"the shell reads {sorted(read)} from the person's app-settings.json: only preferences may come from there"


@pytest.mark.parametrize("key", sorted(k for k in _shell_policy_keys() if k in app_settings.POLICY_RESTRICTS))
def test_the_shell_lays_group_policy_over_the_file_for_each_restriction_it_reads(key):
    registry = _rust()["policy_registry.rs"]
    assert re.search(rf'\(\s*"{key}",\s*Kind::\w+\s*\)', registry), \
        f"the shell reads {key} but its Group Policy table (policy_registry.rs SHELL_KEYS) doesn't, so IT's value there is missed"
    assert key not in _keys_read_from_the_persons_file()
