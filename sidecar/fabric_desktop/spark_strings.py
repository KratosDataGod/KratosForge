"""Spark's regular expressions and string functions on this PC (WR-107).

Spark reads a regular expression with Java's java.util.regex; DuckDB reads it with RE2. A pattern written
as text is translated here before it runs: Java's named groups, POSIX classes (\\p{Alpha}), \\s (which in
Java also takes the vertical tab), \\h, \\v, \\R, \\uXXXX and octal escapes. What RE2 can't do at all
(lookarounds, backreferences, possessive quantifiers, atomic groups, comments mode, class unions and
intersections) is refused by name, so nothing differs silently. regexp_replace's replacement is Java's
($1, ${name}, \\$) written for RE2 (\\1). What still differs is listed in docs/sql-compatibility.md
("Regular expressions and text on this PC"), and LOC-002 warns wherever a pattern is used.

The string fixes give Spark's results where DuckDB's differ: int results, split_part's empty delimiter
and index 0, left and right with a length of 0 or less, lpad and rpad with an empty pad, locate before
position 1, chr past 255, LIKE's default escape (\\), concat with a NULL and concat_ws with arrays.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast

from sqlglot import exp

from . import spark_sql_names

_REFUSED = ("{fn} can't use {what} in a regular expression on this PC, so nothing ran: Fabric's Java regular "
            "expressions accept it, but RE2, which runs them here, doesn't. {hint}")

# Java's POSIX classes, ASCII only as in Java (no UNICODE_CHARACTER_CLASS flag), and RE2's names for them
_POSIX = {"Lower": "lower", "Upper": "upper", "ASCII": "ascii", "Alpha": "alpha", "Digit": "digit",
          "Alnum": "alnum", "Punct": "punct", "Graph": "graph", "Print": "print", "Blank": "blank",
          "Cntrl": "cntrl", "XDigit": "xdigit", "Space": "space"}
# Java's \h and \v, as characters (inside a class) and as a class
_H = r" \t\x{A0}\x{1680}\x{180E}\x{2000}-\x{200A}\x{202F}\x{205F}\x{3000}"
_V = r"\n\x{0B}\f\r\x{85}\x{2028}\x{2029}"
_QUANTIFIER = re.compile(r"\{\d+(,\d*)?\}")
_FLAGS = re.compile(r"([a-zA-Z]*)(?:-([a-zA-Z]*))?([:)])")
_OCTAL = re.compile(r"[0-3][0-7]{0,2}|[0-7]{1,2}")
_NAME = re.compile(r"([A-Za-z][A-Za-z0-9]*)>")


def _refuse(fn: str, what: str, hint: str = "") -> ValueError:
    return ValueError(_REFUSED.format(fn=fn, what=what, hint=hint + " Or run it in Fabric." if hint
                                      else "Run it in Fabric."))


def _groups(n: int) -> str:
    """'1 group', '2 groups'."""
    return f"{n} group" if n == 1 else f"{n} groups"


def _use_group(n: int) -> str:
    return "Use 0 for the whole match." if n == 0 else f"Use a group from 0 to {min(n, 9)}."


def java_pattern(pattern: str, fn: str) -> tuple[str, int, dict[str, int]]:
    """``pattern`` (Java's regular expression syntax) as RE2 reads it, with its number of capturing groups
    and their names. Raises ValueError naming what RE2 can't read."""
    out: list[str] = []
    groups = 0
    names: dict[str, int] = {}
    in_class = False
    quantified = False   # the last thing written was a quantifier, so a + after it is possessive
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            d = pattern[i + 1]
            i += 2
            quantified = False
            if d == "Q":
                end = pattern.find("\\E", i)
                end = n if end < 0 else end
                out.append("".join(ch if ch.isalnum() or ch == " " or ord(ch) > 127 else "\\" + ch
                                   for ch in pattern[i:end]))
                i = end + 2
            elif d in "pP":
                if i < n and pattern[i] == "{":
                    end = pattern.find("}", i)
                    if end < 0:
                        raise _refuse(fn, f"'\\{d}{{' without its closing brace")
                    name, i = pattern[i + 1:end], end + 1
                else:
                    name, i = pattern[i:i + 1], i + 1
                out.append(_property(d == "P", name, in_class, fn))
            elif d.isdigit() and d != "0":
                raise _refuse(fn, f"a backreference ('\\{d}')", "RE2 can't match text a group matched earlier.")
            elif d == "0":
                digits = _OCTAL.match(pattern, i)
                if not digits:
                    raise _refuse(fn, "'\\0' without an octal number after it")
                out.append(f"\\x{{{int(digits.group(), 8):X}}}")
                i = digits.end()
            elif d == "k":
                raise _refuse(fn, "a backreference ('\\k<name>')", "RE2 can't match text a group matched earlier.")
            elif d == "Z":
                raise _refuse(fn, "'\\Z' (the end, or just before a final line break)", "Use \\z for the very end.")
            elif d in "GXN" or (d == "b" and pattern.startswith("{", i)):
                raise _refuse(fn, f"'\\{d}'")
            elif d == "R":
                if in_class:
                    raise _refuse(fn, "'\\R' inside [ ]")
                out.append(f"(?:\\r\\n|[{_V}])")
            elif d in "hH":
                out.append(_H if in_class and d == "h" else f"[{'^' if d == 'H' else ''}{_H}]")
                if in_class and d == "H":
                    raise _refuse(fn, "'\\H' inside [ ]")
            elif d in "vV":
                if in_class and d == "V":
                    raise _refuse(fn, "'\\V' inside [ ]")
                out.append(_V if in_class else f"[{'^' if d == 'V' else ''}{_V}]")
            elif d in "sS":
                # Java's \s includes the vertical tab (\x0B), which RE2's \s leaves out; POSIX space has it
                name = "space" if d == "s" else "^space"
                out.append(f"[:{name}:]" if in_class else f"[[:{name}:]]")
            elif d == "u":
                code = pattern[i:i + 4]
                if not re.fullmatch(r"[0-9A-Fa-f]{4}", code):
                    raise _refuse(fn, "'\\u' without four hex digits after it")
                i += 4
                unit = int(code, 16)
                low = re.match(r"\\u(D[C-F][0-9A-F]{2})", pattern[i:i + 6], re.I)
                if 0xD800 <= unit <= 0xDBFF and low:
                    # Java reads \uD83D\uDE00 as the one character U+1F600 that its two UTF-16 halves make
                    code = f"{0x10000 + ((unit - 0xD800) << 10) + (int(low.group(1), 16) - 0xDC00):X}"
                    i += 6
                elif 0xD800 <= unit <= 0xDFFF:
                    raise _refuse(fn, f"half of a UTF-16 pair ('\\u{code}') on its own",
                                  "Write the whole character, such as \\x{1F600}.")
                out.append(f"\\x{{{code}}}")
            elif d == "x" and pattern.startswith("{", i):
                end = pattern.find("}", i)
                end = n - 1 if end < 0 else end
                out.append("\\x" + pattern[i:end + 1])   # \x{1F600} as it is, so its braces aren't a quantifier
                i = end + 1
            elif d == "e":
                out.append("\\x{1B}")
            elif d == "c":
                if i >= n:
                    raise _refuse(fn, "'\\c' without a letter after it")
                out.append(f"\\x{{{ord(pattern[i]) ^ 64:X}}}")
                i += 1
            elif not d.isascii():
                out.append(f"\\x{{{ord(d):X}}}")   # Java takes \é as é; RE2 escapes only ASCII
            else:
                out.append("\\" + d)
            continue
        if in_class:
            if c == "[":
                raise _refuse(fn, "a class inside a class, such as [a[b]] or [[:alpha:]]",
                              "Write the characters in one class, or use \\p{Alpha} for a POSIX class.")
            if pattern.startswith("&&", i):
                raise _refuse(fn, "a class intersection ('&&')", "Write the characters the class should take.")
            if c == "]" and not (out and out[-1] in ("[", "[^")):
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            start = "[^" if pattern.startswith("^", i + 1) else "["
            out.append(start)
            i += len(start)
            quantified = False
            continue
        if c == "(":
            quantified = False
            if pattern.startswith("(?", i):
                at = i + 2   # matched in place, not on a slice, so a long pattern stays linear
                if pattern.startswith(("=", "!", "<=", "<!"), at):
                    raise _refuse(fn, "a lookahead or lookbehind ('(?=', '(?!', '(?<=', '(?<!')",
                                  "Match the text itself, or test it in a separate condition.")
                if pattern.startswith(">", at):
                    raise _refuse(fn, "an atomic group ('(?>')")
                if pattern.startswith("<", at):
                    named = _NAME.match(pattern, at + 1)
                    if not named:
                        raise _refuse(fn, "'(?<' without a group name")
                    groups += 1
                    names[named.group(1)] = groups
                    out.append(f"(?P<{named.group(1)}>")
                    i = named.end()
                    continue
                flags = _FLAGS.match(pattern, at)
                if flags and flags.group(0) != ":":
                    on, off, close = flags.group(1), flags.group(2) or "", flags.group(3)
                    bad = sorted(set(on + off) - set("imsu"))
                    if bad:
                        raise _refuse(fn, f"the flag '{bad[0]}' in '(?{flags.group(0)}'",
                                      "Only the i, m, s and u flags have a reading here.")
                    on, off = on.replace("u", ""), off.replace("u", "")
                    if on or off:
                        out.append(f"(?{on}{'-' + off if off else ''}{close}")
                    elif close == ":":
                        out.append("(?:")
                    i = flags.end()
                    continue
            else:
                groups += 1
            out.append(c)
            i += 1
            continue
        if c == "{":
            q = _QUANTIFIER.match(pattern, i)
            if not q:
                # Java stops on a { that isn't a repeat such as {2} or {2,5} ("Illegal repetition"); RE2 took it
                # as the character, so rlike('a{', 'a{') was true here where Spark stops
                raise ValueError(f"{fn}'s regular expression has a {{ that isn't a repeat such as {{2}} or {{2,5}}, so "
                                 "nothing ran. Spark stops on it too (\"Illegal repetition\"). Write \\{ for the "
                                 "character {.")
            out.append(q.group())
            i = q.end()
            quantified = True
            continue
        if c == "+" and quantified:
            raise _refuse(fn, "a possessive quantifier ('*+', '++', '?+')", "Drop the second + if the match doesn't depend on it.")
        if c == "?" and quantified:
            out.append(c)   # lazy, which RE2 reads too
            i += 1
            quantified = False
            continue
        quantified = c in "*+?"
        out.append(c)
        i += 1
    return "".join(out), groups, names


def _property(negated: bool, name: str, in_class: bool, fn: str) -> str:
    """Java's \\p{name} (or \\P{name}) as RE2 reads it."""
    if name in _POSIX:
        posix = ("^" if negated else "") + _POSIX[name]
        return f"[:{posix}:]" if in_class else f"[[:{posix}:]]"
    if name.startswith("Is") and len(name) > 2:
        name = name[2:]
        if name in ("Alphabetic", "Letter", "Lowercase", "Uppercase", "Digit", "White_Space", "WhiteSpace",
                    "Punctuation", "Control", "Hex_Digit", "HexDigit", "Alnum", "Ideographic", "Titlecase"):
            raise _refuse(fn, f"the binary property '\\p{{Is{name}}}'", "Use a category such as \\p{L} or \\p{Lu}.")
    elif name.startswith(("In", "java", "block=", "blk=", "script=", "sc=", "general_category=", "gc=")):
        raise _refuse(fn, f"'\\p{{{name}}}'", "Use a category such as \\p{L}, or a script such as \\p{IsLatin}.")
    return f"\\{'P' if negated else 'p'}{{{name}}}"


def java_replacement(text: str, fn: str, groups: int, names: dict[str, int]) -> str:
    """regexp_replace's replacement, Java's ($1, ${name}, \\ escapes the next character) as RE2 reads it
    (\\1, \\\\ for a backslash). Raises ValueError where Java stops, and for a group past 9."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 >= n:
                raise ValueError(f"{fn}'s replacement ends in a \\, so nothing ran: Spark stops on it too, as Java "
                                 "does (\"character to be escaped is missing\"). Write \\\\ for a backslash.")
            out.append("\\\\" if text[i + 1] == "\\" else text[i + 1])
            i += 2
            continue
        if c != "$":
            out.append(c)
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "{":
            end = text.find("}", i)
            name = text[i + 2:end] if end > 0 else ""
            if name not in names:
                raise ValueError(f"{fn}'s replacement refers to '${{{name}}}', a group the pattern doesn't name, so "
                                 "nothing ran: Spark stops on it too. Write \\$ for a dollar sign.")
            group, i = names[name], end + 1
        elif i + 1 < n and text[i + 1].isdigit():
            group = int(text[i + 1])
            if group > groups:
                ask = (f"Use a group from 0 to {groups}, or write \\$ for a dollar sign." if groups
                       else "Use 0 for the whole match, or write \\$ for a dollar sign.")
                raise ValueError(f"{fn}'s replacement refers to group {group}, and the pattern has {_groups(groups)}, "
                                 f"so nothing ran: Spark stops on it too (\"No group {group}\"). {ask}")
            i += 2
            while i < n and text[i].isdigit() and group * 10 + int(text[i]) <= groups:
                group, i = group * 10 + int(text[i]), i + 1
        else:
            raise ValueError(f"{fn}'s replacement has a $ that refers to no group, so nothing ran: Spark stops on "
                             "it too (\"Illegal group reference\"). Write \\$ for a dollar sign.")
        if group > 9:
            raise ValueError(f"{fn}'s replacement refers to group {group}. On this PC a replacement can use groups "
                             "0 to 9, so nothing ran. Run it in Fabric.")
        out.append(f"\\{group}")
    return "".join(out)


# ---- empty matches. Java's Matcher.find takes an empty match where the match before it ended (Python's re does
# too); RE2's GlobalReplace skips it, so regexp_replace('aaa', 'a*', 'X') was 'X' here and 'XX' in Spark. And
# DuckDB 1.x's REGEXP_EXTRACT_ALL takes an empty match found after where it started looking twice, so
# regexp_count('ab', '\\b') was 3 here and 2 in Spark (DuckDB 2.0 gives 2).

_re2: Any = None
_GROUPING = re.compile(r"\(\?P<\w+>|\(\?[a-zA-Z-]*[:)]|[()|]|[?*+]|\{\d+(,\d*)?\}")


def _empty_matches(pattern: str) -> tuple[bool, bool, bool]:
    """For a pattern in RE2's syntax: whether it can match empty text somewhere, whether it only ever matches
    empty text (such as ^, $, \\b or (^|$)), and whether it has an anchor or boundary (^ $ \\b \\B \\A \\z).
    Each anchor is taken as matching anywhere, so 'can match empty text' errs towards yes."""
    kept: list[str] = []
    anchors, in_class, i, n, start = False, False, 0, len(pattern), 0
    unclosed = False  # a [: with no :] after it: later ones have none either, so no rescan
    while i < n:
        c = pattern[i]
        if c == "\\":
            if not in_class and pattern[i + 1:i + 2] in ("b", "B", "A", "z"):
                anchors = True
            else:
                kept.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "[" and pattern.startswith("[:", i) and not unclosed:
                end = pattern.find(":]", i + 2)
                if end > 0:
                    kept.append(pattern[i:end + 2])
                    i = end + 2
                    continue
                unclosed = True
            in_class = not (c == "]" and i > start)
        elif c == "[":
            in_class = True
            start = i + (2 if pattern.startswith("[^", i) else 1)   # a ] right here is a character of the class
            kept.append(pattern[i:start])
            i = start
            continue
        elif c in "^$":
            anchors = True
            i += 1
            continue
        kept.append(c)
        i += 1
    stripped = "".join(kept)
    global _re2
    import duckdb
    if _re2 is None:
        _re2 = duckdb.connect()
    try:
        empty = bool(_re2.cursor().execute("SELECT regexp_full_match('', ?)", [stripped]).fetchone()[0])
    except duckdb.Error:   # silent-ok: RE2 can't read it, and DuckDB stops on the pattern itself when it runs
        return False, False, anchors
    return empty, empty and _GROUPING.sub("", stripped) == "", anchors


def _at_start_only(pattern: str) -> bool:
    """Whether ``pattern`` can match only at the start of the text: it starts with ^, has no | outside a group or
    class, and no m flag (which lets ^ match after each line break). Then there is one match at most."""
    if not pattern.startswith("^") or "(?" in pattern and re.search(r"\(\?[a-zA-Z]*m", pattern):
        return False
    depth, in_class, i = 0, False, 1
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            i += 2 if pattern.startswith("[^", i) else 1
            i += 1 if pattern.startswith("]", i) else 0   # a ] first in a class is a character of it
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            return False
        i += 1
    return True


def _refuse_empty_matches(fn: str, pattern: str, replacement: str | None = None) -> None:
    """Refuse by name where Java and this PC take empty matches differently (see above)."""
    from .duckdb_rows import duckdb_major
    can, only, anchors = _empty_matches(pattern)
    if not can or _at_start_only(pattern):
        return
    if fn == "regexp_replace":
        if only or replacement == "":
            return   # an empty match replaced by nothing changes nothing
        raise ValueError(f"regexp_replace can't run on this PC with a pattern that can match empty text right after "
                         f"another match ('{pattern}'), so nothing ran. Java replaces that empty match too, and RE2, "
                         "which runs it here, skips it. Use a pattern that always matches a character (+ in place of "
                         "*), or run it in Fabric.")
    if anchors and duckdb_major() < 2:
        raise ValueError(f"{fn} can't run on this PC with a pattern that can match empty text at an anchor or word "
                         f"boundary ('{pattern}'), so nothing ran. This PC's DuckDB counts some of those empty "
                         "matches twice. Use a pattern that always matches a character, or run it in Fabric.")


# ---- the rewrite

_REGEX = {exp.RegexpLike: "rlike", exp.RegexpExtract: "regexp_extract", exp.RegexpExtractAll: "regexp_extract_all",
          exp.RegexpReplace: "regexp_replace", exp.RegexpSplit: "split", exp.RegexpCount: "regexp_count",
          exp.RegexpInstr: "regexp_instr", exp.RegexpSubstr: "regexp_substr"}
_INTS = (exp.Length, exp.BitLength, exp.Levenshtein, exp.StrPosition, exp.RegexpCount, exp.RegexpInstr)


def _int(node: exp.Expression) -> int | None:
    """A whole-number literal's value (also -3), or None."""
    if isinstance(node, exp.Neg):
        inner = _int(node.this)
        return None if inner is None else -inner
    if isinstance(node, exp.Literal) and not node.is_string and re.fullmatch(r"\d+", node.name):
        return int(node.name)
    return None


def _text(node: exp.Expression | None) -> str | None:
    return node.name if isinstance(node, exp.Literal) and node.is_string else None


def _swap(node: exp.Expression, new: exp.Expression) -> exp.Expression:
    new.meta.update(spark_sql_names.kept(node))
    node.replace(new)
    return new


def _with(template: str, args: list[exp.Expression]) -> exp.Expression:
    from .spark_builtins_sql import _with_args
    return _with_args(template, [a.copy() for a in args])


def _around(node: exp.Expression, template: str, args: list[exp.Expression]) -> exp.Expression:
    """``template`` with ``node`` itself as fj_arg0 (not a copy, so the rewrites that follow still see it)."""
    new = _with(template, [exp.column("kf_hold"), *args])
    _swap(node, new)
    holder = next(c for c in new.find_all(exp.Column) if c.name == "kf_hold")
    holder.replace(node)
    return new


def _regex(node: exp.Expression, fn: str) -> exp.Expression | None:
    """One regular expression call: its pattern translated, its group and replacement checked."""
    pattern = node.args.get("expression")
    text = _text(pattern)
    groups: int | None = None
    names: dict[str, int] = {}
    if text is not None and isinstance(pattern, exp.Literal):
        translated, groups, names = java_pattern(text, fn)
        if translated != text:
            pattern.replace(exp.Literal.string(translated))
    written_pattern = _text(node.args.get("expression"))
    if written_pattern is not None and isinstance(node, (exp.RegexpReplace, exp.RegexpExtractAll, exp.RegexpCount)):
        _refuse_empty_matches(fn, written_pattern, _text(node.args.get("replacement")))
    if isinstance(node, exp.RegexpInstr):
        return _instr(node, fn, groups)
    if isinstance(node, (exp.RegexpExtract, exp.RegexpExtractAll)):
        group = node.args.get("group")
        if isinstance(group, exp.Null):
            kind = "VARCHAR[]" if isinstance(node, exp.RegexpExtractAll) else "VARCHAR"
            return _swap(node, exp.cast(exp.Null(), exp.DataType.build(kind)))
        index = 1 if group is None else _int(group)
        if index is not None and index < 0:
            raise ValueError(f"{fn}'s group index is {index}, so nothing ran: Spark stops on it too. Use 0 for the "
                             "whole match, or a group's number.")
        if index is not None and groups is not None and index > groups:
            raise ValueError(f"{fn} asks for group {index}, and the pattern has {_groups(groups)}, so nothing ran: "
                             f"Spark stops on it too where a row matches. {_use_group(groups)}")
        if index is not None and index > 9:
            raise ValueError(f"{fn} asks for group {index}. On this PC a group index can be 0 to 9, so nothing ran. "
                             "Run it in Fabric.")
        if isinstance(node, exp.RegexpExtractAll) and index != 0:
            # a group that took no part in a match is '' in Spark's array (as in regexp_extract), not NULL
            return _around(node, "LIST_TRANSFORM(fj_arg0, kf_g -> COALESCE(kf_g, ''))", [])
    if isinstance(node, exp.RegexpReplace):
        position = node.args.get("position")
        at = _int(position) if position is not None else None
        if at is not None and at < 1:
            raise ValueError(f"{fn}'s position is {at}, so nothing ran: Spark stops on it too. The first "
                             "character is position 1.")
        replacement = node.args.get("replacement")
        written = _text(replacement)
        if written is not None and groups is not None and replacement is not None:
            replacement.replace(exp.Literal.string(java_replacement(written, fn, groups, names)))
        elif replacement is not None and not isinstance(replacement, exp.Null):
            message = (f"{fn}'s replacement comes from the data and has a $ or a \\. Java reads those as a group or "
                       "an escape, but on this PC only a replacement written in the query is read that way, so "
                       "nothing ran. Write the replacement in the query, or run it in Fabric.").replace("'", "''")
            replacement.replace(_with(f"CASE WHEN STRPOS(fj_arg0, '$') > 0 OR STRPOS(fj_arg0, '\\') > 0 "
                                      f"THEN CAST(ERROR('{message}') AS VARCHAR) ELSE fj_arg0 END", [replacement]))
    if isinstance(node, exp.RegexpCount):
        # Java counts the empty match at each position, RE2 none for an empty pattern
        return _swap(node, _with("CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL WHEN fj_arg1 = '' "
                                 "THEN LENGTH(fj_arg0) + 1 ELSE LENGTH(REGEXP_EXTRACT_ALL(fj_arg0, fj_arg1)) END",
                                 [node.this, node.expression]))
    return None


def _instr(node: exp.Expression, fn: str, groups: int | None) -> exp.Expression:
    """regexp_instr(str, regexp[, idx]) as Spark's RegExpInstr: where the first match starts, from 1, or 0. idx is a
    group number that Spark only checks (sqlglot read it as a start position, so regexp_instr('bcx', '(b)(c)', 2)
    was 0 here and 1 in Spark), and the start counts UTF-16 units, as a Java String does: a character past
    U+FFFF counts 2. An empty match counts (regexp_instr('xab', 'a*') is 1); here it was skipped. The text
    before the match is read with RE2 as ^(.*?)(?:regexp), its shortest lead-in, which is where an
    unanchored search finds the match."""
    index_arg = node.args.get("position")
    index = _int(index_arg) if index_arg is not None else 0
    if index is not None and (index < 0 or (groups is not None and index > groups)):
        raise ValueError(f"{fn} asks for group {index}, and the pattern has {_groups(groups or 0)}, so nothing ran. "
                         f"Spark stops on it too where a row matches. {_use_group(groups or 0)}")
    pattern = node.expression
    written = _text(pattern)
    lead = (exp.Literal.string(f"^((?s:.*?))(?:{written})").sql("duckdb") if written is not None
            else "'^((?s:.*?))(?:' || kf_ri.p || ')'")
    match = exp.Literal.string(written).sql("duckdb") if written is not None else "kf_ri.p"
    checked = index_arg is not None and index is None   # an idx from the data: NULL gives NULL, as in Spark
    template = (f"CAST((LIST_TRANSFORM([{{'s': kf_put_s, 'p': kf_put_p{', i: kf_put_i' if checked else ''}}}], "
                f"kf_ri -> CASE WHEN kf_ri.s IS NULL OR kf_ri.p IS NULL{' OR kf_ri.i IS NULL' if checked else ''} "
                f"THEN NULL WHEN NOT REGEXP_MATCHES(kf_ri.s, {match}) THEN 0 ELSE (LIST_TRANSFORM([REGEXP_EXTRACT("
                f"kf_ri.s, {lead}, 1)], kf_rl -> LENGTH(kf_rl) + LENGTH(REGEXP_REPLACE(kf_rl, "
                "'[^\\x{10000}-\\x{10FFFF}]', '', 'g')) + 1))[1] END))[1] AS INT)")
    new = _swap(node, _with(template.replace("i: kf_put_i", "'i': kf_put_i"), []))
    for counted in list(new.find_all(exp.Length)):   # already whole numbers, so not each made an int on its own
        counted.replace(exp.Anonymous(this="LENGTH", expressions=[counted.this]))
    # the arguments themselves (not copies) go in, so a regexp_instr inside them is still rewritten after this one
    text = node.this if _text(node.this) is not None else exp.cast(node.this, "VARCHAR", copy=False)
    for name, arg in (("kf_put_s", text), ("kf_put_p", pattern), ("kf_put_i", index_arg)):
        slot = next((c for c in new.find_all(exp.Column) if c.name == name and not c.table), None)
        if slot is not None and arg is not None:
            slot.replace(arg)
    return new


def _split_part(node: exp.Expression) -> exp.Expression:
    index = node.args["part_index"]
    if _int(index) == 0:
        raise ValueError("split_part's index can't be 0, so nothing ran: Spark stops on it too. The first part "
                         "is 1, and the last is -1.")
    stop = ("split_part''s index can''t be 0, so nothing ran: Spark stops on it too. The first part is 1, and "
            "the last is -1.")
    return _swap(node, _with(
        "CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL OR fj_arg2 IS NULL THEN NULL "
        f"WHEN fj_arg2 = 0 THEN CAST(ERROR('{stop}') AS VARCHAR) "
        "WHEN fj_arg1 = '' THEN CASE WHEN fj_arg2 IN (1, -1) THEN fj_arg0 ELSE '' END "
        "ELSE SPLIT_PART(fj_arg0, fj_arg1, fj_arg2) END", [node.this, node.args["delimiter"], index]))


def _left_right(node: exp.Expression) -> exp.Expression:
    """left and right give '' for a length of 0 or less (DuckDB's count from the other end)."""
    return _around(node, "CASE WHEN fj_arg1 <= 0 THEN CASE WHEN fj_arg2 IS NULL THEN NULL ELSE '' END "
                         "ELSE fj_arg0 END", [node.expression, node.this])


def _pad(node: exp.Expression) -> exp.Expression | None:
    """lpad and rpad with an empty pad: the text cut to the length, as Spark gives (DuckDB stops)."""
    fill = node.args.get("fill_pattern")
    if _text(fill) != "":
        return None
    return _swap(node, _with("CASE WHEN fj_arg0 IS NULL OR fj_arg1 IS NULL THEN NULL "
                             "ELSE LEFT(CAST(fj_arg0 AS VARCHAR), GREATEST(fj_arg1, 0)) END",
                             [node.this, node.expression]))


def _locate(node: exp.Expression) -> exp.Expression | None:
    """locate(substr, str, pos) as Spark's StringLocate: 0 for a NULL position; otherwise NULL when substr or str is
    NULL (checked before the position, so locate('b', NULL, -2) is NULL, not 0); then 0 for a position before 1."""
    position = node.args.get("position")
    if position is None:
        return None
    template = ("CASE WHEN fj_arg1 IS NULL THEN 0 WHEN fj_arg2 IS NULL OR fj_arg3 IS NULL THEN NULL "
                "WHEN fj_arg1 < 1 THEN 0 ELSE fj_arg0 END")
    parts = {"p": ("position", position), "s": ("substr", node.args["substr"]), "t": ("this", node.this)}
    bound = {k: v for k, v in parts.items() if not isinstance(v[1], (exp.Column, exp.Literal, exp.Null))}
    if not bound:
        return _around(node, template, [position, node.args["substr"], node.this])
    # An argument that isn't a column or literal is read once, through a lambda over a one-element list: the
    # NULL checks here and DuckDB's own STRPOS(SUBSTRING(str, pos), substr) twice would otherwise each repeat
    # it, so nested locate(b, locate(...), 2) grew 3x per level. The arguments themselves (not copies) move
    # into the list, so the rewrites that follow still see them.
    names = {"p": "fj_arg1", "s": "fj_arg2", "t": "fj_arg3"}
    for key in bound:
        template = re.sub(rf"\b{names[key]}\b", f"kf_loc.{key}", template)
    fields = ", ".join(f"'{key}': kf_put_{key}" for key in bound)
    new = _with(f"(LIST_TRANSFORM([{{{fields}}}], kf_loc -> {template}))[1]",
                [exp.column("kf_hold"), *(v for _, v in parts.values())])
    _swap(node, new)
    for key, (arg, value) in bound.items():
        slot = next(c for c in new.find_all(exp.Column) if c.name == f"kf_put_{key}" and not c.table)
        node.set(arg, exp.column(key, table="kf_loc"))
        slot.replace(value)
    holder = next(c for c in new.find_all(exp.Column) if c.name == "kf_hold")
    holder.replace(node)
    return new


def _chr(node: exp.Expression) -> exp.Expression | None:
    """chr(n): '' below 0, and the character n % 256 from 256 on, as Spark gives."""
    if len(node.expressions) != 1:
        return None
    return _swap(node, _with("CASE WHEN fj_arg0 IS NULL THEN NULL WHEN CAST(fj_arg0 AS BIGINT) < 0 THEN '' "
                             "ELSE CHR(CAST(CAST(fj_arg0 AS BIGINT) % 256 AS INTEGER)) END", list(node.expressions)))


_ARRAYS = tuple(getattr(exp, name) for name in (
    "Array", "ArrayAgg", "RegexpSplit", "Split", "ArrayDistinct", "ArraySort", "ArrayConcat", "ArrayFilter",
    "ArrayUniqueAgg", "ArrayRemove", "Flatten") if hasattr(exp, name))


def _concat_ws(node: exp.Expression) -> exp.Expression | None:
    """concat_ws(sep, ...) with an array: its elements joined, NULLs left out, as Spark gives."""
    args = list(node.expressions)
    if len(args) < 2 or not any(isinstance(a, _ARRAYS) for a in args[1:]):
        return None
    parts = [f"CAST(fj_arg{k} AS VARCHAR[])" if isinstance(a, _ARRAYS) else f"[CAST(fj_arg{k} AS VARCHAR)]"
             for k, a in enumerate(args) if k]
    joined = parts[0] if len(parts) == 1 else f"LIST_CONCAT({', '.join(parts)})"
    return _swap(node, _with(f"CASE WHEN fj_arg0 IS NULL THEN NULL ELSE ARRAY_TO_STRING({joined}, fj_arg0) END",
                             args))


def _wrap(node: exp.Expression, kind: str) -> None:
    """``node`` cast to ``kind`` in place, keeping the name Spark gives it."""
    new = exp.Cast(to=exp.DataType.build(kind))
    new.meta.update(spark_sql_names.kept(node))
    node.replace(new)
    new.set("this", node)


def spark_text_results(statement: exp.Expression) -> exp.Expression:
    """Spark's regular expressions and string results on DuckDB (see the module's docstring)."""
    for regex in list(statement.find_all(*_REGEX)):
        _regex(cast(exp.Expression, regex), _REGEX[type(regex)])
    for found in list(statement.find_all(exp.SplitPart, exp.Left, exp.Right, exp.Pad, exp.Chr, exp.ConcatWs)):
        call = cast(exp.Expression, found)
        if isinstance(call, exp.SplitPart):
            _split_part(call)
        elif isinstance(call, (exp.Left, exp.Right)):
            _left_right(call)
        elif isinstance(call, exp.Pad):
            _pad(call)
        elif isinstance(call, exp.Chr):
            _chr(call)
        else:
            _concat_ws(call)
    for match in list(statement.find_all(exp.Like, exp.ILike)):
        like = cast(exp.Expression, match)
        pattern = like.expression
        if isinstance(like.parent, exp.Escape) or (_text(pattern) is not None and "\\" not in pattern.name):
            continue
        _swap(like, exp.Escape(this=like.copy(), expression=exp.Literal.string("\\")))
    for joined in list(statement.find_all(exp.Concat, exp.DPipe)):
        if any(isinstance(a, _ARRAYS) for a in joined.iter_expressions()):
            continue
        nulls = [c for c in joined.iter_expressions()
                 if isinstance(c, exp.Null) or (isinstance(c, (exp.Cast, exp.TryCast)) and isinstance(c.this, exp.Null))]
        for child in nulls:
            if isinstance(child, exp.Null):
                child.replace(exp.cast(exp.Null(), exp.DataType.build("VARCHAR")))
        if nulls and joined is not statement and not isinstance(joined.parent, (exp.Concat, exp.DPipe)):
            # DuckDB types 'a' || CAST(NULL AS VARCHAR) as an INTEGER once it folds it; Spark's is a string
            _wrap(joined, "VARCHAR")
    ints = [cast(exp.Expression, n) for n in statement.find_all(*_INTS, exp.Anonymous)
            if not isinstance(n, exp.Anonymous) or n.name.upper() == "OCTET_LENGTH"]
    for item in ints:
        node: exp.Expression = _locate(item) or item if isinstance(item, exp.StrPosition) else item
        if node is statement or (isinstance(node.parent, exp.Cast) and node.parent.to.is_type(exp.DataType.Type.INT)):
            continue
        _wrap(node, "INT")
    return statement


# ---- the DataFrame API: a pattern given as a Python string (F.regexp_replace(c, pattern, replacement),
# F.regexp_extract, F.split, Column.rlike) is translated the same way before DuckDB's function sees it

def java_regex_function(fn_name: str, fn: Callable[..., Any], at: int, replacement_at: int | None = None,
                        group_at: int | None = None, own_replacement: bool = False) -> Callable[..., Any]:
    """``fn`` with its pattern argument at ``at`` translated from Java's syntax, when it is a string.
    ``own_replacement``: ``fn`` translates Java's replacement itself (DuckDB 2.0's regexp_replace does), so
    the replacement is only checked here, not rewritten."""
    import functools

    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        if len(args) > at and isinstance(args[at], str):
            pattern, groups, names = java_pattern(args[at], fn_name)
            new = list(args)
            new[at] = pattern
            if fn_name == "regexp_replace":
                given = args[replacement_at] if replacement_at is not None and len(args) > replacement_at else None
                _refuse_empty_matches(fn_name, pattern, given if isinstance(given, str) else None)
            if replacement_at is not None and len(args) > replacement_at and isinstance(args[replacement_at], str):
                rewritten = java_replacement(args[replacement_at], fn_name, groups, names)
                if not own_replacement:
                    new[replacement_at] = rewritten
            if group_at is not None and len(args) > group_at and isinstance(args[group_at], int):
                index = args[group_at]
                if index < 0 or index > groups or index > 9:
                    raise ValueError(f"{fn_name} asks for group {index}, and the pattern has {_groups(groups)}, so "
                                     f"nothing ran: Spark stops on it too where a row matches. {_use_group(groups)}")
            args = tuple(new)
        return fn(*args, **kwargs)
    return call
