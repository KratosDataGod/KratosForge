"""WR-236: the T-SQL splitter and translator under random comments, GO lines, brackets, N'' text and nested quotes.

Scripts are built from statements whose answer is known, joined and padded with the pieces that make splitting
hard. Each one must split into exactly its statements and each statement must give its own answer. Scripts cut
and spliced at random must either translate or stop with a ValueError that names what's wrong: never another
exception, never a crash. The seeds are fixed, so a failure always comes back the same way.
"""

from __future__ import annotations

import random

import duckdb
import pytest

from fabric_desktop.localdata import tsql

# (T-SQL text of a value, the value DuckDB gives back)
VALUES = [
    ("1", 1), ("-7", -7), ("'a;b'", "a;b"), ("N'it''s'", "it's"), ("'GO'", "GO"), ("'--x'", "--x"),
    ("'/* x */'", "/* x */"), ("'a]b'", "a]b"), ("'[go]'", "[go]"), ("N'é;😀'", "é;😀"), ("''''", "'"),
    ("'a''; GO; ''b'", "a'; GO; 'b"), ("'\"q\"'", '"q"'), ("N''", ""),
]
# (T-SQL text of a column name, the name DuckDB gives back)
NAMES = [
    ("a", "a"), ("[a;b]", "a;b"), ("[a]]b]", "a]b"), ('"x;y"', "x;y"), ("[GO]", "GO"), ("[--]", "--"),
    ("[/*]", "/*"), ("[it's]", "it's"), ("[ ]", " "),
]
# what may sit between two statements; each ends the first one (a bare line break doesn't: see the end)
BETWEEN = [
    ";", "; ", ";\n", ";\r\n", "\nGO\n", "\n  go  \n", "\nGO -- next batch\n", "; /* ; GO */\n", ";\n-- ; GO\n",
    "\n\nGO\n\n", ";\n/* a /* nested */ comment */\n", "\r\nGO\r\n", "\t;\t",
]
# what may sit between a statement's words; it changes nothing
INSIDE = [" ", "\n", " /* ; */ ", " /* GO */ ", " -- ; GO\n", "\n/* a /* b */ c */\n", "\t"]
SEEDS = range(40)


def statement(rng: random.Random) -> tuple[str, str, object]:
    (value, expected), (name, column) = rng.choice(VALUES), rng.choice(NAMES)
    gap = [rng.choice(INSIDE) for _ in range(3)]
    return f"SELECT{gap[0]}{value}{gap[1]}AS{gap[2]}{name}", column, expected


def script(rng: random.Random) -> tuple[str, list[tuple[str, object]]]:
    parts, expected = [], []
    for i in range(rng.randint(1, 6)):
        text, column, value = statement(rng)
        if i:
            parts.append(rng.choice(BETWEEN))
        parts.append(text)
        expected.append((column, value))
    return rng.choice(["", "\n", "-- top\n", "/* top */ "]) + "".join(parts) + rng.choice(["", ";", "\nGO", "\n"]), \
        expected


@pytest.mark.parametrize("seed", SEEDS)
def test_a_random_script_splits_into_its_statements_and_each_gives_its_own_answer(seed):
    rng = random.Random(seed)
    con = duckdb.connect()
    for _ in range(25):
        sql, expected = script(rng)
        texts, _notes = tsql.translate(sql)
        assert len(texts) == len(expected), (sql, texts)
        for text, (column, value) in zip(texts, expected, strict=True):
            result = con.execute(text)
            assert [d[0] for d in result.description] == [column], (sql, text)
            assert result.fetchall() == [(value,)], (sql, text)


SPLICE = ["'", "''", "N'", '"', "[", "]", "]]", "/*", "*/", "--", "\nGO\n", ";", "\n", "GO", "(", ")", ",", "@",
          "#", "\x00", "\\", "`", "$", "{", "}", "\u202e", "\ufeff", "SELECT", "BEGIN", "END", "WITH", "CASE"]


@pytest.mark.parametrize("seed", SEEDS)
def test_a_script_cut_and_spliced_at_random_translates_or_stops_with_a_named_error(seed):
    rng = random.Random(1000 + seed)
    for _ in range(25):
        sql, _expected = script(rng)
        for _ in range(rng.randint(1, 4)):
            at = rng.randint(0, len(sql))
            if rng.random() < 0.3:
                sql = sql[:at] + sql[at + rng.randint(1, 5):]  # cut
            else:
                sql = sql[:at] + rng.choice(SPLICE) + sql[at:]  # splice
        try:
            texts, _notes = tsql.translate(sql)
        except ValueError as exc:
            message = str(exc)
            assert message and "Traceback" not in message, (sql, message)
            continue
        assert all(isinstance(t, str) and t.strip() for t in texts), (sql, texts)


@pytest.mark.parametrize("sql, inside", [
    ("SELECT 'a\nGO\nb' AS s", "a string"),
    ("SELECT N'a\n  go  \nb' AS s", "a string"),
    ("SELECT 1 AS [a\nGO\nb]", "a bracketed name"),
    ("SELECT 1 AS [a]]\nGO\n]", "a bracketed name"),
    ('SELECT 1 AS "a\nGO\nb"', "a quoted name"),
    ("SELECT 1 /* a\nGO\n*/ AS x", "a comment"),
    ("SELECT 1 /* a /* b */\nGO\n*/ AS x", "a comment"),  # T-SQL nests comments: the first */ closes the inner one
])
def test_a_go_line_inside_a_string_name_or_comment_is_refused_by_name(sql, inside):
    with pytest.raises(ValueError, match=f"the GO on line 2 is inside {inside}"):
        tsql.translate(sql)


@pytest.mark.parametrize("sql, n", [
    ("SELECT 'it''s'\nGO\nSELECT 2", 2),
    ("SELECT 1 /* a /* b */ */\nGO\nSELECT 2", 2),
    ("SELECT 1 -- it's [ \" /*\nGO\nSELECT 2", 2),
    ("SELECT ']]'\nGO\nSELECT 2", 2),
    ("SELECT 1 AS [a]]b]\nGO\nSELECT 2", 2),
])
def test_a_go_line_after_closed_quotes_names_and_comments_splits_there(sql, n):
    assert len(tsql.translate(sql)[0]) == n


def test_the_line_named_counts_every_line_before_it():
    with pytest.raises(ValueError, match="the GO on line 5 is inside a comment"):
        tsql.translate("SELECT 1\nGO\nSELECT 2 /* x\n\nGO\n*/")


@pytest.mark.parametrize("sql, texts", [
    ("SELECT 1; /* c */", ["SELECT 1"]),
    ("SELECT 1; -- c", ["SELECT 1"]),
    ("SELECT 1; /* c */\nSELECT 2; /* d */", ["SELECT 1", "SELECT 2"]),
])
def test_a_comment_after_the_last_semicolon_is_no_statement(sql, texts):
    assert tsql.translate(sql)[0] == texts


@pytest.mark.parametrize("sql, word, line", [
    ("SELECT 1 AS a\nSELECT 2 AS b", "SELECT", 2),
    ("SELECT 1 AS a\n\nINSERT INTO t VALUES (1)", "INSERT", 3),
    ("INSERT INTO t SELECT 1 SELECT 2", "SELECT", 1),
    ("SELECT 1 AS a\nWITH c AS (SELECT 1 AS x) SELECT x FROM c", "WITH", 2),
])
def test_a_statement_with_no_semicolon_before_it_is_refused_by_name(sql, word, line):
    with pytest.raises(ValueError, match=f"the {word} on line {line} starts a new statement with no ; before it"):
        tsql.translate(sql)


@pytest.mark.parametrize("sql", ["SELECT 1 1", "SELECT * FROM t x WITH", "SELECT 1 AS a\nSELECT"])
def test_another_unreadable_statement_keeps_the_parsers_words(sql):
    with pytest.raises(ValueError, match="Could not read the query as T-SQL"):
        tsql.translate(sql)


@pytest.fixture
def engine(tmp_path):
    from fabric_desktop.localdata import LocalData
    data = LocalData(tmp_path)
    yield data.engine(data.create("Fuzz")["id"])
    data.close_all()


def test_a_comment_after_the_last_semicolon_keeps_the_scripts_answer(engine):
    assert engine.execute("SELECT 1 AS a; /* c */", dialect="tsql")["rows"] == [[1]]


def test_a_statement_after_a_comment_is_named_by_its_first_word(engine):
    engine.execute("CREATE TABLE dbo.t (a INT)", dialect="tsql")
    with pytest.raises(Exception) as caught:
        engine.execute("INSERT INTO dbo.t VALUES (1);\n/* a /* b */ c */ -- d\nSELECT 1 / 0; SELECT 2", dialect="tsql")
    assert "Statement 2 of 3 (SELECT) failed" in str(caught.value), caught.value


@pytest.mark.parametrize("sql, n", [
    ("SELECT 1\r\nGO\r\nSELECT 2", 2),
    ("SELECT 1\r\n  go  \r\nSELECT 2\r\n", 2),
    ("SELECT 1\r\nGO -- next\r\nSELECT 2", 2),
    ("SELECT 1\r\nGO\r\n", 1),
])
def test_a_go_line_saved_with_windows_line_endings_splits_there(sql, n):
    assert len(tsql.translate(sql)[0]) == n


def test_go_n_with_windows_line_endings_is_refused_by_name():
    with pytest.raises(ValueError, match="GO 3 .run the batch 3 times. isn't mapped yet"):
        tsql.translate("SELECT 1\r\nGO 3\r\n")


@pytest.mark.parametrize("sql", ["-- it's [ \" /* a note\nSELECT 1 AS a", "/* it's -- [ */ SELECT 1 AS a",
                                 "SELECT 1 AS a -- it's /* [\n", "SELECT 1 /* it's */ AS a"])
def test_a_comment_carries_no_meaning_whatever_it_holds(engine, sql):
    result = engine.execute(sql, dialect="tsql")
    assert result["rows"] == [[1]] and result["columns"][0]["name"] == "a", result
