"""duckdb_nul: an unclosed string, text that holds every stand-in character, and text the tokenizer can't read
are each refused before anything runs."""

from __future__ import annotations

import types

import duckdb
import pytest

from fabric_desktop import duckdb_nul


def test_a_nul_in_a_string_that_never_closes_is_refused():
    with pytest.raises(duckdb.ParserException, match="holds a NUL character"):
        duckdb_nul.guard("SELECT 'a\x00b")


def test_the_closing_quote_is_found_past_doubled_quotes():
    data = b"'it''s' x"
    assert duckdb_nul._literal_end(data, 0) == 6
    assert duckdb_nul._literal_end(b"'it''s", 0) == -1
    assert duckdb_nul._literal_end(b"'", 0) == -1


def test_a_closed_string_after_doubled_quotes_keeps_its_nul_as_chr0():
    out = duckdb_nul.guard("SELECT 'it''s\x00x' AS v")
    assert out == "SELECT ('it''s' || CHR(0) || 'x') AS v"
    with duckdb.connect() as con:
        assert con.execute(out).fetchone() == ("it's\x00x",)


def test_text_holding_every_stand_in_character_is_refused():
    every = "".join(map(chr, range(0xE000, 0xF900)))
    assert duckdb_nul._stand_in(every) is None
    with pytest.raises(duckdb.ParserException, match="holds a NUL character"):
        duckdb_nul.guard(f"SELECT '{every}\x00'")


def test_the_first_free_stand_in_is_taken():
    assert duckdb_nul._stand_in("SELECT 1") == ""
    assert duckdb_nul._stand_in("") == ""


def test_text_the_tokenizer_cant_read_is_refused(monkeypatch):
    def tokenize(text):
        raise RuntimeError("unreadable")

    fake = types.SimpleNamespace(tokenize=tokenize, ParserException=duckdb.ParserException)
    monkeypatch.setattr(duckdb_nul, "duckdb", fake)  # the module's own name: the real duckdb stays as it is
    with pytest.raises(duckdb.ParserException, match="holds a NUL character") as raised:
        duckdb_nul.guard("SELECT 'a\x00'")
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_text_without_a_nul_never_reaches_the_tokenizer(monkeypatch):
    monkeypatch.setattr(duckdb_nul, "duckdb", types.SimpleNamespace())  # any use of it would fail
    text = "SELECT 'a'"
    assert duckdb_nul.guard(text) is text
