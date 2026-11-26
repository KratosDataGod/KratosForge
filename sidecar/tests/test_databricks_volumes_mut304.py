"""Mutation batch 304: Unity Catalog volumes on this PC (databricks_volumes). Each test pins one behaviour the
first mutation run showed no test held. Every test has a volume root of its own (LOCALAPPDATA in tmp_path)."""

from __future__ import annotations

import json

import pytest
import sqlglot

from fabric_desktop import databricks_volumes as volumes


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERNAME", "ana")
    return tmp_path / "appdata" / "FabricDesktop" / "volumes" / "main"


def _refused(code: str) -> str:
    with pytest.raises(ValueError) as caught:
        volumes.prepare(code)
    return str(caught.value)


# ---- names

def test_a_name_with_del_is_refused_by_name(here):
    with pytest.raises(ValueError, match="U\\+007F"):
        volumes.check_name("volume", "raw\x7f")
    assert volumes.check_name("volume", "raw\x80") == "raw\x80"  # the next character is not DEL


def test_the_catalog_refusal_names_every_other_session_catalog():
    text = str(volumes.catalog_refusal("sales", "SHOW VOLUMES IN sales.default"))
    assert "(workspace, hive_metastore and spark_catalog name it too)" in text
    assert "because sales is a Unity Catalog catalog" in text


# ---- what a volume reports

def test_a_created_time_kept_beside_the_volume_is_reported_as_kept(here):
    assert volumes.create("default", "landing")
    meta = here / "default" / ".landing.volume.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["created"] = 1234567
    meta.write_text(json.dumps(data), encoding="utf-8")
    assert volumes.describe("default", "landing").created == 1234567


def test_no_volume_folder_at_all_lists_no_volumes(here):
    assert not (here.parent / "main").exists()
    assert volumes.volumes() == []
    assert volumes.volumes("default") == []


# ---- reading a statement

def test_one_character_tokens_and_an_empty_string_are_read():
    tokens = volumes._Lexer("COMMENT ON VOLUME a.b IS ''").tokens
    assert [t[:2] for t in tokens] == [("word", "COMMENT"), ("word", "ON"), ("word", "VOLUME"), ("word", "a"),
                                       ("punct", "."), ("word", "b"), ("word", "IS"), ("string", "")]


@pytest.mark.parametrize("text", ["`", "'", '"'])
def test_a_lone_quote_is_unterminated(text):
    with pytest.raises(ValueError, match="unterminated"):
        volumes._Lexer(text)


def test_a_missing_word_is_a_syntax_error_near_what_was_found():
    text = _refused("DROP VOLUME IF landing")
    assert text == "[PARSE_SYNTAX_ERROR] DROP VOLUME IF landing: expected EXISTS, near 'landing'."


def test_create_volume_with_an_unknown_clause_is_a_syntax_error():
    text = _refused("CREATE VOLUME landing FOO")
    assert text == "[PARSE_SYNTAX_ERROR] CREATE VOLUME landing FOO: expected LOCATION or COMMENT, near 'FOO'."


def test_a_volume_named_alone_needs_no_schema_check():
    _code, written = volumes.prepare("CREATE VOLUME landing")
    (steps,) = written.values()
    assert len(steps) == 2 and not any("SCHEMA_NOT_FOUND" in s for s in steps)
    _code, written = volumes.prepare("CREATE VOLUME default.landing")
    (steps,) = written.values()
    assert len(steps) == 3 and "SCHEMA_NOT_FOUND" in steps[0]


def test_a_rename_to_a_bare_name_is_not_checked_for_a_schema_move():
    _code, written = volumes.prepare("ALTER VOLUME landing RENAME TO raw")
    (steps,) = written.values()
    assert len(steps) == 2 and not any("can't move a volume" in s for s in steps)


def test_tags_and_owners_are_refused_each_with_their_own_reason():
    assert "this PC keeps no tags on a volume" in _refused("ALTER VOLUME landing SET TAGS ('a' = 'b')")
    owner = _refused("ALTER VOLUME landing OWNER TO `bo`")
    assert "belongs to the person signed in to this PC" in owner and "tags" not in owner


def test_list_alone_and_a_table_named_volume_are_not_volume_statements():
    assert volumes.prepare("LIST") == ("LIST", {})
    code = "CREATE TABLE volume (x INT)"
    assert volumes.prepare(code) == (code, {})


def test_a_statement_is_named_whole_up_to_80_characters_then_cut_to_77_and_dots():
    def statement(length):
        head = "DROP VOLUME IF "
        return head + "v" * (length - len(head))
    s80, s81 = statement(80), statement(81)
    assert f"[PARSE_SYNTAX_ERROR] {s80}: expected EXISTS" in _refused(s80)
    assert f"[PARSE_SYNTAX_ERROR] {s81[:77]}...: expected EXISTS" in _refused(s81)


def test_only_a_placeholder_string_is_a_placeholder():
    assert volumes.placeholder(sqlglot.parse_one("SELECT 1")) is None
    assert volumes.placeholder(sqlglot.parse_one("SELECT 'other'")) is None
    key = f"{volumes.PLACEHOLDER}0_abc"
    assert volumes.placeholder(sqlglot.parse_one(f"SELECT '{key}'")) == key


# ---- a change runs once

def test_a_change_with_a_token_runs_once_however_often_it_is_asked(here, monkeypatch):
    monkeypatch.setattr(volumes, "_done", {})
    assert volumes._answer("create", "t1", "default", "landing", None, False) == ""
    assert volumes._answer("create", "t1", "default", "landing", None, False) == ""
    assert "t1" in volumes._done and "" not in volumes._done
    assert "VOLUME_ALREADY_EXISTS" in volumes._answer("create", "", "default", "landing", None, False)
    assert "" not in volumes._done


def test_the_answers_kept_are_cleared_once_there_are_more_than_1000(here, monkeypatch):
    monkeypatch.setattr(volumes, "_done", {f"k{i}": "" for i in range(1001)})
    answer = volumes._answer("exists", "new", "default", "nothing", None, None)
    assert answer and volumes._done == {"new": answer}
    monkeypatch.setattr(volumes, "_done", {f"k{i}": "" for i in range(1000)})
    volumes._answer("exists", "new", "default", "nothing", None, None)
    assert len(volumes._done) == 1001 and "new" in volumes._done


# ---- read_files

def test_csv_files_with_their_columns_in_another_order_are_read_by_name(here):
    assert volumes.create("default", "landing")
    folder = here / "default" / "landing"
    (folder / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (folder / "b.csv").write_text("y,x\n4,3\n", encoding="utf-8")
    table = volumes.read_table("/Volumes/main/default/landing", {})
    rows = sorted(zip(table.column("x").to_pylist(), table.column("y").to_pylist(), strict=True))
    assert rows == [(1, 2), (3, 4)]


def test_multiline_json_is_read_when_asked(here):
    assert volumes.create("default", "landing")
    (here / "default" / "landing" / "a.json").write_text('[\n  {"x": 1},\n  {"x": 2}\n]\n', encoding="utf-8")
    table = volumes.read_table("/Volumes/main/default/landing/a.json", {"multiLine": "true"})
    assert table.column("x").to_pylist() == [1, 2]
