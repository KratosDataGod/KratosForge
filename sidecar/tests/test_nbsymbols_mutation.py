"""Go to definition, find references and rename: the cases mutation testing found untested (batch 27,
docs/quality/mutation-testing.md). Statements on a cell's first line, dotted imports, defaults,
except and case names, global statements with the name earlier on the line, the count of a name in
any scope, and the length of a place's line text."""

from __future__ import annotations

from fabric_desktop.nbsymbols import find_symbol


def code(source: str) -> dict:
    return {"type": "code", "source": source}


def places(cells, name, cell=0):
    found = find_symbol(cells, cell, 0, name)
    return [(p["cell"], p["kind"], p["line"], cells[p["cell"]]["source"][p["from"]:p["to"]], p["from"])
            for p in found["places"]]


def at(cells, cell, offset):
    return find_symbol(cells, cell, offset)["name"]


# ---- the first line of a cell

def test_a_def_on_the_first_line_is_its_definition():
    src = "def total():\n    return 1\nprint(total())"
    cells = [code(src)]
    assert places(cells, "total") == [(0, "definition", 1, "total", 4), (0, "reference", 3, "total", src.rindex("total"))]
    assert at(cells, 0, 5) == "total"


def test_a_global_statement_on_the_first_line_is_read():
    cells = [code("def f(): global total; total = 5")]
    assert at(cells, 0, 17) == "total"
    assert places(cells, "total") == [(0, "reference", 1, "total", 16), (0, "definition", 1, "total", 23)]


# ---- imports

def test_a_dotted_import_in_a_function_shadows_the_module_s_name():
    cells = [code("os = 1\ndef f():\n    import os.path\n    return os\nprint(os)")]
    assert [(k, line) for _, k, line, _, _ in places(cells, "os")] == [("definition", 1), ("reference", 5)]


def test_a_dotted_import_binds_its_first_part():
    cells = [code("import os.path\nos.getcwd()")]
    assert at(cells, 0, 7) == "os"
    assert places(cells, "os") == [(0, "definition", 1, "os", 7), (0, "reference", 2, "os", 15)]


# ---- defaults are read where the function is

def test_a_keyword_only_default_reads_the_module_s_name():
    cells = [code("limit = 3\ndef f(a, *, b=limit, c):\n    return b")]
    assert [(k, line) for _, k, line, _, _ in places(cells, "limit")] == [("definition", 1), ("reference", 2)]


def test_a_lambda_s_keyword_only_default_reads_the_module_s_name():
    cells = [code("limit = 3\ng = lambda *, b=limit, c: b + c")]
    assert [(k, line) for _, k, line, _, _ in places(cells, "limit")] == [("definition", 1), ("reference", 2)]


# ---- except and case names

def test_an_except_name_at_module_level_is_a_definition():
    src = "try:\n    pass\nexcept ValueError as err:\n    pass\nprint(err)"
    cells = [code(src)]
    assert places(cells, "err") == [(0, "definition", 3, "err", src.index("err")),
                                    (0, "reference", 5, "err", src.rindex("err"))]


def test_another_except_name_isn_t_this_name():
    cells = [code("total = 0\ntry:\n    pass\nexcept ValueError as e: print('as total')")]
    assert [(k, line) for _, k, line, _, _ in places(cells, "total")] == [("definition", 1)]


def test_a_case_capture_and_star_are_definitions():
    src = "match v:\n    case [first, *rest]:\n        pass\nprint(first, rest)"
    cells = [code(src)]
    for name in ("first", "rest"):
        assert places(cells, name) == [(0, "definition", 2, name, src.index(name)),
                                       (0, "reference", 4, name, src.rindex(name))]


# ---- a global statement with the name earlier on its line

def test_a_global_statement_finds_the_name_after_the_keyword():
    src = "x = 0\ndef f():\n    y = x; global x"
    cells = [code(src)]
    found = places(cells, "x")
    assert found[-1] == (0, "reference", 3, "x", src.rindex("x"))


def test_the_name_just_before_global_isn_t_taken_for_it():
    src = "x = 0\ndef f():\n    x;global x"
    cells = [code(src)]
    assert places(cells, "x")[-1] == (0, "reference", 3, "x", src.rindex("x"))


def test_a_name_in_a_global_statement_is_found_under_the_cursor_after_the_keyword():
    src = "def f():\n    y = x; global x\n"
    assert at([code(src)], 0, src.rindex("x")) == "x"


# ---- how often the name is written in any scope

def test_anywhere_counts_only_this_name():
    src = ("def f(total, other, *more):\n    pass\n"
           "class Total:\n    pass\n"
           "def g():\n    pass\n"
           "try:\n    pass\nexcept ValueError as e:\n    pass\nexcept TypeError:\n    pass\n"
           "match v:\n    case {'a': 1, **total}:\n        pass\n    case {'b': 2, **rest}:\n        pass\n"
           "    case [x, *ys]:\n        pass\n    case _:\n        pass\n")
    assert find_symbol([code(src)], 0, 0, "total")["anywhere"] == 2
    assert find_symbol([code(src)], 0, 0, "g")["anywhere"] == 1
    assert find_symbol([code(src)], 0, 0, "e")["anywhere"] == 1


# ---- a place's line text

def test_a_long_line_s_text_is_cut_at_200_characters():
    src = "total = [" + ", ".join(str(i) for i in range(100)) + "]"
    found = find_symbol([code(src)], 0, 0, "total")["places"]
    assert len(found[0]["text"]) == 200
    assert found[0]["text"] == src[:200]


def test_a_global_statement_on_the_first_line_is_read_after_its_keyword():
    src = "def f(): y = total; global total"
    assert at([code(src)], 0, src.rindex("total") + 1) == "total"


# ---- a method in a class inside a function

def test_a_method_sees_the_module_s_name_past_its_class_s_own():
    src = ("total = 0\n"
           "def outer():\n"
           "    class C:\n"
           "        total = 1\n"
           "        def m(self):\n"
           "            return total\n")
    found = places([code(src)], "total")
    assert [(k, line) for _, k, line, _, _ in found] == [("definition", 1), ("reference", 6)]


# ---- a case name with the same word earlier on its line

def test_a_case_name_is_found_where_the_pattern_binds_it():
    src = "match v:\n    case {'first': first}:\n        x\nx\nprint(first)"
    found = places([code(src)], "first")
    assert found[0] == (0, "definition", 2, "first", src.index("first}"))
