"""Mutation batch 489: the NUL guard refuses a NUL at the very start, in an unclosed or a
dollar-quoted string, in text that leaves it no stand-in character, and in text the tokenizer
can't read."""
from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import duckdb_nul as dn


@pytest.mark.parametrize("text", ["\x00SELECT 1", "SELECT 'a\x00b", "SELECT $$a\x00b'$$"])
def test_a_nul_first_in_an_unclosed_string_or_in_a_dollar_string_is_refused(text):
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        dn.guard(text)


def test_text_holding_every_stand_in_character_is_refused():
    every = "".join(map(chr, range(0xE000, 0xF900)))
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        dn.guard(f"SELECT '{every}\x00'")


def test_text_the_tokenizer_cant_read_is_refused(monkeypatch):
    def broken(_text):
        raise RuntimeError("can't read it")
    monkeypatch.setattr(dn.duckdb, "tokenize", broken)
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        dn.guard("SELECT 'a\x00b'")


def test_a_nul_in_a_string_that_is_the_first_token_is_kept():
    assert dn.guard("'a\x00b'") == "('a' || CHR(0) || 'b')"


def test_the_first_stand_in_character_is_used_when_the_text_holds_all_the_others():
    others = "".join(map(chr, range(0xE001, 0xF900)))
    assert dn.guard(f"SELECT '{others}\x00'") == f"SELECT ('{others}' || CHR(0))"


def test_sql_text_passed_by_keyword_is_guarded_too():
    con = duckdb.connect()
    try:
        with pytest.raises(duckdb.ParserException, match="NUL character"):
            con.execute(query="SELECT 1 /*\x00*/")
    finally:
        con.close()


def test_installing_again_wraps_nothing_twice_and_adds_no_entry_point_duckdb_lacks():
    dn.install()
    owners = [(duckdb, dn._MODULE), (duckdb.DuckDBPyConnection, dn._CONNECTION)]
    for owner, names in owners:
        for name in names:
            call = getattr(owner, name, None)
            if call is None:
                continue
            assert getattr(call, "__kf_nul_guard__", False), name
            inner = call.__wrapped__
            assert inner is not None and not getattr(inner, "__kf_nul_guard__", False), name
