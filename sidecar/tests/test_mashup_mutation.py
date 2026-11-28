"""Dataflow Gen2 M reading at its edges: where statements split, how strings, comments and brackets hide a
semicolon, the attribute record before ``shared``, and the destination and load flags a query gets.
Mutation batch 56."""

from __future__ import annotations

import base64
import json

from fabric_desktop.orchestration import mashup


def test_statements_split_from_the_first_character():
    assert mashup.statements("a;b") == ["a", "b"]
    assert mashup.statements(";b") == ["", "b"]


def test_blank_text_after_the_last_semicolon_is_not_a_statement():
    assert mashup.statements("a; ") == ["a"]
    assert mashup.statements("a; b ") == ["a", " b "]


def test_a_semicolon_in_a_string_is_text_and_doubled_quotes_stay_in_it():
    assert mashup.statements('x = "a;""b;"; y') == ['x = "a;""b;"', " y"]
    assert mashup.statements('x = "a"') == ['x = "a"']
    assert mashup.statements('x = ""') == ['x = ""']
    assert mashup.statements('x = "a') == ['x = "a']


def test_line_comments_end_at_the_newline():
    assert mashup.statements("a // c;\nb;c") == ["a // c;\nb", "c"]
    assert mashup.statements("a // c;") == ["a // c;"]


def test_block_comments_end_at_their_close():
    assert mashup.statements("/*/;x") == ["/*/;x"]
    assert mashup.statements("/**/;b") == ["/**/", "b"]
    assert mashup.statements("/**/*;*/b") == ["/**/*", "*/b"]
    assert mashup.statements("/* ; ") == ["/* ; "]


def test_brackets_hide_semicolons_and_a_stray_close_does_not_go_below_zero():
    assert mashup.statements("f(a;b);c") == ["f(a;b)", "c"]
    assert mashup.statements("[a;{b;c}];d") == ["[a;{b;c}]", "d"]
    assert mashup.statements(");a;b") == [")", "a", "b"]


def test_trivia_skips_leading_spaces_and_comments():
    assert mashup._trivia("  // c\n /* d */ x") == "x"
    assert mashup._trivia("// only") == ""
    assert mashup._trivia("/* open") == ""
    assert mashup._trivia("/**/x") == "x"


def test_the_attribute_record_ends_at_its_own_close_bracket():
    assert mashup._attributes('[A = "x]"] shared Q = 1') == ('[A = "x]"]', "shared Q = 1")
    assert mashup._attributes('[A = "a""]"] rest') == ('[A = "a""]"]', "rest")
    assert mashup._attributes("[A = [B = 1]] rest") == ("[A = [B = 1]]", "rest")
    assert mashup._attributes('[A = "x') == ("", '[A = "x')
    assert mashup._attributes("[A = 1") == ("", "[A = 1")
    assert mashup._attributes('[A = "x"') == ("", '[A = "x"')
    assert mashup._attributes('[A = ""] r') == ('[A = ""]', "r")
    assert mashup._attributes('[A = "a"""] r') == ('[A = "a"""]', "r")
    assert mashup._attributes("shared Q = 1") == ("", "shared Q = 1")


def test_queries_take_quoted_names_and_skip_what_isnt_shared():
    text = 'section Section1;\n[A = 1] shared #"My ""Q""" = 1;\nshared B.c = 2;\nfoo'
    assert mashup.queries(text) == [{"name": 'My "Q"', "m": "1", "attributes": "[A = 1]"},
                                    {"name": "B.c", "m": "2", "attributes": ""}]


def _dest(query, update="Replace", new=True):
    attrs = (f'[DataDestinations = {{[Definition = [Kind = "Reference", QueryName = "{query}", '
             f'IsNewTarget = {"true" if new else "false"}], Settings = [Kind = "Manual", '
             f'UpdateMethod = [Kind = "{update}"]]]}}]')
    return attrs


def test_a_destination_reads_its_target_query():
    by_name = {"Q_D": {"m": 'let S = Fabric.Warehouse([]), N = S{[workspaceId = "w1"]}[Data], '
                            'T = N{[warehouseId = "h1"]}[Data], X = T{[Schema = "dbo", Item = "Sales"]}[Data] in X'}}
    got = mashup.destination(_dest("Q_D"), by_name)
    assert got == {"query": "Q_D", "kind": "Warehouse", "workspace_id": "w1", "item_id": "h1", "schema": "dbo",
                   "table": "Sales", "update_method": "Replace", "new_table": True}


def test_a_destination_to_an_unknown_function_or_a_missing_query():
    got = mashup.destination(_dest("Q_D", new=False), {"Q_D": {"m": "Odd.Thing()"}})
    assert got["kind"] == "Unknown" and got["new_table"] is False
    got = mashup.destination(_dest("Gone"), {})
    assert got["kind"] == "" and got["query"] == "Gone"
    assert mashup.destination("[Other = 1]", {}) is None
    got = mashup.destination("[DataDestinations = {}]", {})
    assert got["query"] == "" and got["update_method"] == ""


def test_a_quoted_name_with_doubled_quotes_finds_its_destination():
    by_name = {'Q "D"': {"m": 'Lakehouse.Contents([])'}}
    got = mashup.destination('[DataDestinations = {[Definition = [QueryName = "Q ""D"""]]}]', by_name)
    assert got["kind"] == "Lakehouse"


def test_connection_ids_come_from_json_or_plain_text():
    assert mashup._connection_id('{"DatasourceId": "d1"}') == "d1"
    assert mashup._connection_id("plain-id") == "plain-id"
    assert mashup._connection_id("[1]") == "[1]"
    assert mashup._connection_id(None) == ""


def _result(m, meta):
    def b64(s):
        return base64.b64encode(s.encode()).decode()
    return {"definition": {"parts": [{"path": "mashup.pq", "payload": b64(m)},
                                     {"path": "queryMetadata.json", "payload": b64(json.dumps(meta))}]}}


def test_a_query_loads_and_shows_unless_its_flags_say_otherwise():
    out = mashup.describe(_result("section S; shared A = 1; shared B = 2;",
                                  {"queriesMetadata": {"B": {"loadEnabled": False, "isHidden": True}}}))
    flags = {q["name"]: (q["load_enabled"], q["hidden"]) for q in out["queries"]}
    assert flags == {"A": (True, False), "B": (False, True)}
    assert out["ok"] is True and out["problems"] == []
