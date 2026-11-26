"""Mutation tests for the policy schema (policy_schema.py): each setting's kind and limits as app_settings
gives them, and the ADMX/ADML a template holds for each kind, read from the generator's own output (the
committed files under deploy/ are compared elsewhere). Found by tools/mutate.py."""

import dataclasses
import runpy
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fabric_desktop import app_settings as s
from fabric_desktop import policy_schema as ps

NS = {"p": "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"}
BY_KEY = {st.key: st for st in ps.settings()}


def test_a_setting_has_no_choices_no_limits_and_isnt_a_users_unless_it_says_so():
    st = ps.Setting("k", "bool", "signin", "text")
    assert (st.choices, st.low, st.high, st.user) == ((), 0, 0, False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.kind = "text"


def _expected(key):
    t = s.TYPES.get(key)
    if key in ps._JSON:
        return "json", (), 0, 0
    if key in s.CHOICES:
        return "enum", tuple(s.CHOICES[key]), 0, 0
    if key == "channel":
        return "enum", tuple(s.CHANNELS), 0, 0
    if t is None:
        return ps._KINDS[key], (), 0, 0
    if t is bool:
        return "bool", (), 0, 0
    if t is list:
        return "list", (), 0, 0
    if t is str:
        return "text", (), 0, 0
    if key in s.SQL_RANGES:
        return ("int", (), *s.SQL_RANGES[key])
    if key == "motion_level":
        return ("int", (), *s.MOTION_LEVELS)
    dword = 0xFFFFFFFF  # the largest number Group Policy can write
    ranges = {"editor_font_size": tuple(s.EDITOR_FONT_SIZES), "audit_retention_days": (s.RETENTION_MIN_DAYS, dword),
              "receipt_retention_days": (s.RETENTION_MIN_DAYS, dword)}
    if key in ranges:
        return ("int", (), *ranges[key])
    high = {"local_retention_days": dword}.get(key, 0)
    return "int", (), 0, int(s.LIMITS.get(key, high))


def test_each_setting_takes_its_kind_and_limits_from_app_settings():
    assert [st.key for st in ps.settings()] == list(s.POLICY_KEYS)
    for key, st in BY_KEY.items():
        assert st.user is (key in s.USER_KEYS), key
        assert st.category in ps.CATEGORIES and st.text, key
        assert (st.kind, tuple(st.choices), st.low, st.high) == _expected(key), key
    assert {"json", "enum", "bool", "list", "text", "int"} <= {st.kind for st in BY_KEY.values()}
    assert any(st.kind == "int" and st.high for st in BY_KEY.values())


def _policies():
    root = ET.fromstring(ps.admx())
    return root, {p.get("name"): p for p in root.iterfind("p:policies/p:policy", NS)}


def test_the_admx_holds_each_kind_in_its_own_box_and_leaves_json_out():
    root, policies = _policies()
    assert {c.get("name") for c in root.iterfind("p:categories/p:category", NS)} == {"KratosForge", *(f"cat_{c}" for c in ps.CATEGORIES)}
    kinds = {st.kind for st in BY_KEY.values()}
    assert {"bool", "enum", "int", "text", "list", "json"} <= kinds
    for key, st in BY_KEY.items():
        if st.kind == "json":
            assert key not in policies, key
            continue
        p = policies.pop(key)
        assert p.get("key") == ps.REGISTRY_KEY and p.get("class") == "Machine"
        assert p.find("p:parentCategory", NS).get("ref") == f"cat_{st.category}"
        elements = p.find("p:elements", NS)
        if st.kind == "bool":
            assert p.get("valueName") == key and elements is None
            assert p.find("p:enabledValue/p:decimal", NS).get("value") == "1"
            assert p.find("p:disabledValue/p:decimal", NS).get("value") == "0"
            continue
        assert p.get("presentation") == f"$(presentation.{key})" and p.get("valueName") is None
        [box] = list(elements)
        tag = box.tag.split("}")[1]
        assert box.get("id") == key
        if st.kind == "enum":
            assert tag == "enum" and [i.find("p:value/p:string", NS).text for i in box] == list(st.choices)
        elif st.kind == "int":
            assert (tag, box.get("minValue"), box.get("maxValue")) == ("decimal", str(st.low), str(st.high))
        elif st.kind == "text":
            assert (tag, box.get("maxLength")) == ("text", "2048")
        else:
            assert (tag, box.get("key")) == ("list", f"{ps.REGISTRY_KEY}\\{key}")
    assert policies == {}


def test_the_adml_explains_each_setting_and_shows_the_right_box():
    root = ET.fromstring(ps.adml())
    strings = {e.get("id"): e.text for e in root.iterfind("p:resources/p:stringTable/p:string", NS)}
    shown = {e.get("id"): e for e in root.iterfind("p:resources/p:presentationTable/p:presentation", NS)}
    for key, st in BY_KEY.items():
        if st.kind == "json":
            assert key not in strings and key not in shown
            continue
        help_text = strings[f"{key}_Help"]
        assert help_text.startswith(st.text)
        assert ("People can also change it in Settings; this policy fixes it." in help_text) is st.user, key
        assert ("Add one item per line." in help_text) is (st.kind == "list"), key
        assert help_text.endswith(f"Policy file key: {key}.{ps._PRECEDENCE}")
        assert strings[key] == key.replace("_", " ").capitalize()
        for c in st.choices:
            assert strings[f"{key}_{c}"] == c
        if st.kind == "bool":
            assert key not in shown
            continue
        [box] = list(shown[key])
        tag = box.tag.split("}")[1]
        assert box.get("refId") == key
        assert tag == {"enum": "dropdownList", "int": "decimalTextBox", "text": "textBox", "list": "listBox"}[st.kind]
        if st.kind == "int":
            assert box.get("defaultValue") == str(st.low)
        if st.kind == "enum":
            assert box.get("noSort") == "true"
    assert {f"cat_{c}": t for c, t in ps.CATEGORIES.items()}.items() <= strings.items()


def test_run_with_no_folder_it_writes_the_templates_of_the_repository_it_is_in(monkeypatch):
    written = []
    monkeypatch.setattr(sys, "argv", ["policy_schema"])
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: written.append(self) or len(data))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("fabric_desktop.policy_schema", run_name="__main__")
    repo = Path(ps.__file__).resolve().parents[2]
    assert written == [repo / "deploy" / "policy" / "KratosForge.admx", repo / "deploy" / "policy" / "en-US" / "KratosForge.adml"]
