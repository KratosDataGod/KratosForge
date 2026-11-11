"""Reading pipeline expressions: the string and bracket edges the expression tests don't reach.
Nothing is evaluated."""

from fabric_desktop.orchestration import expressions as ex


def test_an_escaped_interpolation_is_literal_and_quotes_and_braces_inside_one_are_skipped():
    found = list(ex.scan("a @@{not this} then @{concat('it''s }', string(json('{}')))} and @{x{y}} end"))
    assert [e.text for e in found] == ["concat('it''s }', string(json('{}')))", "x{y}"]
    assert all(e.interpolated for e in found) and found[0].functions == ["concat", "string", "json"]


def test_an_unclosed_interpolation_is_reported_not_raised():
    (expr,) = ex.scan({"a": "Run @{pipeline().RunId"})
    assert expr.problem == "an @{ isn't closed with }"
    assert expr.to_json()["problem"] == "an @{ isn't closed with }"
    assert expr.to_json()["path"] == "$.a"


def test_an_expression_value_that_interpolates_is_read_as_its_parts():
    node = {"value": "Run @{pipeline().RunId} of @{pipeline().Pipeline}", "type": "Expression"}
    found = list(ex.scan(node))
    assert [e.text for e in found] == ["pipeline().RunId", "pipeline().Pipeline"]
    assert all(e.path == "$.value" and e.interpolated for e in found)


def test_quotes_and_brackets_that_dont_close_are_problems():
    assert ex.tokens("concat('a")[1] == "a quoted string isn't closed"
    assert ex.tokens("a)")[1] == "a ) has no matching opening bracket"


def test_an_optional_member_read_is_followed_past_the_question_mark():
    (ref,) = ex.references("activity('Lookup')?.output?.firstRow")
    assert (ref.kind, ref.name, ref.member) == ("activity", "Lookup", "output.firstRow")
