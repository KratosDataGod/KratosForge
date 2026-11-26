"""secret_redaction's own rules, read directly (mutation testing, batch 327).

test_secret_redaction.py and test_secret_leaks.py follow a secret through a kernel; these pin the pieces: a
value inside a longer one, the escaped bytes form, how much of a part write waits, and RedactedStream's hold,
flush on close and the file methods it answers for the stream under it. conftest forgets every value
around each test.
"""

from __future__ import annotations

import io

from fabric_desktop import secret_redaction
from fabric_desktop.secret_redaction import REDACTED, RedactedStream


def test_a_longer_value_holding_a_shorter_one_is_replaced_whole():
    secret_redaction.remember("abcd")
    secret_redaction.remember("abcdefgh")
    assert secret_redaction.redact("abcdefgh abcd") == f"{REDACTED} {REDACTED}"


def test_a_value_is_replaced_as_its_utf8_bytes_print():
    secret_redaction.remember("pässwörd")
    assert secret_redaction.redact(repr("pässwörd".encode())) == f"b'{REDACTED}'"


def test_a_part_write_waits_only_for_the_start_of_a_value_not_a_whole_one():
    secret_redaction.remember("abcdef")
    assert secret_redaction.partial_tail("xxa") == 1
    assert secret_redaction.partial_tail("xxabc") == 3
    secret_redaction.remember("wxyz")
    assert secret_redaction.partial_tail("12wxyz") == 0


def test_with_no_values_the_longest_is_none_long():
    assert secret_redaction.longest() == 0
    secret_redaction.remember("abcdef")
    assert secret_redaction.longest() == 6


def stream() -> tuple[io.StringIO, RedactedStream]:
    under = io.StringIO()
    return under, RedactedStream(under)


def test_a_line_goes_out_whole_with_no_value_remembered():
    under, out = stream()
    assert out.write("abc\n") == 4
    assert under.getvalue() == "abc\n"


def test_a_part_line_is_held_up_to_hold_characters_and_goes_out_past_it():
    under, out = stream()
    assert RedactedStream.HOLD == 65536
    out.write("a" * RedactedStream.HOLD)
    assert under.getvalue() == ""
    out.write("a")
    assert under.getvalue() == "a" * (RedactedStream.HOLD + 1)
    under, out = stream()
    out.write("line\n" + "b" * (RedactedStream.HOLD - 4))  # held: HOLD - 4 after the line
    assert under.getvalue() == "line\n"


def test_closing_writes_out_what_is_held_and_leaves_the_stream_open():
    under, out = stream()
    out.write("abc")
    assert under.getvalue() == ""
    out.close()
    assert under.getvalue() == "abc" and out.closed and not under.closed


def test_the_stream_answers_as_a_writable_text_stream_of_the_one_under_it():
    class Under(io.StringIO):
        encoding = "cp1252"

        def fileno(self):
            return 7

    out = RedactedStream(Under())
    assert out.writable() is True and out.isatty() is False
    assert out.encoding == "cp1252" and out.fileno() == 7
    assert RedactedStream(io.StringIO()).encoding == "utf-8"
