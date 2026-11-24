"""Fuzz Group Policy's registry values (registry_policy.read_all, through its injectable reader): every
restriction, the external AI tools' and App Studio's included (integrations/policy.py, appstudio/policy.py),
given a value of the wrong registry type, out of range, junk, NUL bytes or very long, or a list subkey with
items that aren't strings, takes its most restrictive value with a warning that names it, as a policy file's
would. Nothing raises, and junk never loosens what a valid policy file sets. Seeded, so a failure repeats.
The registry is a stand-in; nothing here touches a real one."""

from __future__ import annotations

import random

import pytest

from fabric_desktop import app_settings, policy_schema, registry_policy
from fabric_desktop.appstudio import policy as appstudio_policy
from fabric_desktop.integrations import policy as integrations_policy

from test_policy_loosening import gp, policy  # noqa: F401 - fixtures

SZ, EXPAND, DWORD = registry_policy.REG_SZ, registry_policy.REG_EXPAND_SZ, registry_policy.REG_DWORD
KINDS = [SZ, EXPAND, DWORD, 0, 3, 7, 11, -1, 2**32]  # NONE, BINARY, MULTI_SZ, QWORD and nonsense
JUNK = [None, True, False, 0, 1, 2, -1, 2**31, 2**32, 2**64, -2**63, 1.5, float("nan"), "", "0", "1", "true",
        "\x00", "1\x00", "a" * 70000, "é\ud800", b"", b"\x01\x00\x00\x00", ["1"], [], ("0",), {"a": 1}]
STRICT = {**{k: v for k, v in app_settings.RESTRICTED.items() if not isinstance(v, list)}, **registry_policy._safest()}
KINDS_OF = {st.key: st.kind for st in policy_schema.settings()}
NAMES = [*app_settings.POLICY_KEYS, "", "x" * 300, "telemetry\x00", "TELEMETRY", "integrations "]
CASES = 120


def _valid(name, data, kind) -> bool:
    if KINDS_OF.get(name) == "bool":
        return kind == DWORD and type(data) is int and data in (0, 1)
    return False  # every other strict key is a list, which is a subkey, never a value


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_junk_group_policy_values_never_raise_or_loosen_a_restriction(seed, policy, gp):  # noqa: F811
    rng = random.Random(seed)
    for case in range(CASES):
        values = {rng.choice(NAMES): (rng.choice(JUNK), rng.choice(KINDS)) for _ in range(rng.randint(0, 12))}
        if rng.random() < 0.5:  # and some well-formed ones among them
            values.update({k: (rng.choice([0, 1]), DWORD) for k in rng.sample(sorted(STRICT), 3) if KINDS_OF[k] == "bool"})
        lists = {rng.choice(NAMES): [rng.choice(JUNK + ["READ_DATA", "ok", "11111111-1111-1111-1111-111111111111"])
                                     for _ in range(rng.randint(0, 4))] for _ in range(rng.randint(0, 6))}
        gp(values, lists)
        label = f"seed {seed} case {case}: {sorted(values)[:6]} {sorted(lists)[:6]}"
        out, problems, unknown = registry_policy.read_all()
        assert not unknown, label
        assert all(isinstance(p, str) and len(p) < 2000 for p in problems), label
        registry_policy.fingerprint(out)  # JSON of what was read: nothing in it that JSON can't hold
        for name, (data, kind) in values.items():
            if name in STRICT and not _valid(name, data, kind):
                assert out[name] == STRICT[name], (label, name, data, kind)
                assert any(p.startswith(f"Group Policy value {name} ") and "most restrictive" in p for p in problems), \
                    (label, name, problems)
        for name, items in lists.items():
            if name in STRICT and KINDS_OF[name] == "list" and items and not all(isinstance(i, str) for i in items):
                assert out[name] == STRICT[name], (label, name, items)
            if name in out and isinstance(out[name], list):
                assert all(isinstance(i, str) for i in out[name]), (label, name)
        app_settings.load()
        app_settings.policy_problems()
        _check_fail_closed(out)


def _check_fail_closed(out) -> None:
    """What integrations/policy.py and appstudio/policy.py make of it is never looser than what was read."""
    integrations, appstudio = integrations_policy.current(), appstudio_policy.current()
    if out.get("integrations") is False:
        assert integrations["enabled"] is False
    if out.get("integrations_editors") is False:
        assert integrations["editors"] is False
    if out.get("integrations_extensions") == []:
        assert integrations["extensions"] == []
    if out.get("integrations_max_permissions") == []:
        assert integrations["max_permissions"] == []
    if out.get("appstudio_models") is False:
        assert appstudio["enabled"] is False
    if out.get("appstudio_sample_rows") is False:
        assert appstudio["sample_rows"] is False
    if out.get("appstudio_providers") == []:
        assert appstudio["providers"] == []
    if "receipt_redactions" in out and out["receipt_redactions"] == STRICT["receipt_redactions"]:
        assert sorted(integrations_policy.receipt_redactions()) == sorted(STRICT["receipt_redactions"])


@pytest.mark.parametrize("key", ["integrations", "integrations_editors", "appstudio_models", "appstudio_sample_rows"])
@pytest.mark.parametrize("data, kind", [("0", SZ), (2, DWORD), (2**32, DWORD), (-1, DWORD), (b"\x00\x00\x00\x00", 3),
                                        (["0"], 7), (None, 0), ("\x00", SZ)])
def test_an_ai_tools_or_app_studio_switch_with_a_bad_value_turns_it_off(key, data, kind, policy, gp):  # noqa: F811
    # Regression: these were ignored ("so it's ignored"), leaving the tools on by default, where the same bad
    # value in policy.json turns them off.
    gp({key: (data, kind)})
    out, problems = registry_policy.read()
    assert out == {key: False}
    assert problems == [problems[0]] and problems[0].startswith(f"Group Policy value {key} ") \
        and "most restrictive value applies" in problems[0]
    enabled = {"integrations": lambda: integrations_policy.current()["enabled"],
               "integrations_editors": lambda: integrations_policy.current()["editors"],
               "appstudio_models": lambda: appstudio_policy.current()["enabled"],
               "appstudio_sample_rows": lambda: appstudio_policy.current()["sample_rows"]}[key]
    assert enabled() is False


def test_junk_in_group_policy_never_loosens_the_policy_files_restriction(policy, gp):  # noqa: F811
    policy({"integrations": False, "appstudio_models": False, "integrations_extensions": ["outline"]})
    gp({"integrations": ("1", SZ), "appstudio_models": (7, DWORD)}, {"integrations_extensions": ["outline", 5, "more"]})
    assert integrations_policy.current()["enabled"] is False
    assert integrations_policy.current()["extensions"] == []
    assert appstudio_policy.current()["enabled"] is False


@pytest.mark.parametrize("key, safest", [("integrations_max_permissions", []), ("integrations_extensions", []),
                                         ("appstudio_providers", [])])
def test_a_restricting_list_with_items_that_arent_strings_allows_nothing(key, safest, policy, gp):  # noqa: F811
    for items in ([None], [1, b"x"], ["READ_DATA", 3]):
        gp({}, {key: items})
        out, problems = registry_policy.read()
        assert out == {key: safest}, items
        assert any(key in p and "aren't strings" in p for p in problems), problems


def test_receipt_redactions_with_junk_items_leave_every_part_out(policy, gp):  # noqa: F811
    gp({}, {"receipt_redactions": ["identity", 7]})
    from fabric_desktop.receipts import REDACTIONS
    assert sorted(integrations_policy.receipt_redactions()) == sorted(REDACTIONS)


def test_a_restricting_list_written_as_a_value_allows_nothing(policy, gp):  # noqa: F811
    gp({"integrations_max_permissions": ("READ_METADATA", SZ), "appstudio_providers": ("ollama", SZ)})
    assert integrations_policy.current()["max_permissions"] == []
    assert appstudio_policy.current()["providers"] == []


def test_a_restricting_list_subkey_that_cant_be_read_allows_nothing(policy, gp):  # noqa: F811
    gp({}, {"integrations_extensions": PermissionError(5, "Access is denied")})
    out, problems, unknown = registry_policy.read_all()
    assert out == {"integrations_extensions": []} and unknown == {"integrations_extensions"}
    assert integrations_policy.current()["extensions"] == []
    assert any("integrations_extensions couldn't be read" in p and "most restrictive" in p for p in problems)


def test_an_unreadable_key_turns_ai_tools_and_app_studio_off(policy, monkeypatch):  # noqa: F811
    def refused():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: refused)
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)
    assert integrations_policy.current()["enabled"] is False
    assert appstudio_policy.current()["enabled"] is False


def test_other_lists_keep_their_string_items_and_say_what_was_left_out(policy, gp):  # noqa: F811
    tenant = "11111111-1111-1111-1111-111111111111"
    gp({}, {"allowed_tenants": [tenant, None, 3]})
    out, problems = registry_policy.read()
    assert out == {"allowed_tenants": [tenant]}
    assert problems == ["Group Policy key allowed_tenants has 2 items that aren't strings, so they're left out"]


def test_every_ai_tools_and_app_studio_restriction_has_a_most_restrictive_value():
    """_safest() lists each restriction by name: a new key in integrations/policy.py or appstudio/policy.py that
    it missed would keep its loose default when Group Policy can't be read. This fails until it is added there."""
    restrictions = {*app_settings.INTEGRATION_KEYS, *app_settings.APPSTUDIO_KEYS, *integrations_policy.KEYS,
                    *appstudio_policy.KEYS}
    assert set(registry_policy._safest()) == restrictions  # noqa: SLF001


def test_a_readable_subkey_does_not_undo_a_bad_value_under_the_same_name(policy, gp):  # noqa: F811
    # Found by seed 11: a strict list key set both as a junk value and as a subkey of valid items.
    gp({"appstudio_providers": (b"\x01\x00\x00\x00", 11)}, {"appstudio_providers": ["true", "1"]})
    out, problems, _ = registry_policy.read_all()
    assert out["appstudio_providers"] == STRICT["appstudio_providers"]
    assert any(p.startswith("Group Policy value appstudio_providers ") and "most restrictive" in p for p in problems)
