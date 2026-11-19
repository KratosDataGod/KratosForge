"""Kill tests for appstudio/publish.py (docs/quality/mutation-testing.md, batch 115).

A report name is cut at 256 characters, a missing field is named by its component's title, and the
fingerprint doesn't depend on the order of the specification's keys.
"""

from __future__ import annotations

import copy

from fabric_desktop.appstudio import publish as publishing

from test_appstudio_publish_edges import ready  # noqa: F401 - the fixture


def test_a_report_name_is_cut_at_256_characters():
    assert publishing.report_name("x" * 256, "f") == "x" * 256
    cut = publishing.report_name("x" * 257, "f")
    assert len(cut) == 256 and cut != "x" * 256


def test_a_field_the_model_lacks_is_named_by_its_component_s_title(ready):  # noqa: F811
    spec, meta = ready
    spec = copy.deepcopy(spec)
    page = next(p for p in spec["pages"] if any(c.get("fields") for c in p["components"]))
    comp = next(c for c in page["components"] if c.get("fields"))
    comp["title"], comp["fields"] = "Sales by month", ["Nowhere[Missing]"]
    got = publishing.check(spec, meta, "model-1")
    assert f"{page['title']}: Sales by month uses Nowhere[Missing], which the semantic model doesn't have." in got["problems"]


def test_the_fingerprint_does_not_depend_on_key_order():
    a = {"pages": [], "target": "powerbi-report"}
    b = {"target": "powerbi-report", "pages": []}
    assert publishing.fingerprint(a, "ws", "m", "n", {"p": "t"}, None) == \
        publishing.fingerprint(b, "ws", "m", "n", {"p": "t"}, None)
