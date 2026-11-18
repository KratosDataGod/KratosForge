"""Spark's regular expressions and string results on this PC (spark_strings.py, WR-107): what mutation testing
(batch 89, docs/quality/mutation-testing.md) showed no test pinned. Java's pattern syntax as RE2 reads it
(\\Q...\\E, \\pL, \\h and \\v in and out of a class, a ] first in a class, flag groups that turn one off),
the refusals that name what RE2 can't read, a replacement's escapes and groups past 9, the DataFrame wrapper's
group check, and LIKE's backslash escape."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import spark_strings as s
from fabric_desktop.notebook import Kernel

_V = r"\n\x{0B}\f\r\x{85}\x{2028}\x{2029}"
_H = r" \t\x{A0}\x{1680}\x{180E}\x{2000}-\x{200A}\x{202F}\x{205F}\x{3000}"


def pattern(text: str) -> str:
    return s.java_pattern(text, "rlike")[0]


def refused(text: str, says: str) -> str:
    with pytest.raises(ValueError, match="can't use") as caught:
        s.java_pattern(text, "rlike")
    message = str(caught.value)
    assert says in message, message
    return message


# ---- Java's syntax as RE2 reads it

def test_quoted_text_runs_to_e_and_the_pattern_goes_on_after_it():
    assert pattern(r"\Qa.b\Ec") == r"a\.bc"


def test_quoted_text_keeps_a_character_past_ascii_as_it_is():
    assert pattern("\\Q\x80\\E") == "\x80"
    assert pattern("\\Q\x7f\\E") == "\\\x7f"  # the last ASCII character is escaped like any other


def test_a_one_letter_property_takes_just_that_letter():
    assert pattern(r"\pLx") == r"\p{L}x"
    assert pattern(r"\PLx") == r"\P{L}x"


def test_vertical_space_outside_and_inside_a_class():
    assert pattern(r"\v") == f"[{_V}]"
    assert pattern(r"\V") == f"[^{_V}]"
    assert pattern(r"[\v]") == f"[{_V}]"


def test_horizontal_space_outside_and_inside_a_class():
    assert pattern(r"\h") == f"[{_H}]"
    assert pattern(r"\H") == f"[^{_H}]"
    assert pattern(r"[\h]") == f"[{_H}]"


def test_a_bracket_first_in_a_class_is_a_character_and_the_class_runs_to_the_next_one():
    assert s.java_pattern("[]a]x", "rlike") == ("[]a]x", 0, {})
    assert s.java_pattern("[ab(]", "rlike")[1] == 0  # the ( is in the class, not a group
    assert s.java_pattern("[](]", "rlike") == ("[](]", 0, {})  # the first ] doesn't close the class


def test_a_flag_group_that_turns_a_flag_off():
    assert pattern("(?i-m:a)") == "(?i-m:a)"


@pytest.mark.parametrize("text, says", [
    (r"\p{Alpha", r"'\p{' without its closing brace"),
    (r"\0", r"'\0' without an octal number after it"),
    (r"\G", r"'\G'"),
    (r"[\H]", r"'\H' inside [ ]"),
    (r"[\V]", r"'\V' inside [ ]"),
    (r"\c", r"'\c' without a letter after it"),
    ("(?<>a)", "'(?<' without a group name"),
])
def test_what_re2_cant_read_is_refused_by_name(text, says):
    refused(text, says)


def test_a_refusal_with_a_hint_adds_or_run_it_in_fabric_and_one_without_says_run_it_in_fabric():
    assert refused(r"\1", "a backreference").endswith(
        "RE2 can't match text a group matched earlier. Or run it in Fabric.")
    assert refused(r"\G", r"'\G'").endswith("doesn't. Run it in Fabric.")


# ---- a replacement

def test_an_escaped_character_in_a_replacement_is_that_character():
    assert s.java_replacement(r"x\$y", "regexp_replace", 0, {}) == "x$y"
    assert s.java_replacement(r"a\\b", "regexp_replace", 0, {}) == r"a\\b"  # an escaped backslash stays one


def test_a_replacement_group_past_9_is_refused_on_this_pc():
    with pytest.raises(ValueError, match="refers to group 12. On this PC a replacement can use groups 0 to 9"):
        s.java_replacement("$12", "regexp_replace", 12, {})


# ---- the DataFrame wrapper

def _wrapped(**options):
    seen = []

    def fn(*args):
        seen.append(args)
        return "ran"

    return s.java_regex_function("regexp_replace", fn, 1, **options), seen


def test_the_wrapper_rewrites_the_replacement_unless_the_function_reads_javas_itself():
    call, seen = _wrapped(replacement_at=2)
    call("c", "(a)", "$1")
    assert seen[-1] == ("c", "(a)", "\\1")
    call, seen = _wrapped(replacement_at=2, own_replacement=True)
    call("c", "(a)", "$1")
    assert seen[-1] == ("c", "(a)", "$1")


def test_the_wrapper_passes_a_call_without_a_pattern_straight_on():
    call, seen = _wrapped()
    assert call("c") == "ran" and seen == [("c",)]


@pytest.mark.parametrize("regex, index, says", [
    ("a", 1, ("the pattern has 0 groups, so nothing ran: Spark stops on it too where a row matches. "
              "Use 0 for the whole match.")),
    ("(a)", 2, ("the pattern has 1 group, so nothing ran: Spark stops on it too where a row matches. "
                "Use a group from 0 to 1.")),
    ("(a)" * 12, 10, "Use a group from 0 to 9."),
])
def test_the_wrapper_refuses_a_group_the_pattern_or_this_pc_doesnt_have(regex, index, says):
    call, _ = _wrapped(group_at=2)
    with pytest.raises(ValueError, match=f"asks for group {index}") as caught:
        call("c", regex, index)
    assert str(caught.value).endswith(says)


@pytest.mark.parametrize("regex, index", [("(a)", 1), ("(a)", 0), ("(a)" * 12, 9)])
def test_the_wrapper_takes_a_group_the_pattern_has_up_to_9(regex, index):
    call, _ = _wrapped(group_at=2)
    assert call("c", regex, index) == "ran"


# ---- LIKE's escape, through spark.sql

_RUN = """
import json
out = []
for c in {cases!r}:
    r = spark.sql('SELECT ' + c + ' AS v')
    out.append(r.collect()[0][0])
print('RESULT' + json.dumps(out))
"""


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LOCALAPPDATA", str(home))
        mp.setenv("HOME", str(home))
        yield Kernel()


def test_like_reads_a_backslash_as_its_escape_unless_another_is_given(kernel):
    cases = [r"'a%b' LIKE 'a\\%b'", r"'axb' LIKE 'a\\%b'", r"'a\\b' LIKE 'a\\\\b' ESCAPE '!'",
             r"'a!b' LIKE 'a!!b' ESCAPE '!'", r"'a%b' LIKE concat('a\\', '%b')"]
    out = kernel.run(_RUN.format(cases=cases))
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    # the last case takes its pattern from an expression
    assert json.loads(text.split("RESULT", 1)[1]) == [True, False, False, True, True]
