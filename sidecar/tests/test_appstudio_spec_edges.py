"""App Studio specifications: the refusals the main tests don't reach, one problem at a time. Pure
validation of dicts; nothing is read or written."""

import pytest

from fabric_desktop.appstudio import spec as specs


def base():
    s = specs.new("Sales")
    s["sources"] = [{"id": "src", "kind": "sample", "name": "Sample"}]
    s["measures"] = [{"id": "m1", "name": "Revenue", "expression": "SUM(Sales[Amount])", "source": "src"}]
    s["pages"][0]["components"] = [{"id": "c1", "type": "card", "measures": ["m1"],
                                    "layout": {"x": 0, "y": 0, "w": 3, "h": 2}}]
    assert specs.validate(s) == []
    return s


def problems(change):
    s = base()
    change(s)
    return specs.validate(s)


def comp(s):
    return s["pages"][0]["components"][0]


@pytest.mark.parametrize("change, says", [
    (lambda s: s.update(format=0, id="other"), ["format must be 1.", 'id must be "app".']),
    (lambda s: s.update(purpose="x" * (specs.MAX_TEXT + 1)), ["purpose is longer than 2000 characters."]),
    (lambda s: s.update(purpose="bell\x07"), ["purpose has control characters."]),
    (lambda s: s.update(audience=["", "Sales"]), ["audience must be a list of short texts."]),
    (lambda s: s["sources"][0].update(kind="excel"),
     ["sources[0].kind must be one of: lakehouse-table, local-table, sample, semantic-model."]),
    (lambda s: s.update(pages=[]), ["An application needs at least one page."]),
    (lambda s: s["pages"][0].update(components="none"), ["pages[0].components must be a list of objects."]),
    (lambda s: comp(s).update(measures="m1"), ["pages[0].components[0].measures must be a list of measure ids."]),
    (lambda s: comp(s).update(layout={"x": 0, "y": 0, "w": True, "h": 1}),
     ["pages[0].components[0].layout needs whole numbers x, y, w and h."]),
    (lambda s: comp(s).update(props=[]), ["pages[0].components[0].props must be an object."]),
    (lambda s: s.update(filters=[{"id": "f1", "field": "no brackets"}]),
     ["filters[0].field must be a Table[Column] reference."]),
    (lambda s: s.update(filters=[{"id": "f1", "field": "Sales[Region]", "pages": []}]),
     ['filters[0].pages must be "all" or a list of page ids.']),
    (lambda s: s.update(theme="dark"), ["theme.colors must be an object."]),
    (lambda s: s.update(permissions={"roles": "admins"}), ["permissions.roles must be a list."]),
    (lambda s: s.update(refresh={"mode": "hourly"}), ["refresh.mode must be one of: live, manual, scheduled."]),
    (lambda s: s.update(tests={}), ["tests must be a list."]),
])
def test_each_problem_is_named(change, says):
    assert problems(change) == says


def test_too_many_pages_or_components_are_refused(monkeypatch):
    monkeypatch.setattr(specs, "MAX_PAGES", 1)
    monkeypatch.setattr(specs, "MAX_COMPONENTS", 1)

    def grow(s):
        s["pages"].append({"id": "pg_2", "title": "Two", "components": [
            {"id": "c2", "type": "text", "layout": {"x": 0, "y": 0, "w": 1, "h": 1}}]})

    found = problems(grow)
    assert "An application has at most 1 pages." in found
    assert "An application has at most 1 components." in found


def test_a_component_on_no_page_has_none():
    assert specs.page_of(base(), "c1") == "pg_1"
    assert specs.page_of(base(), "nowhere") is None
