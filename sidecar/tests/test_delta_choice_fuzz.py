"""Property and fuzz tests for the per-table Delta choice: with_delta_choice, which edits an entity file's
delta_features, delta_readers and delta_accepted line by line, and delta_profile.confirm_choice, which
decides whether a save that changes them needs the table's name typed.

The cases are generated from fixed seeds (hypothesis isn't one of the engine's test packages), so a
failure names its seed and case and comes back the same on every run.
"""

from __future__ import annotations

import itertools
import json
import random
import re
import tomllib

import pytest

from fabric_desktop import delta_profile as P
from fabric_desktop.meta.project import with_delta_choice

KEYS = P.TABLE_KEYS
CHOICES = (None, *P.TABLE_CHOICES)
READERS = tuple(P.READERS)

# ---- Entity files, generated

# Characters a TOML string or comment may hold, the awkward ones included: quotes, a backslash, accents,
# characters outside the BMP, and U+2028, U+2029 and U+0085, which str.splitlines() counts as line breaks
# and TOML doesn't.
_CHARS = ["a", "z", " ", "'", '"', "\\", "#", "=", "[", "]", "{", "}", ",", ".", "\u00e9", "\u00df", "\u65e5",
          "\u672c", "\U0001F600", "\u03a9", "\u2028", "\u2029", "\x85", "\u00a0", "\t"]
_SECTIONS = ["load", "contract", "source", "scd", "model", "model.extra", "quality", "schedule"]
_ARRAYS = ["columns", "checks", "tests"]


def _text(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choice(_CHARS) for _ in range(rng.randint(0, n)))


def _string(rng: random.Random) -> str:
    """A one-line TOML string, basic or literal, holding awkward characters."""
    s = _text(rng)
    if rng.random() < 0.3:
        literal = "".join(c for c in s if c not in "'\t")
        return f"'{literal}'"
    return _basic(s, escape_all=rng.random() < 0.3)


def _basic(s: str, escape_all: bool) -> str:
    """``s`` as a TOML basic string; with ``escape_all``, every character past ASCII as a \\u or \\U escape."""
    out = []
    for c in s:
        if c in '"\\':
            out.append("\\" + c)
        elif c == "\t" or (escape_all and ord(c) > 0x7E):
            out.append(f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _value(rng: random.Random, depth: int = 0) -> str:
    """A one-line TOML value: a string, number, bool, date, array or inline table."""
    kind = rng.randrange(7 if depth < 2 else 4)
    if kind == 0:
        return _string(rng)
    if kind == 1:
        return str(rng.randint(-5, 10_000))
    if kind == 2:
        return rng.choice(["true", "false", "1.5", "2026-11-17", "1e3"])
    if kind == 3:
        return _string(rng)
    if kind == 4:
        return "[" + ", ".join(_value(rng, depth + 1) for _ in range(rng.randint(0, 3))) + "]"
    keys = rng.sample(["a", "b", '"c d"', "'e'", "f.g"], rng.randint(0, 3))
    return "{ " + ", ".join(f"{k} = {_value(rng, depth + 1)}" for k in keys) + " }" if keys else "{}"


def _eq(rng: random.Random) -> str:
    return rng.choice(["=", " = ", "  =  ", "\t=\t", " ="])


def _tail(rng: random.Random) -> str:
    return rng.choice(["", "", "", "  # " + _text(rng).replace("\n", ""), " #"])


def _comment(rng: random.Random) -> str:
    return rng.choice(["", "  ", "\t"]) + "#" + rng.choice(["", " "]) + rng.choice([
        _text(rng), "delta_features = \"every_reader\"", "[not a section]", "name = \"nope\"",
        "delta_readers = [", "key = 'value'"])


def _multiline(rng: random.Random, tricky: bool) -> list[str]:
    """A multi-line string over several lines. A tricky one has lines that look like a delta_ key or a
    [section] header."""
    quote = rng.choice(['"""', "'''"])
    inner = ['delta_features = "every_reader"', "[fake]", 'name = "other"', "delta_readers = []"] if tricky \
        else ["plain words", "  indented " + "\u00e9\u00df", ""]
    body = rng.sample(inner, rng.randint(1, len(inner)))
    return [f"{rng.choice(['notes', 'sql', 'doc'])} = {quote}", *body, quote]


def _delta_value(rng: random.Random, key: str) -> str:
    if key == "delta_features":
        return json.dumps(rng.choice([*P.TABLE_CHOICES, "fast"]))
    return json.dumps(rng.sample(READERS, rng.randint(0, 3)))


def entity_file(rng: random.Random, hostile: bool = False) -> tuple[str, set[int]]:
    """An entity file's text and the indexes of its top-level delta_ lines (the lines with_delta_choice
    replaces). Unless ``hostile``, each delta_ setting is on one line and nothing else at the top of the
    file can be mistaken for one, so the file is one with_delta_choice must be able to edit."""
    has_name = rng.random() < 0.8
    items: list[tuple[str, list[str]]] = []  # (kind, lines)
    if has_name:
        items.append(("name", [f"name{_eq(rng)}{rng.choice([_basic('gold.' + _text(rng, 4), False), _string(rng)])}"
                               + _tail(rng)]))
    for key in rng.sample(["owner", "layer", "description", "mode", "tags", "src", "meta.tag", '"odd key"',
                           "'lit'", "x-y"], rng.randint(0, 6)):
        if key == "src":
            value = "{ entity = " + _string(rng) + ", rename = { \"a b\" = " + _string(rng) + " } }"
        else:
            value = _value(rng)
        items.append(("key", [f"{key}{_eq(rng)}{value}{_tail(rng)}"]))
    if has_name and rng.random() < 0.3:  # a multi-line array: its inner lines aren't keys
        items.append(("key", ["columns_order = [", *[f"  {_string(rng)}," for _ in range(rng.randint(0, 3))], "]"]))
    if has_name and rng.random() < 0.2:
        items.append(("key", _multiline(rng, tricky=False)))
    for key in KEYS:
        if rng.random() < 0.5:
            indent = rng.choice(["", "", " ", "\t"])
            items.append(("delta", [f"{indent}{key}{_eq(rng)}{_delta_value(rng, key)}{_tail(rng)}"]))
    for _ in range(rng.randint(0, 5)):
        items.append(("filler", [rng.choice(["", "   ", _comment(rng)])]))
    if hostile:
        tricks = [
            ["delta_readers = [", '  "sql_endpoint",', "]"],
            ['"delta_features" = "every_reader"'],
            ["delta_accepted = [ # the confirmed ones", '"pipelines" ]'],
            _multiline(rng, tricky=True),
            ["delta_features.x = 1"],
            ["columns_order = [", '  "a",', "]"],
        ]
        items.append(("hostile", rng.choice(tricks)))
    top_items = items[:1] + rng.sample(items[1:], len(items) - 1) if rng.random() < 0.5 \
        else rng.sample(items, len(items))
    if not has_name and not hostile:
        # Without a name, the new lines go after the last top-level key, so a multi-line value can't be last.
        assert all(len(lines) == 1 for kind, lines in top_items if kind == "key")

    lines: list[str] = []
    delta_at: set[int] = set()
    for kind, item in top_items:
        if kind == "delta":
            delta_at.add(len(lines))
        lines += item
    sections = rng.sample(_SECTIONS, rng.randint(0, 4))
    arrays = rng.sample(_ARRAYS, rng.randint(0, 2))
    headers = [f"[{s}]" for s in sections] + [f"[[{a}]]" for a in arrays for _ in range(rng.randint(1, 3))]
    rng.shuffle(headers)
    for header in headers:
        if rng.random() < 0.4:
            lines.append(rng.choice(["", _comment(rng)]))
        if not header.startswith("[[") and rng.random() < 0.3:
            header = f"[ {header[1:-1]} ]"
        lines.append(rng.choice(["", "  "]) + header + _tail(rng))
        for key in rng.sample(["mode", "keys", "delta_features", "delta_readers", "name", "watermark", "note",
                               "inline", "rules"], rng.randint(0, 4)):
            if key == "inline":
                lines.append(f"inline = {{ a = {_string(rng)}, b = [1, 2], c = {{ d = true }} }}")
            elif key == "rules":
                lines += _multiline(rng, tricky=True)
            else:
                lines.append(f"{key}{_eq(rng)}{_value(rng)}{_tail(rng)}")
            if rng.random() < 0.2:
                lines.append(_comment(rng))
    newline = "\r\n" if rng.random() < 0.3 else "\n"
    text = newline.join(lines)
    if lines and rng.random() < 0.8:
        text += newline
    return text, delta_at


def _lines(text: str) -> list[str]:
    """The file's lines as TOML counts them: only LF or CRLF ends one. The last is empty when the file
    ends with a newline."""
    return [line.removesuffix("\r") for line in text.split("\n")]


def _args(rng: random.Random) -> tuple[str | None, list[str] | None, list[str]]:
    features = rng.choice([*CHOICES, "odd \"value\" \u00e9"])
    readers = rng.choice([None, [], rng.sample(READERS, rng.randint(1, 4)), ["x\u2028y", 'q"uote']])
    accepted = rng.choice([[], rng.sample(READERS, rng.randint(0, 3))])
    return features, readers, accepted


def _expected(before: dict, features: str | None, readers: list[str] | None, accepted: list[str]) -> dict:
    want = {k: v for k, v in before.items() if k not in KEYS}
    if features is not None:
        want["delta_features"] = features
    if readers:
        want["delta_readers"] = list(readers)
    if accepted:
        want["delta_accepted"] = list(accepted)
    return want


def _new_lines(features: str | None, readers: list[str] | None, accepted: list[str]) -> list[str]:
    return ([f"delta_features = {json.dumps(features)}"] if features is not None else []) \
        + ([f"delta_readers = {json.dumps(readers)}"] if readers else []) \
        + ([f"delta_accepted = {json.dumps(accepted)}"] if accepted else [])


def _check_edit(text: str, delta_at: set[int], args: tuple, out: str) -> None:
    features, readers, accepted = args
    before = tomllib.loads(text)
    # (a) It parses, and only the three keys changed.
    assert tomllib.loads(out) == _expected(before, *args)
    # Line endings: every line ends in LF, whatever the file had (jsonfile.write_text then writes the OS's).
    # A CRLF file loses nothing by it: TOML reads CRLF inside a multi-line string as LF.
    assert "\r" not in out
    # (b) Every other line keeps its content and its order, and the new ones are together, once. Compared
    # as text, so that a last line with no newline after it compares too.
    old, new = _lines(text), out.split("\n")
    added = _new_lines(*args)
    at = next((i for i in range(len(new) - len(added) + 1) if new[i:i + len(added)] == added), None)
    assert at is not None, "the new delta_ lines are not together"
    assert "\n".join(new[:at] + new[at + len(added):]) == "\n".join(line for i, line in enumerate(old) if i not in delta_at)
    # They go after the name, when there is one.
    names = [i for i, line in enumerate(new[:at]) if re.match(r"^name\s*=", line.strip())]
    if added and names and not any(line.lstrip().startswith("[") for line in new[:at]):
        assert at == names[0] + 1
    # (d) Idempotent.
    assert with_delta_choice(out, *args) == out


CASES = 2500


@pytest.mark.parametrize("seed", range(5))
def test_with_delta_choice_edits_any_file_whose_settings_are_each_on_one_line(seed):
    rng = random.Random(seed)
    for case in range(CASES // 5):
        text, delta_at = entity_file(rng)
        tomllib.loads(text)  # the generator's own check: a file that doesn't parse is the generator's mistake
        args = _args(rng)
        try:
            out = with_delta_choice(text, *args)
        except Exception as exc:
            raise AssertionError(f"seed {seed} case {case}: refused {text!r} with {args!r}") from exc
        try:
            _check_edit(text, delta_at, args, out)
        except AssertionError as exc:
            raise AssertionError(f"seed {seed} case {case}: {text!r} with {args!r} gave {out!r}") from exc


@pytest.mark.parametrize("seed", range(3))
def test_with_delta_choice_refuses_with_value_error_or_edits_right(seed):
    rng = random.Random(1000 + seed)
    refused = 0
    for case in range(300):
        text, delta_at = entity_file(rng, hostile=True)
        args = _args(rng)
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            # A file that doesn't parse (a hostile line can clash with a key) is refused, as a ValueError.
            with pytest.raises(ValueError):
                with_delta_choice(text, *args)
            refused += 1
            continue
        try:
            out = with_delta_choice(text, *args)
        except ValueError as exc:  # (c) only ever ValueError, and then nothing was changed
            assert "Put each delta_ setting on one line" in str(exc) or isinstance(exc, tomllib.TOMLDecodeError)
            refused += 1
            continue
        try:
            _check_edit(text, delta_at, args, out)
        except AssertionError as exc:
            raise AssertionError(f"seed {seed} case {case}: {text!r} with {args!r} gave {out!r}") from exc
    assert refused  # the hostile layouts are refused often enough for the branch to be tested


def test_with_delta_choice_on_garbage_raises_only_value_error():
    rng = random.Random(7)
    pieces = ["name = \"a.b\"", "delta_features = ", "[", "]", "\"", "'''", "\"\"\"", "\r\n", "\n", "\r", "=",
              "delta_readers = [", "#", "\x00", "\u2028", "x", " ", "{", "}"]
    for _ in range(1500):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        try:
            out = with_delta_choice(text, *_args(rng))
        except ValueError:
            continue
        tomllib.loads(out)


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_a_line_separator_inside_a_string_or_comment_is_not_a_line_break(sep):
    # Regression: the file was split with str.splitlines(), which also breaks at U+2028, U+2029 and
    # U+0085. TOML doesn't, so a description or comment holding one was cut in two and the edit refused.
    text = f'name = "gold.x"\ndescription = "a{sep}b"\n# note{sep}more\n\n[load]\nmode = "full"\n'
    out = with_delta_choice(text, "every_reader", None, [])
    assert out == text.replace('"gold.x"\n', '"gold.x"\ndelta_features = "every_reader"\n', 1)
    # A comment that ended in one was changed without a word: that character became a blank line.
    text = f'name = "gold.x"\n# note{sep}\n[load]\n'
    assert with_delta_choice(text, None, None, []) == text


def test_a_crlf_file_comes_back_with_lf_and_the_same_content():
    text = 'name = "gold.x"\r\ndelta_features = "column_mapping"\r\nsql = """\r\nselect 1\r\n"""\r\n\r\n[load]\r\n'
    out = with_delta_choice(text, "every_reader", ["pipelines"], ["pipelines"])
    assert out == ('name = "gold.x"\ndelta_features = "every_reader"\ndelta_readers = ["pipelines"]\n'
                   'delta_accepted = ["pipelines"]\nsql = """\nselect 1\n"""\n\n[load]\n')
    assert tomllib.loads(out)["sql"] == tomllib.loads(text)["sql"] == "select 1\n"


# ---- confirm_choice

# Which matrix features each choice allows, said again here rather than read from delta_profile.
_ALLOWS = {None: (P.DELETION_VECTORS, P.CM_NAME), "every_reader": (), "deletion_vectors": (P.DELETION_VECTORS,),
           "column_mapping": (P.CM_NAME,)}


def _breaks(choice: str | None, readers) -> set[str]:
    return {r for r in readers if any(P.MATRIX[f][r] != P.READS for f in _ALLOWS[choice])}


def _readers(spec: dict) -> tuple[str, ...]:
    return tuple(spec["delta_readers"]) if spec.get("delta_readers") else P.DEFAULT_READERS


def _unchanged(old: dict, new: dict) -> bool:
    """The delta_ settings mean the same: no delta_readers is the default readers, and delta_accepted is a set
    in any order."""
    return (not P.value_problems(old) and old.get("delta_features") == new.get("delta_features")
            and _readers(old) == _readers(new)
            and sorted(old.get("delta_accepted") or []) == sorted(new.get("delta_accepted") or []))


def _must_ask(old: dict, new: dict) -> set[str]:
    """The readers a changed save has to have the name typed for: each reader in new that its choice breaks,
    and each reader dropped in this save that it breaks and that old didn't accept under the same choice.
    A dropped reader counts only when old's own settings are right: a wrong old file's readers aren't
    trusted (they may not even be a list), and its save gate already fails."""
    choice = new.get("delta_features")
    ask = _breaks(choice, _readers(new))
    if not P.value_problems(old):
        accepted = set(old.get("delta_accepted") or []) if old.get("delta_features") == choice else set()
        ask |= _breaks(choice, [r for r in _readers(old) if r not in _readers(new) and r not in accepted])
    return ask


def _spec(choice, readers, accepted) -> dict:
    spec = {"name": "gold.fact_sales", "owner": "data"}
    if choice is not None:
        spec["delta_features"] = choice
    if readers:
        spec["delta_readers"] = list(readers)
    if accepted is not None:
        spec["delta_accepted"] = list(accepted)
    return spec


NAMES = ["gold.fact_sales", "silver.\u00e9t\u00e9", "gold.dbo.x", " gold.padded ", "gold.x\u00a0"]


def _check_confirm(name: str, old: dict, new: dict) -> None:
    unchanged, ask = _unchanged(old, new), _must_ask(old, new)
    for typed in (None, "", "   ", "wrong.name", name.upper() if name.upper() != name else name + "x"):
        try:
            got = P.confirm_choice(name, old, new, typed)
        except P.ConfirmNeeded as exc:  # the only refusal for right values without the name typed
            assert ask and not unchanged, (old, new, typed)
            assert exc.name == name and exc.reasons
            said = " ".join(exc.reasons)
            assert all(P.READERS[r] in said for r in ask), (said, ask)
            continue
        if unchanged:
            assert got is None
        else:
            assert not ask, ("a break went through without the name typed", old, new, typed)
            assert got == []
    for typed in (name, f"  {name.strip()}\t"):
        got = P.confirm_choice(name, old, new, typed)  # never ConfirmNeeded with the name typed
        if unchanged:
            assert got is None
        else:
            assert got is not None  # None only when nothing changed
            assert set(got) >= _breaks(new.get("delta_features"), _readers(new))
            assert set(got) <= set(_readers(new))


# Four readers that between them have every answer: reads everything, can't read either feature, no source
# says for column mapping, and the default pair.
_FEW = ("sql_endpoint", "direct_lake", "python_notebooks", "snowflake")


def _subsets(items):
    return [list(c) for n in range(len(items) + 1) for c in itertools.combinations(items, n)]


def test_confirm_choice_over_every_choice_and_reader_set_of_four_readers():
    olds = []
    for choice, readers in itertools.product(CHOICES, _subsets(_FEW)):
        broken = sorted(_breaks(choice, _readers(_spec(choice, readers, None))))
        for accepted in (None, [], broken, ["python_notebooks"], ["snowflake", "python_notebooks"]):
            olds.append(_spec(choice, readers, accepted))
    news = [_spec(choice, readers, None) for choice, readers in itertools.product(CHOICES, _subsets(_FEW))]
    rng = random.Random(11)
    for old in olds:
        for new in rng.sample(news, 12):
            _check_confirm("gold.fact_sales", old, new)
            # As meta_set_delta_features saves it: old's file with the new choice and readers, its delta_accepted kept.
            kept = {k: v for k, v in old.items() if k not in ("delta_features", "delta_readers")} \
                | {k: v for k, v in new.items() if k in ("delta_features", "delta_readers")}
            _check_confirm("gold.fact_sales", old, kept)
        _check_confirm("gold.fact_sales", old, dict(old))


def test_confirm_choice_sampled_over_every_reader():
    rng = random.Random(23)

    def spec():
        readers = rng.choice([None, rng.sample(READERS, rng.randint(1, len(READERS)))])
        if readers and rng.random() < 0.1:
            readers = readers + [readers[0]]  # a reader named twice
        accepted = rng.choice([None, [], rng.sample(READERS, rng.randint(0, 4))])
        return _spec(rng.choice(CHOICES), readers, accepted)

    for _ in range(2500):
        old = spec()
        new = rng.choice([spec(), dict(old), {**old, "delta_features": rng.choice(CHOICES)}])
        new = {k: v for k, v in new.items() if v is not None}
        _check_confirm(rng.choice(NAMES), old, new)


def test_confirm_choice_with_a_wrong_value_is_a_plain_value_error():
    rng = random.Random(31)
    wrong = [{"delta_features": "fast"}, {"delta_features": 1}, {"delta_readers": []}, {"delta_readers": "sql_endpoint"},
             {"delta_readers": ["nope"]}, {"delta_accepted": ["nope"]}, {"delta_accepted": [1]}]
    for _ in range(300):
        old = _spec(rng.choice(CHOICES), rng.choice([None, rng.sample(READERS, 2)]), None)
        new = old | rng.choice(wrong)
        for typed in (None, "gold.fact_sales"):
            with pytest.raises(ValueError) as refused:
                P.confirm_choice("gold.fact_sales", old, new, typed)
            assert not isinstance(refused.value, P.ConfirmNeeded)
        # A wrong old file (changed outside the app) never makes a right new one fail any other way.
        bad_old = old | rng.choice(wrong)
        new = _spec(rng.choice(CHOICES), rng.choice([None, rng.sample(READERS, 2)]), None)
        _check_confirm("gold.fact_sales", bad_old, new)


def test_a_name_with_spaces_round_it_is_confirmed_by_typing_it():
    # Regression: the typed name was stripped and then compared with the unstripped name, so an entity whose
    # name has a space at either end (entity_name_problem allows it) could never be confirmed.
    name = " gold.padded "
    new = {"delta_features": "deletion_vectors", "delta_readers": ["pipelines"]}
    with pytest.raises(P.ConfirmNeeded):
        P.confirm_choice(name, {}, new, None)
    assert P.confirm_choice(name, {}, new, name) == ["pipelines"]
