"""App Studio specifications: the edges a mutation run showed no test pinned (docs/quality/mutation-testing.md,
batch 30). Each limit at and one past its value, each target's own lists, and the dependency index. Pure
validation of dicts; nothing is read or written."""

import pytest

from fabric_desktop.appstudio import spec as specs


def base(target="powerbi-report"):
    s = specs.new("Sales", target=target)
    s["sources"] = [{"id": "src", "kind": "sample", "name": "Sample"}]
    s["measures"] = [{"id": "m1", "name": "Revenue", "expression": "SUM(Sales[Amount])", "source": "src"}]
    s["pages"][0]["components"] = [{"id": "c1", "type": "card", "measures": ["m1"],
                                    "layout": {"x": 0, "y": 0, "w": 3, "h": 2}}]
    assert specs.validate(s) == []
    return s


def problems(change, target="powerbi-report"):
    s = base(target)
    change(s)
    return specs.validate(s)


def comp(s):
    return s["pages"][0]["components"][0]


def test_a_new_specification_has_every_part_and_is_valid():
    s = specs.new("Ops", target="kratosforge-app", purpose="Watch the runs")
    assert s == {"format": 1, "id": "app", "name": "Ops", "purpose": "Watch the runs", "audience": [],
                 "target": "kratosforge-app", "sources": [], "measures": [], "theme": {"colors": {}, "font": ""},
                 "pages": [{"id": "pg_1", "title": "Page 1", "components": []}], "filters": [], "interactions": [],
                 "permissions": {"roles": [], "writeback": False}, "refresh": {"mode": "live"}, "tests": []}
    assert specs.validate(s) == []
    assert specs.new("x")["target"] == "powerbi-report" and specs.new("x")["purpose"] == ""


def test_anything_but_an_object_is_refused_whole():
    assert specs.validate([]) == ["The specification must be a JSON object."]
    assert specs.validate(None) == ["The specification must be a JSON object."]


@pytest.mark.parametrize("field, limit", [("name", 120), ("purpose", specs.MAX_TEXT)])
def test_app_texts_are_allowed_up_to_their_limit(field, limit):
    assert problems(lambda s: s.update({field: "x" * limit})) == []
    assert problems(lambda s: s.update({field: "x" * (limit + 1)})) == [f"{field} is longer than {limit} characters."]


def test_the_name_is_required_and_the_purpose_is_not():
    assert problems(lambda s: s.update(name="   ")) == ["name must be non-empty text."]
    assert problems(lambda s: s.pop("name")) == ["name must be non-empty text."]
    assert problems(lambda s: s.update(name=5)) == ["name must be non-empty text."]
    assert problems(lambda s: s.pop("purpose")) == []
    assert problems(lambda s: s.update(purpose="")) == []
    assert problems(lambda s: s.update(purpose=7)) == ["purpose must be text."]


def test_tabs_and_new_lines_are_text_but_other_control_characters_are_not():
    assert problems(lambda s: s.update(purpose="one\ntwo\tthree")) == []
    assert problems(lambda s: s.update(purpose="x\x1f")) == ["purpose has control characters."]
    assert problems(lambda s: s.update(purpose="x\r")) == ["purpose has control characters."]
    assert problems(lambda s: s.update(purpose="x\x00")) == ["purpose has control characters."]
    assert problems(lambda s: s.update(purpose="space and ~")) == []


def test_audience_entries_are_short_texts():
    assert problems(lambda s: s.update(audience=["x" * 120, "x"])) == []
    assert problems(lambda s: s.update(audience=["x" * 121])) == ["audience must be a list of short texts."]
    assert problems(lambda s: s.update(audience="Sales")) == ["audience must be a list of short texts."]
    assert problems(lambda s: s.update(audience=[3])) == ["audience must be a list of short texts."]
    assert problems(lambda s: s.pop("audience")) == []


def test_unknown_top_level_fields_are_named_in_order():
    assert problems(lambda s: s.update(zeta=1, alpha=2)) == ["Unknown top-level fields: alpha, zeta."]
    for known in ("theme", "permissions", "refresh", "tests", "audience", "purpose"):
        assert not any("Unknown" in m for m in problems(lambda s, k=known: s.update({k: s.get(k)})))


def test_an_unknown_target_is_named_and_its_components_are_refused():
    found = problems(lambda s: s.update(target="excel"))
    assert found[0] == "target must be one of: kratosforge-app, powerbi-report."
    assert "pages[0].components[0].type 'card' isn't available for a this target." in found
    # An unknown target can't have writeback either.
    found = problems(lambda s: s.update(target="excel", permissions={"roles": [], "writeback": True}))
    assert found[-1] == "Writeback isn't available for this target yet."


def test_a_collection_that_is_not_a_list_of_objects_stops_the_rest():
    def change(s):
        s.update(filters="all", refresh={})
        comp(s)["id"] = "m1"   # a duplicate id, a bad refresh: neither is checked once a list is wrong
    assert problems(change) == ["filters must be a list of objects."]
    assert problems(lambda s: s.update(measures=[1])) == ["measures must be a list of objects."]
    assert problems(lambda s: s.pop("interactions")) == ["interactions must be a list of objects."]


def test_ids_follow_the_pattern_and_64_characters_at_most():
    assert problems(lambda s: comp(s).update(id="c" + "x" * 63)) == []
    assert problems(lambda s: comp(s).update(id="c" + "x" * 64)) == [
        "pages[0].components[0].id must start with a letter and use letters, digits, _ or - (64 at most)."]
    assert problems(lambda s: comp(s).update(id="1c")) != []
    assert problems(lambda s: comp(s).update(id="c-1_A")) == []


def test_an_id_used_twice_names_where_it_was_first():
    found = problems(lambda s: comp(s).update(id="m1"))
    assert "pages[0].components[0].id 'm1' is already used at measures[0]." in found


def test_a_reference_must_name_the_right_kind():
    assert problems(lambda s: s["measures"][0].update(source="pg_1")) == ["measures[0].source must name a source in this specification."]
    assert problems(lambda s: comp(s).update(measures=["src"])) == [
        "pages[0].components[0].measures[0] must name a measure in this specification."]


def test_source_and_measure_texts_have_their_own_limits():
    assert problems(lambda s: s["sources"][0].update(name="x" * 256, table="t" * 256)) == []
    assert problems(lambda s: s["sources"][0].update(name="x" * 257)) == ["sources[0].name is longer than 256 characters."]
    assert problems(lambda s: s["sources"][0].update(item_id="x" * 257)) == ["sources[0].item_id is longer than 256 characters."]
    assert problems(lambda s: s["sources"][0].update(workspace_id=1)) == ["sources[0].workspace_id must be text."]
    assert problems(lambda s: s["sources"][0].update(name="")) == ["sources[0].name must be non-empty text."]
    assert problems(lambda s: s["measures"][0].update(name="x" * 128, expression="1" * 8000)) == []
    assert problems(lambda s: s["measures"][0].update(name="x" * 129)) == ["measures[0].name is longer than 128 characters."]
    assert problems(lambda s: s["measures"][0].pop("name")) == ["measures[0].name must be non-empty text."]
    assert problems(lambda s: s["measures"][0].pop("expression")) == ["measures[0].expression must be non-empty text."]
    assert problems(lambda s: s["sources"][0].pop("name")) == ["sources[0].name must be non-empty text."]
    assert problems(lambda s: s["pages"][0].pop("title")) == ["pages[0].title must be non-empty text."]
    assert problems(lambda s: s["measures"][0].update(expression="1" * 8001)) == [
        "measures[0].expression is longer than 8000 characters."]
    assert problems(lambda s: s["measures"][0].update(expression=" ")) == ["measures[0].expression must be non-empty text."]
    assert problems(lambda s: s["measures"][0].update(description="d" * 2001)) == [
        "measures[0].description is longer than 2000 characters."]


def test_page_and_component_titles_have_their_own_limits():
    assert problems(lambda s: s["pages"][0].update(title="t" * 120)) == []
    assert problems(lambda s: s["pages"][0].update(title="t" * 121)) == ["pages[0].title is longer than 120 characters."]
    assert problems(lambda s: s["pages"][0].update(title="")) == ["pages[0].title must be non-empty text."]
    assert problems(lambda s: comp(s).update(title="t" * 200)) == []
    assert problems(lambda s: comp(s).update(title="t" * 201)) == ["pages[0].components[0].title is longer than 200 characters."]


def _pages(n):
    def grow(s):
        s["pages"] = [{"id": f"pg_{i}", "title": "P", "components": []} for i in range(n)]
    return grow


def test_fifty_pages_are_allowed_and_fifty_one_are_not():
    assert problems(_pages(50)) == []
    assert problems(_pages(51)) == ["An application has at most 50 pages."]


def _components(n):
    def grow(s):
        s["pages"] = [{"id": "pg_a", "title": "A", "components": []}, {"id": "pg_b", "title": "B", "components": []}]
        for i in range(n):
            s["pages"][i % 2]["components"].append({"id": f"c{i}", "type": "text",
                                                    "layout": {"x": 0, "y": i, "w": 1, "h": 1}})
    return grow


def test_components_are_counted_over_every_page():
    assert problems(_components(400)) == []
    assert problems(_components(401)) == ["An application has at most 400 components."]


@pytest.mark.parametrize("layout, ok", [
    ({"x": 0, "y": 0, "w": 12, "h": 1}, True),
    ({"x": 9, "y": 0, "w": 3, "h": 1}, True),
    ({"x": 10, "y": 0, "w": 3, "h": 1}, False),
    ({"x": -1, "y": 0, "w": 1, "h": 1}, False),
    ({"x": 0, "y": -1, "w": 1, "h": 1}, False),
    ({"x": 0, "y": 0, "w": 0, "h": 1}, False),
    ({"x": 0, "y": 0, "w": 1, "h": 0}, False),
    ({"x": 0, "y": 500, "w": 1, "h": 9}, True),
])
def test_a_layout_sits_inside_the_twelve_column_grid(layout, ok):
    found = problems(lambda s: comp(s).update(layout=layout))
    assert found == ([] if ok else ["pages[0].components[0].layout must sit inside the 12-column grid."])


def test_components_that_are_not_objects_are_refused_by_page():
    assert problems(lambda s: s["pages"][0].update(components=[1])) == ["pages[0].components must be a list of objects."]
    assert problems(lambda s: s["pages"][0].pop("components")) == ["pages[0].components must be a list of objects."]


def test_layout_numbers_must_be_whole_and_present():
    for bad in ({"x": 0, "y": 0, "w": 1.0, "h": 1}, {"x": 0, "y": 0, "w": 1}, [0, 0, 1, 1], {"x": False, "y": 0, "w": 1, "h": 1}):
        assert problems(lambda s, b=bad: comp(s).update(layout=b)) == ["pages[0].components[0].layout needs whole numbers x, y, w and h."]


def test_fields_are_table_column_references():
    ok = ["Sales[Amount]", "'Sales 2024'[Amount]", "_t[c]", "My Table[A b]"]
    assert problems(lambda s: comp(s).update(fields=ok)) == []
    for bad in (["Sales"], ["Sales[]"], ["[Amount]"], ["Sales[A][B]"], "Sales[Amount]", [1]):
        assert problems(lambda s, b=bad: comp(s).update(fields=b)) == [
            "pages[0].components[0].fields must be a list of Table[Column] references."]


def test_each_target_has_its_own_components():
    assert problems(lambda s: comp(s).update(type="matrix")) == []
    assert problems(lambda s: comp(s).update(type="matrix"), target="kratosforge-app") == [
        "pages[0].components[0].type 'matrix' isn't available for a KratosForge app."]
    assert problems(lambda s: comp(s).update(type="gauge")) == [
        "pages[0].components[0].type 'gauge' isn't available for a Power BI report."]
    assert problems(lambda s: comp(s).update(type="slicer"), target="kratosforge-app") == []


def _interaction(kind, to):
    def add(s):
        s["pages"].append({"id": "pg_2", "title": "Two", "components": [
            {"id": "c2", "type": "table", "layout": {"x": 0, "y": 0, "w": 6, "h": 4}}]})
        s["interactions"] = [{"id": "i1", "kind": kind, "from": "c1", "to": to}]
    return add


def test_a_drillthrough_goes_to_a_page_and_a_cross_filter_to_a_component():
    assert problems(_interaction("drillthrough", "pg_2")) == []
    assert problems(_interaction("drillthrough", "c2")) == ["interactions[0].to must name a page in this specification."]
    assert problems(_interaction("cross-filter", "c2")) == []
    assert problems(_interaction("cross-filter", "pg_2")) == ["interactions[0].to must name a component in this specification."]
    assert problems(_interaction("bookmark", "c2")) == []


def test_each_target_has_its_own_interactions():
    assert problems(_interaction("cross-filter", "c2"), target="kratosforge-app") == []
    assert problems(_interaction("drillthrough", "pg_2"), target="kratosforge-app") == [
        "interactions[0].kind 'drillthrough' isn't available for a KratosForge app."]
    assert problems(_interaction("hover", "c2")) == ["interactions[0].kind 'hover' isn't available for a Power BI report."]


def test_an_interaction_from_something_that_is_not_a_component_is_refused():
    def add(s):
        _interaction("cross-filter", "c1")(s)
        s["interactions"][0]["from"] = "m1"
    assert problems(add) == ["interactions[0].from must name a component in this specification."]


def test_filter_pages_are_all_or_page_ids():
    assert problems(lambda s: s.update(filters=[{"id": "f1", "field": "Sales[Region]"}])) == []
    assert problems(lambda s: s.update(filters=[{"id": "f1", "field": "Sales[Region]", "pages": ["pg_1"]}])) == []
    assert problems(lambda s: s.update(filters=[{"id": "f1", "field": "Sales[Region]", "pages": ["c1"]}])) == [
        "filters[0].pages[0] must name a page in this specification."]
    assert problems(lambda s: s.update(filters=[{"id": "f1", "field": "Sales[Region]", "pages": "some"}])) == [
        'filters[0].pages must be "all" or a list of page ids.']
    assert problems(lambda s: s.update(filters=[{"id": "f1", "field": 3}])) == ["filters[0].field must be a Table[Column] reference."]


def test_theme_colours_and_font():
    assert problems(lambda s: s.update(theme={"colors": {"accent": "#a1B2c3"}, "font": "f" * 80})) == []
    assert problems(lambda s: s.update(theme={"colors": {"accent": "#a1B2c"}})) == ["theme.colors.accent must be a #RRGGBB colour."]
    assert problems(lambda s: s.update(theme={"colors": {"k" * 60: 1}})) == [f"theme.colors.{'k' * 40} must be a #RRGGBB colour."]
    assert problems(lambda s: s.update(theme={"colors": []})) == ["theme.colors must be an object."]
    assert problems(lambda s: s.update(theme={"font": "f" * 81})) == ["theme.font is longer than 80 characters."]
    assert problems(lambda s: s.pop("theme")) == []


def test_writeback_is_refused_for_every_target_today():
    for target in specs.TARGETS:
        assert problems(lambda s: s.update(permissions={"roles": [], "writeback": True}), target=target) == [
            "Writeback isn't available for this target yet."]
    assert problems(lambda s: s.update(permissions={"writeback": False})) == []
    assert problems(lambda s: s.update(permissions=[])) == ["permissions.roles must be a list."]


def test_every_refresh_mode_is_accepted():
    for mode in specs.REFRESH_MODES:
        assert problems(lambda s, m=mode: s.update(refresh={"mode": m})) == []
    assert problems(lambda s: s.update(refresh="live")) == ["refresh.mode must be one of: live, manual, scheduled."]
    assert problems(lambda s: s.pop("tests")) == []


def test_check_returns_a_valid_spec_and_raises_with_every_problem():
    s = base()
    assert specs.check(s) is s
    with pytest.raises(specs.SpecError) as e:
        specs.check({"format": 2, "id": "x"})
    assert e.value.problems[:2] == ["format must be 1.", 'id must be "app".']
    assert str(e.value) == "; ".join(e.value.problems)
    assert isinstance(e.value, ValueError)


def full():
    s = base()
    s["sources"].append({"id": "src2", "kind": "sample", "name": "Two"})
    s["measures"].append({"id": "m2", "name": "Cost", "expression": "1", "source": "src2"})
    s["measures"].append({"id": "m3", "name": "Plain", "expression": "1"})
    s["pages"].append({"id": "pg_2", "title": "Two", "components": [
        {"id": "c2", "type": "table", "measures": ["m1", "m2", "m3", "m1"], "layout": {"x": 0, "y": 0, "w": 1, "h": 1}}]})
    s["filters"] = [{"id": "f_all", "field": "Sales[Region]"}, {"id": "f_some", "field": "Sales[Region]", "pages": ["pg_2"]}]
    s["interactions"] = [{"id": "i1", "kind": "drillthrough", "from": "c1", "to": "pg_2"}]
    assert specs.validate(s) == ["measures[2].source must name a source in this specification."]  # m3 has none
    return s


def test_find_names_the_kind_entry_and_its_list():
    s = full()
    assert specs.find(s, "app") == ("app", s, None)
    kind, entry, holder = specs.find(s, "c2")
    assert (kind, entry["id"]) == ("component", "c2") and holder is s["pages"][1]["components"]
    kind, entry, holder = specs.find(s, "pg_2")
    assert (kind, entry) == ("page", s["pages"][1]) and holder is s["pages"]
    assert specs.find(s, "m2")[:2] == ("measure", s["measures"][1])
    assert specs.find(s, "i1")[0] == "interaction" and specs.find(s, "f_some")[0] == "filter"
    with pytest.raises(KeyError):
        specs.find(s, "nowhere")


def test_page_of_finds_a_component_on_a_later_page():
    assert specs.page_of(full(), "c2") == "pg_2"
    assert specs.page_of(full(), "pg_2") is None


def test_dependencies_list_what_each_id_uses():
    deps = specs.dependencies(full())
    assert deps["m1"] == ["src"] and deps["m3"] == []
    assert deps["pg_1"] == [] and deps["pg_2"] == []
    assert deps["c1"] == ["m1", "src"]
    assert deps["c2"] == ["m1", "m2", "m3", "src", "src2"]
    assert deps["f_all"] == [] and deps["f_some"] == ["pg_2"]
    assert deps["i1"] == ["c1", "pg_2"]
    assert "app" not in deps and "src" not in deps


def test_dependents_are_what_an_edit_affects_in_document_order():
    s = full()
    assert specs.dependents(s, "src") == ["m1", "c1", "c2"]
    assert specs.dependents(s, "pg_2") == ["f_some", "i1"]
    assert specs.dependents(s, "c2") == []


def test_a_copy_is_deep():
    s = full()
    c = specs.copy_of(s)
    c["pages"][0]["components"][0]["measures"].append("m2")
    assert s["pages"][0]["components"][0]["measures"] == ["m1"] and c != s


def test_an_early_problem_does_not_stop_the_later_checks():
    def change(s):
        s["format"] = 2
        comp(s)["id"] = "m1"
    assert problems(change) == ["format must be 1.", "pages[0].components[0].id 'm1' is already used at measures[0].",
                                "pages[0].components[0].measures[0] must name a measure in this specification."]
