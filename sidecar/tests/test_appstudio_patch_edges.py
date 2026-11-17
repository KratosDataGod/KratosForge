"""App Studio patch operations: the refusals and nested fields the patch tests don't reach."""

import pytest

from fabric_desktop.appstudio import patch
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import sales


@pytest.mark.parametrize("op, expected", [
    ({"op": "set", "id": "c_rev"}, "set needs a field"),
    ({"op": "set", "id": "c_rev", "field": "title.size", "value": 3}, "goes through a value that isn't an object"),
    ({"op": "add", "kind": "component", "page": "pg_ghost", "value": {"id": "c_new"}}, "isn't a page"),
    ({"op": "add", "kind": "measure", "value": {"name": "no id"}}, "needs a value object with an id"),
    ({"op": "move", "id": "app", "index": 0}, "Only entries in a list can be moved"),
])
def test_a_bad_operation_is_refused_and_nothing_changes(op, expected):
    doc = sales()
    with pytest.raises(SpecError, match=expected):
        patch.apply(doc, [op])
    assert doc == sales()


def test_setting_a_nested_field_creates_the_objects_on_the_way_and_undo_removes_the_value():
    doc = sales()
    done = patch.apply(doc, [{"op": "set", "id": "c_rev", "field": "props.label.color", "value": "#000000"}])
    rev = done["spec"]["pages"][0]["components"][0]
    assert rev["props"] == {"label": {"color": "#000000"}}
    assert done["inverse"] == [{"op": "set", "id": "c_rev", "field": "props.label.color"}]
    back = patch.apply(done["spec"], done["inverse"])["spec"]
    assert back["pages"][0]["components"][0]["props"] == {"label": {}}
