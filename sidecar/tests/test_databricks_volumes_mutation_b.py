"""databricks_volumes: the edges the earlier tests left open (mutation batch 476). The statement reader's
refusals and their wording, a statement's shown text cut at 80 characters, a change run once per token, the
stored creation time, and read_files' union of a folder's CSV files and its multiLine option."""

from __future__ import annotations

import json
import threading

import pytest
import sqlglot

from fabric_desktop import databricks_volumes as volumes
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.engines.local_sql import DuckDBSession


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERNAME", "ana")
    return tmp_path / "appdata" / "FabricDesktop" / "volumes" / "main"


def _refusal(code):
    with pytest.raises(ValueError) as caught:
        volumes.prepare(code)
    return str(caught.value)


# ---- names

def test_del_is_refused_in_a_name_and_the_next_character_is_kept():
    with pytest.raises(ValueError, match="U\\+007F"):
        volumes.check_name("volume", "a\x7fb")
    assert volumes.check_name("volume", "a\x80b") == "a\x80b"


def test_another_catalog_names_the_three_others_main_keeps_volumes_in():
    text = str(volumes.catalog_refusal("prod", "X"))
    assert "(workspace, hive_metastore and spark_catalog name it too)" in text


# ---- the statement reader

def test_an_unknown_clause_after_create_volume_is_a_syntax_error_and_doesnt_hang():
    box = {}

    def run():
        try:
            volumes.prepare("CREATE VOLUME v FOO")
        except ValueError as exc:
            box["error"] = str(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(20)
    assert not worker.is_alive(), "CREATE VOLUME v FOO never finished"
    assert box["error"] == "[PARSE_SYNTAX_ERROR] CREATE VOLUME v FOO: expected LOCATION or COMMENT, near 'FOO'."


def test_a_missing_word_is_named_and_the_error_shows_the_token_it_stopped_at():
    assert _refusal("DROP VOLUME IF v") == "[PARSE_SYNTAX_ERROR] DROP VOLUME IF v: expected EXISTS, near 'v'."
    assert _refusal("DROP VOLUME v extra") == "[PARSE_SYNTAX_ERROR] DROP VOLUME v extra: unexpected text, near 'extra'."
    assert _refusal("DROP VOLUME IF") == ("[PARSE_SYNTAX_ERROR] DROP VOLUME IF: expected EXISTS, near 'the end of "
                                          "the statement'.")


def test_tags_and_owner_refusals_each_say_their_own_reason():
    tags = _refusal("ALTER VOLUME v SET TAGS ('a' = 'b')")
    owner = _refusal("ALTER VOLUME v OWNER TO bob")
    assert "this PC keeps no tags on a volume" in tags and "principal" not in tags
    assert "the only principal it has" in owner and "tags" not in owner


def test_an_unterminated_name_or_string_leaves_the_cell_to_sqlglot():
    for code in ("CREATE VOLUME `v", "LIST '/Volumes/main/default/v"):
        assert volumes.prepare(code) == (code, {})


def test_an_empty_string_is_read_as_one():
    code, written = volumes.prepare("COMMENT ON VOLUME v IS ''")
    assert len(written) == 1 and code.startswith("SELECT '")


def test_a_one_word_list_isnt_a_volume_statement_and_list_with_a_path_is():
    assert volumes.prepare("LIST") == ("LIST", {})
    assert len(volumes.prepare("LIST '/Volumes/main/default/v'")[1]) == 1


def test_create_with_volume_as_its_third_word_isnt_a_volume_statement():
    assert volumes.prepare("CREATE TABLE volume (x INT)") == ("CREATE TABLE volume (x INT)", {})


def test_a_statement_is_shown_whole_up_to_80_characters_then_cut_to_77_and_dots():
    head = "DROP VOLUME a.b.c."
    exact = head + "d" * (80 - len(head))
    assert len(exact) == 80
    assert _refusal(exact).startswith(f"[PARSE_SYNTAX_ERROR] {exact}: a volume is named")
    longer = exact + "d"
    assert _refusal(longer).startswith(f"[PARSE_SYNTAX_ERROR] {longer[:77]}...: a volume is named")


def test_only_a_placeholder_string_is_a_placeholder():
    key = "__kf_volume_statement_0_abc"
    assert volumes.placeholder(sqlglot.parse_one(f"SELECT '{key}'")) == key
    assert volumes.placeholder(sqlglot.parse_one("SELECT 1")) is None
    assert volumes.placeholder(sqlglot.parse_one("SELECT 'other'")) is None


def test_a_volume_in_another_schema_renames_inside_that_schema(here):
    volumes.create("sales", "v")
    session = DuckDBSession(SessionConfig("nb"))
    try:
        result = session.execute(CellRequest("ALTER VOLUME sales.v RENAME TO w", language="sql",
                                             dialect="databricks"))
    finally:
        session.close()
    assert result.status == "ok", result.outputs[-1]
    assert (here / "sales" / "w").is_dir() and not (here / "sales" / "v").exists()


# ---- the functions the statements call

def test_a_change_runs_once_per_token(here, monkeypatch):
    monkeypatch.setattr(volumes, "_done", {})
    assert volumes._answer("create", "tok", "default", "v", None, False) == ""
    assert volumes._answer("create", "tok", "default", "v", None, False) == ""  # the answer kept, not run again
    assert "[VOLUME_ALREADY_EXISTS]" in volumes._answer("create", "", "default", "v", None, False)
    assert volumes._done == {"tok": ""}


def test_the_answers_kept_are_cleared_past_1000(here, monkeypatch):
    monkeypatch.setattr(volumes, "_done", {str(i): "" for i in range(1001)})
    volumes._answer("exists", "new", "default", "v", None, None)
    assert list(volumes._done) == ["new"] and "cannot be found" in volumes._done["new"]
    monkeypatch.setattr(volumes, "_done", {str(i): "" for i in range(1000)})
    volumes._answer("exists", "new", "default", "v", None, None)
    assert len(volumes._done) == 1001


def test_no_volume_root_lists_no_volumes(here):
    assert not here.exists()
    assert volumes.volumes() == []


def test_a_stored_creation_time_is_kept(here):
    volumes.create("default", "v")
    meta = here / "default" / ".v.volume.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["created"] = 123
    meta.write_text(json.dumps(data), encoding="utf-8")
    assert volumes.describe("default", "v").created == 123


# ---- read_files

def test_a_folder_of_csv_files_is_read_by_column_name(here):
    volumes.create("default", "v")
    folder = here / "default" / "v" / "d"
    folder.mkdir()
    (folder / "a.csv").write_bytes(b"x,y\n1,2\n")
    (folder / "b.csv").write_bytes(b"y,x\n3,4\n")
    table = volumes.read_table("/Volumes/main/default/v/d", {})
    rows = sorted(zip(table.column("x").to_pylist(), table.column("y").to_pylist(), strict=True))
    assert rows == [(1, 2), (4, 3)]


def test_multiline_json_is_read_when_asked(here):
    volumes.create("default", "v")
    (here / "default" / "v" / "m.json").write_bytes(b'[\n  {"k": 1},\n  {"k": 2}\n]\n')
    table = volumes.read_table("/Volumes/main/default/v/m.json", {"multiLine": "true"})
    assert table.column("k").to_pylist() == [1, 2]
