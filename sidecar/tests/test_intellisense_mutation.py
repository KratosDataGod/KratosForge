"""Notebook hover, signature help and the as-you-type syntax check: limits at their exact values, the
call being typed in nested and quoted code, and where an error is drawn. Mutation batch 60."""

from fabric_desktop import intellisense as isn


def test_a_cursor_outside_the_code_finds_no_name():
    assert isn.dotted_at("df.x", 4) == ("df.x", 0, 4)
    assert isn.dotted_at("df.x", 5) is None and isn.dotted_at("df.x", -1) is None
    assert isn.dotted_at("df", 0) == ("df", 0, 2)


def test_docs_are_kept_up_to_1500_characters():
    assert isn.DOC_CHARS == 1500

    class Exact:
        pass

    Exact.__doc__ = "d" * 1500
    assert isn._doc(Exact) == "d" * 1500

    class Long:
        pass

    Long.__doc__ = "d" * 1501
    assert isn._doc(Long) == "d" * 1500 + " ..."


def test_a_callable_object_is_shown_as_a_function():
    class Adder:
        def __call__(self, a, b=1):
            return a + b

    out = isn.describe("add", Adder())
    assert out["kind"] == "function" and out["signature"] == "(a, b=1)"


def test_a_comment_runs_to_the_end_of_its_line_or_the_cell():
    assert isn.open_call("f(  # g(\n") == ("f", 0, None)
    assert isn.open_call("f(a, # g(") == ("f", 1, None)
    assert isn.open_call("# f(") is None


def test_triple_quoted_strings_are_skipped_whole():
    assert isn.open_call("f('''it's (''', x") == ("f", 1, None)
    assert isn.open_call('g(1, """a"b(""", ') == ("g", 2, None)


def test_the_innermost_open_call_is_the_one_being_typed():
    assert isn.open_call("f(a, g(") == ("g", 0, None)
    assert isn.open_call("[f(") == ("f", 0, None)
    assert isn.open_call("f([") is None


def test_the_keyword_being_typed_is_the_last_argument_only():
    assert isn.open_call("f(x=") == ("f", 0, "x")
    assert isn.open_call("f(a=1, b=") == ("f", 1, "b")
    assert isn.open_call("f(a=1, b") == ("f", 1, None)


def _two(a, b, *rest):
    return a


def test_past_the_last_positional_parameter_the_star_args_are_active():
    assert isn.call_help(_two, "two", 1, None)["active"] == 1
    assert isn.call_help(_two, "two", 2, None)["active"] == 2
    assert isn.call_help(lambda a, b: a, "f", 2, None)["active"] is None


def test_the_return_annotation_is_shown_only_when_there_is_one():
    def plain(a):
        """Short."""

    def typed(a) -> int:  # this file has no `from __future__ import annotations`, so int stays a type
        return 1

    assert isn.call_help(plain, "plain", 0, None)["returns"] == ""
    assert isn.call_help(typed, "typed", 0, None)["returns"] == "int"


def test_the_signature_doc_is_its_first_paragraph_cut_to_400():
    def f():
        pass

    f.__doc__ = "w" * 401 + "\n\nmore"
    assert isn.call_help(f, "f", 0, None)["doc"] == "w" * 400
    f.__doc__ = "w" * 400
    assert isn.call_help(f, "f", 0, None)["doc"] == "w" * 400


def test_errors_after_a_python_cell_magic_keep_their_cell_line_numbers():
    assert isn.syntax_problems("%%python\nx = (") == [
        {"line": 2, "col": 4, "end_line": 2, "end_col": 5, "message": "'(' was never closed"}]
    assert isn.syntax_problems("\n%%Python\n\nx = )")[0]["line"] == 4
    assert isn.syntax_problems("%%python") == []


def test_an_error_at_the_start_of_a_line_is_drawn_from_column_0():
    assert isn.syntax_problems(")") == [
        {"line": 1, "col": 0, "end_line": 1, "end_col": 1, "message": "Unmatched ')'"}]


def test_an_error_keeps_the_span_python_gives_it():
    assert isn.syntax_problems("return 1") == [
        {"line": 1, "col": 0, "end_line": 1, "end_col": 8, "message": "'return' outside function"}]
    assert isn.syntax_problems('print "a"')[0]["end_col"] == 9
    assert isn.syntax_problems("x = 1 +") == [
        {"line": 1, "col": 7, "end_line": 1, "end_col": 8, "message": "Invalid syntax"}]


def test_a_line_magic_stands_in_for_an_empty_statement():
    assert isn.syntax_problems("if x:\n%time f()") == [
        {"line": 2, "col": 0, "end_line": 2, "end_col": 4,
         "message": "Expected an indented block after 'if' statement on line 1"}]


def test_the_check_does_not_take_this_modules_future_imports():
    # Under `from __future__ import annotations` this would be a different error.
    assert isn.syntax_problems("def f(x: (yield)): pass")[0]["message"] == "'yield' outside function"


def test_messages_start_with_a_capital_and_have_a_fallback():
    assert isn._message(SyntaxError("invalid thing")) == "Invalid thing"
    assert isn._message(SyntaxError()) == "Invalid syntax"


def test_an_error_over_several_lines_ends_on_its_last_line():
    assert isn.syntax_problems("[x\nfor x in y] = 1") == [
        {"line": 1, "col": 0, "end_line": 2, "end_col": 2,
         "message": "Cannot assign to list comprehension here. Maybe you meant '==' instead of '='?"}]
    assert isn.syntax_problems("f(\n**k,\n*a)")[0]["end_line"] == 3


def test_an_error_with_no_end_column_is_one_character_wide():
    # Python gives end_offset 0 here; the span is drawn over one character.
    assert isn.syntax_problems("x = [1,\n2,\n") == [
        {"line": 1, "col": 4, "end_line": 1, "end_col": 5, "message": "'[' was never closed"}]


def test_an_error_python_gives_no_position_is_drawn_at_the_start():
    assert isn.syntax_problems("x = 1\x00") == [
        {"line": 1, "col": 0, "end_line": 1, "end_col": 1,
         "message": "Source code string cannot contain null bytes"}]


def test_an_error_over_several_lines_keeps_its_own_end_column():
    assert isn.syntax_problems("f(\n**k,\n*a)") == [
        {"line": 2, "col": 3, "end_line": 3, "end_col": 2,
         "message": "Iterable argument unpacking follows keyword argument unpacking"}]


def test_a_sql_cell_after_a_line_of_spaces_is_not_checked_as_python():
    assert isn.syntax_problems("  \n%%sql\nselect 1 from") == []
