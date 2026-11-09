"""The policy templates can't drift from the settings the app reads.

app_settings.POLICY_KEYS is the one list of policy settings. policy_schema describes each, and the
ADMX/ADML in deploy/policy are generated from it. These fail when a key is read but not described,
described but not read, missing from the ADMX, or when the committed templates aren't what the
generator writes now (run ``python -m fabric_desktop.policy_schema`` to regenerate them).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fabric_desktop import app_settings, policy_schema

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "sidecar" / "fabric_desktop"
ADMX = REPO / "deploy" / "policy" / "KratosForge.admx"
ADML = REPO / "deploy" / "policy" / "en-US" / "KratosForge.adml"
NS = {"p": "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"}
ELEMENT = {"enum": "enum", "int": "decimal", "text": "text", "list": "list"}


def test_every_policy_key_is_described_once():
    described = [st.key for st in policy_schema.settings()]
    assert described == list(app_settings.POLICY_KEYS)
    assert set(policy_schema._TEXT) == set(app_settings.POLICY_KEYS)
    assert all(st.category in policy_schema.CATEGORIES and st.text.endswith(".") for st in policy_schema.settings())


def test_keys_read_outside_app_settings_are_policy_keys():
    shell = (REPO / "app" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    read_by_shell = set(re.findall(r'\bvalue\.get\("([a-z_]+)"\)', shell))
    assert read_by_shell and read_by_shell <= set(app_settings.POLICY_KEYS), read_by_shell - set(app_settings.POLICY_KEYS)
    for module in ("integrations/policy.py", "appstudio/policy.py"):
        text = (PACKAGE / module).read_text(encoding="utf-8")
        read = set(re.findall(r'raw\["([a-z_]+)"\]|"([a-z_]+)" in raw', text))
        keys = {a or b for a, b in read}
        assert keys and keys <= set(app_settings.POLICY_KEYS), (module, keys - set(app_settings.POLICY_KEYS))


def _policies() -> dict[str, ET.Element]:
    root = ET.parse(ADMX).getroot()
    return {p.get("name"): p for p in root.iterfind("p:policies/p:policy", NS)}


def test_every_setting_a_template_can_hold_is_in_the_admx_with_the_right_box():
    policies = _policies()
    for st in policy_schema.settings():
        if st.kind == "json":
            assert st.key not in policies, f"{st.key} is structured: only the policy file can hold it"
            continue
        policy = policies.pop(st.key)  # KeyError: a policy key missing from the ADMX
        assert policy.get("class") == "Machine" and policy.get("key") == policy_schema.REGISTRY_KEY
        if st.kind == "bool":
            assert policy.get("valueName") == st.key and policy.find("p:enabledValue", NS) is not None
            continue
        element = policy.find(f"p:elements/p:{ELEMENT[st.kind]}", NS)
        assert element is not None and element.get("id") == st.key, st.key
        if st.kind == "enum":
            assert [v.text for v in element.iterfind("p:item/p:value/p:string", NS)] == list(st.choices)
        if st.kind == "int":
            assert (int(element.get("minValue")), int(element.get("maxValue"))) == (st.low, st.high)
    assert policies == {}, f"in the ADMX but not a policy key: {sorted(policies)}"


def test_every_name_the_admx_uses_is_in_the_adml():
    admx, adml = ADMX.read_text(encoding="utf-8"), ET.parse(ADML).getroot()
    strings = {s.get("id") for s in adml.iterfind(".//p:stringTable/p:string", NS)}
    presentations = {s.get("id") for s in adml.iterfind(".//p:presentationTable/p:presentation", NS)}
    assert set(re.findall(r"\$\(string\.([A-Za-z0-9_]+)\)", admx)) <= strings
    assert set(re.findall(r"\$\(presentation\.([A-Za-z0-9_]+)\)", admx)) <= presentations


@pytest.mark.parametrize("path,make", [(ADMX, policy_schema.admx), (ADML, policy_schema.adml)])
def test_the_committed_templates_are_what_the_generator_writes(path, make):
    assert path.read_bytes().replace(b"\r\n", b"\n") == make().encode("utf-8"), \
        f"{path.name} is out of date: run python -m fabric_desktop.policy_schema"
