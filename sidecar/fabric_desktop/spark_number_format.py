"""Spark's number format patterns ('999', '$99,999.99S', '9.9PR') for to_number and try_to_number,
read the way Apache Spark 3.5 reads them (org.apache.spark.sql.catalyst.util.ToNumberParser).

Spark requires the format to be a constant, so it is read here, in Python, when the query is
rewritten: ``tokens(fmt)`` checks it as Spark does and ``parse_sql`` gives DuckDB SQL that reads
each input as Spark's parser does. Spark walks the input once, token by token, and every token but
a digit group takes at most one character, so one anchored regular expression with a capture per
token matches the same inputs; the digit-group sizes, the dollar sign and the angle brackets are
then checked on the captures. The result is Spark's decimal(precision, scale).
"""

from __future__ import annotations

from dataclasses import dataclass

_WHITESPACE = r"\t\n\x0B\f\r\x1C-\x1F "   # Java's Character.isWhitespace, within US-ASCII


@dataclass(frozen=True)
class _Digits:
    count: int
    exact: bool   # a run starting with 0 before the decimal point: exactly that many digits


@dataclass(frozen=True)
class _Group:
    """A run of digits and thousands separators; ``expected`` is its digit runs, rightmost first."""
    expected: tuple[_Digits, ...]
    separators: tuple[bool, ...]   # the run's tokens in order: True for a separator


def _invalid(fmt: str, why: str) -> ValueError:
    return ValueError(f"The number format {fmt!r} isn't valid: {why}.")


def tokens(fmt: str) -> list[object]:
    """The format's tokens, as Spark's ToNumberParser.formatTokens, after its checkInputDataTypes."""
    raw: list[object] = []
    i, point = 0, False
    while i < len(fmt):
        c = fmt[i]
        if c in "09":
            start = i
            while i < len(fmt) and fmt[i] in "09":
                i += 1
            raw.append(_Digits(i - start, c == "0" and not point))
            continue
        two = fmt[i:i + 2]
        if c in ".D":
            raw.append("point")
            point = True
        elif c in ",G":
            raw.append("sep")
        elif c == "$":
            raw.append("dollar")
        elif c == "S":
            raw.append("sign")
        elif two == "MI":
            raw.append("minus")
            i += 1
        elif two == "PR":
            raw.insert(0, "open")
            raw.append("close")
            i += 1
        else:
            raw.append(("char", c))
        i += 1
    out: list[object] = []
    run: list[object] = []
    for token in raw + [None]:
        if isinstance(token, _Digits) or token == "sep":
            run.append(token)
            continue
        if run:
            out.append(_Group(tuple(t for t in reversed(run) if isinstance(t, _Digits)),
                              tuple(t == "sep" for t in run)))
            run = []
        if token is not None:
            out.append(token)
    _check(fmt, out)
    return out


def _check(fmt: str, toks: list[object]) -> None:
    if not fmt:
        raise _invalid(fmt, "it is empty")
    if not any(isinstance(t, _Group) for t in toks):
        raise _invalid(fmt, "it has no digits (0 or 9)")
    first_digit = next(i for i, t in enumerate(toks) if isinstance(t, _Group))
    dollar = toks.index("dollar") if "dollar" in toks else -1
    point = toks.index("point") if "point" in toks else -1
    if first_digit < dollar or (point != -1 and point < dollar):
        raise _invalid(fmt, "$ must come before the digits and the decimal point")
    for i, t in enumerate(toks):
        if not isinstance(t, _Group):
            continue
        seps = t.separators
        if point == -1 or i < point:
            if any(s and (j == 0 or j == len(seps) - 1 or seps[j - 1] or seps[j + 1]) for j, s in enumerate(seps)):
                raise _invalid(fmt, "each thousands separator must have digits on both sides")
        elif any(seps):
            raise _invalid(fmt, "thousands separators must come before the decimal point")
    for name in ("point", "sign", "minus", "dollar", "close"):
        if toks.count(name) > 1:
            raise _invalid(fmt, f"it has more than one {name}")
    allowed = [{"open"}, {"minus", "sign"}, {"dollar"}, {"digits"}, {"point"}, {"digits"}, {"dollar"},
               {"minus", "sign", "close"}]
    at = 0
    for kinds in allowed:
        if at < len(toks) and ("digits" if isinstance(toks[at], _Group) else toks[at]) in kinds:
            at += 1
    if at < len(toks):
        token = toks[at]
        raise _invalid(fmt, f"{token[1]!r} isn't a format character" if isinstance(token, tuple)
                       else "its parts are out of order")


def precision_scale(toks: list[object]) -> tuple[int, int]:
    def digits(ts: list[object]) -> int:
        return sum(d.count for t in ts if isinstance(t, _Group) for d in t.expected)
    point = toks.index("point") if "point" in toks else len(toks)
    return digits(toks), digits(toks[point:])


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def parse_sql(fmt: str, fail: str) -> tuple[str, int, int]:
    """DuckDB SQL reading fj_arg0 (text) with ``fmt`` as Spark's to_number does, and its decimal
    precision and scale. ``fail`` is the SQL for an input the format doesn't match (an ERROR for
    to_number, NULL for try_to_number); kf_ names are the rewrite's own."""
    toks = tokens(fmt)
    precision, scale = precision_scale(toks)
    if precision > 38:
        raise _invalid(fmt, "it has more than 38 digits")
    pattern, names = "^", list[str]()
    for t in toks:
        if isinstance(t, _Group):
            pattern += f"([0-9,{_WHITESPACE}]*)"
        else:
            pattern += {"point": r"(\.?)", "dollar": r"(\$?)", "sign": "([+-]?)", "minus": "(-?)",
                        "open": "(<?)", "close": "(>?)"}[str(t)]
        names.append(f"c{len(names)}")
    pattern += "$"
    cap = [f"kf_m.c{i}" for i in range(len(toks))]
    checks = [f"REGEXP_MATCHES(kf_s, {_q(pattern)})"]
    point_at = toks.index("point") if "point" in toks else -1
    before: list[tuple[str, int]] = []
    after: list[tuple[str, int]] = []
    for i, t in enumerate(toks):
        if isinstance(t, _Group):
            sizes = (f"LIST_REVERSE(LIST_TRANSFORM(STRING_SPLIT(REGEXP_REPLACE({cap[i]}, '[^0-9,]', '', 'g'), ','), "
                     "kf_x -> LENGTH(kf_x)))")
            rules = [f"LEN({sizes}) <= {len(t.expected)}"]
            for j, d in enumerate(t.expected, start=1):
                rules.append(f"COALESCE({sizes}[{j}], 0) {'=' if d.exact else '<='} {d.count}")
            checks.append("(" + " AND ".join(rules) + ")")
            digits = f"REGEXP_REPLACE({cap[i]}, '[^0-9]', '', 'g')"
            # Digits after a decimal point the input has go after the point; otherwise before it.
            (after if point_at != -1 and i > point_at else before).append((digits, i))
        elif t == "dollar":
            rest = " || ".join(cap[i + 1:]) or "''"
            checks.append(f"({cap[i]} = '$' OR ({rest}) = '')")
    if "close" in toks:
        checks.append(f"(({cap[toks.index('open')]} = '<') = ({cap[toks.index('close')]} = '>'))")
    point_sql = cap[point_at] if point_at != -1 else "''"
    before_sql = " || ".join(d for d, _ in before) or "''"
    after_sql = " || ".join(d for d, _ in after) or "''"
    whole = f"CASE WHEN {point_sql} = '.' THEN {before_sql} ELSE {before_sql} || {after_sql} END"
    frac = f"CASE WHEN {point_sql} = '.' THEN {after_sql} ELSE '' END"
    negative = " + ".join([f"({cap[i]} = '-')::INT" for i, t in enumerate(toks) if t in ("sign", "minus")]
                          + ([f"({cap[toks.index('close')]} = '>')::INT"] if "close" in toks else [])) or "0"
    sign = f"CASE WHEN ({negative}) % 2 = 1 THEN '-' ELSE '' END"
    number = (f"{sign} || ({whole}) || '.' || RPAD({frac}, {scale}, '0')" if scale
              else f"{sign} || ({whole})")
    empty = "" if scale else (f"WHEN ({whole}) = '' THEN ERROR('to_number() found no digits in ''' || kf_s || ''', "
                              "so nothing ran, as in Fabric.') ")
    groups = ", ".join(f"'{n}'" for n in names)
    sql = (f"CASE WHEN fj_arg0 IS NULL THEN NULL ELSE LIST_TRANSFORM([CAST(fj_arg0 AS VARCHAR)], kf_s -> "
           f"CASE WHEN REGEXP_MATCHES(kf_s, '[\\p{{Nd}}\\p{{Z}}\\x{{85}}]') AND REGEXP_MATCHES(kf_s, '[^\\x00-\\x7F]') "
           f"THEN ERROR('to_number() can''t run on this PC the way it runs in Fabric, so nothing ran: the input '''"
           f" || kf_s || ''' has digits or spaces outside US-ASCII. Run the cell in Fabric.') "
           f"WHEN NOT ({checks[0]}) THEN {fail} ELSE LIST_TRANSFORM([REGEXP_EXTRACT(kf_s, {_q(pattern)}, [{groups}])], "
           f"kf_m -> CASE WHEN NOT ({' AND '.join(checks[1:]) or 'TRUE'}) THEN {fail} {empty}"
           f"ELSE CAST({number} AS DECIMAL({precision}, {scale})) END)[1] END)[1] END")
    return sql, precision, scale


# ---- to_char / to_varchar: Spark's ToNumberParser.format, worked out per case
#
# For a given format the text Spark writes depends only on how many digits the number has before
# the decimal point (k), whether it is negative and whether it fits; the digits themselves are just
# copied into place. So Spark's formatting is run here once per case, on placeholders ('b', i) for
# the i-th digit before the point and ('a', j) for the j-th after it, and the SQL picks the case.

_Item = tuple  # ("lit", char) | ("b", index) | ("a", index)


def _char(item: _Item) -> str:
    return item[1] if item[0] == "lit" else "5"   # a placeholder reads as a digit


def _add_checking_spaces(out: list[_Item], char: str) -> None:
    out.append(("lit", char))
    i = len(out) - 1
    while i >= 1 and _char(out[i - 1]) == " " and _char(out[i]) == char:
        out[i], out[i - 1] = ("lit", " "), ("lit", char)
        i -= 1


def _add_space(out: list[_Item]) -> None:
    out.append(("lit", " "))
    i = len(out) - 1
    while i >= 1 and _char(out[i - 1]) in "+-<" and _char(out[i]) == " ":
        out[i], out[i - 1] = out[i - 1], ("lit", " ")
        i -= 1


def _strip_lone_point(out: list[_Item]) -> None:
    for i, item in enumerate(out):
        if _char(item) == ".":
            if i == len(out) - 1 or _char(out[i + 1]) == " ":
                out[i] = ("lit", " ")
            return


def _format_case(toks: list[object], k: int | None, negative: bool) -> list[_Item]:
    """Spark's format() for a number with k digits before the point (None: it doesn't fit)."""
    nb = sum(d.count for t in toks[:toks.index("point") if "point" in toks else len(toks)]
             if isinstance(t, _Group) for d in t.expected)
    na = sum(d.count for t in toks[toks.index("point"):] if isinstance(t, _Group) for d in t.expected) \
        if "point" in toks else 0
    if k is None:
        before: list[_Item] = [("lit", "#")] * nb
        after: list[_Item] = [("lit", "#")] * na
    else:
        before = [("lit", " ")] * (nb - k) + [("b", i) for i in range(k)]
        after = [("a", j) for j in range(na)]
    out: list[_Item] = []
    bi = ai = 0
    point = False
    for t in toks:
        if isinstance(t, _Group):
            run: list[_Digits | None] = []   # the group's tokens left to right: digit runs, None for a separator
            digits = list(reversed(t.expected))
            for is_sep in t.separators:
                run.append(None if is_sep else digits.pop(0))
            for piece in run:
                if piece is None:
                    if out and _char(out[-1]).isdigit():
                        out.append(("lit", ","))
                    else:
                        _add_space(out)
                elif not point:
                    for _ in range(piece.count):
                        item = before[bi]
                        if _char(item) == " " and item[0] == "lit":
                            if piece.exact:
                                out.append(("lit", "0"))
                            else:
                                _add_space(out)
                        else:
                            out.append(item)
                        bi += 1
                else:
                    for _ in range(piece.count):
                        out.append(after[ai])
                        ai += 1
        elif t == "point":
            if out and _char(out[-1]) == " ":
                out[-1] = ("lit", "0")
            out.append(("lit", "."))
            point = True
        elif t == "dollar":
            out.append(("lit", "$"))
        elif t == "sign":
            _strip_lone_point(out)
            _add_checking_spaces(out, "-" if negative else "+")
        elif t == "minus":
            if negative:
                _strip_lone_point(out)
                _add_checking_spaces(out, "-")
            else:
                out.append(("lit", " "))
        elif t == "open":
            if negative:
                out.append(("lit", "<"))
        elif t == "close":
            _strip_lone_point(out)
            if negative:
                _add_checking_spaces(out, ">")
            else:
                out += [("lit", " "), ("lit", " ")]
    _strip_lone_point(out)
    text = "".join(_char(i) for i in out)
    if not out or text in ("+", "-"):
        return [("lit", "0")]
    return out


_INTEGER_TYPES = "'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT'"


def spark_format(fmt: str) -> str:
    """The format as Spark reads it: upper case (ASCII letters only, as Locale.ROOT)."""
    return fmt.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def format_sql(fmt: str) -> str:
    """DuckDB SQL writing fj_arg0 (a number, or text read as one) with ``fmt`` as Spark's to_char does.
    kf_v holds the number's digits before the point (b, no leading zeros), after it (a, padded to the
    format with zeros) and whether it is negative (n)."""
    toks = tokens(fmt)
    nb = sum(d.count for t in toks[:toks.index("point") if "point" in toks else len(toks)]
             if isinstance(t, _Group) for d in t.expected)
    na = precision_scale(toks)[1]

    def sql_of(items: list[_Item]) -> str:
        parts, text = [], ""
        for item in items:
            if item[0] == "lit":
                text += item[1]
                continue
            if text:
                parts.append(_q(text))
                text = ""
            parts.append(f"SUBSTR(kf_v.{item[0]}, {item[1] + 1}, 1)")
        if text:
            parts.append(_q(text))
        return " || ".join(parts) or "''"

    cases = []
    for negative in (True, False):
        for k in range(nb + 1):
            cases.append(f"WHEN kf_v.n = {str(negative).upper()} AND LENGTH(kf_v.b) = {k} "
                         f"THEN {sql_of(_format_case(toks, k, negative))}")
        cases.append(f"WHEN kf_v.n = {str(negative).upper()} AND kf_v.b IS NULL "
                     f"THEN {sql_of(_format_case(toks, None, negative))}")
    plain = (f"CASE WHEN typeof(fj_arg0) LIKE 'DECIMAL%' OR typeof(fj_arg0) IN ({_INTEGER_TYPES}) "
             "THEN CAST(fj_arg0 AS VARCHAR) "
             # Spark casts text to DECIMAL(38,18), a double to DECIMAL(30,15) and a float to DECIMAL(14,7),
             # from the value's shortest text; a wider decimal first, as DuckDB rounds '5e-19' wrongly.
             "WHEN typeof(fj_arg0) = 'VARCHAR' THEN CAST(TRY_CAST(fj_arg0 AS DECIMAL(38, 18)) AS VARCHAR) "
             "WHEN typeof(fj_arg0) = 'DOUBLE' THEN CAST(TRY_CAST(TRY_CAST(CAST(fj_arg0 AS VARCHAR) "
             "AS DECIMAL(38, 23)) AS DECIMAL(30, 15)) AS VARCHAR) "
             "WHEN typeof(fj_arg0) = 'FLOAT' THEN CAST(TRY_CAST(TRY_CAST(CAST(fj_arg0 AS VARCHAR) "
             "AS DECIMAL(38, 30)) AS DECIMAL(14, 7)) AS VARCHAR) "
             "ELSE ERROR('to_char() can''t run on this PC the way it runs in Fabric, so nothing ran: it writes "
             "numbers and text here, not a ' || typeof(fj_arg0) || '. Run the cell in Fabric.') END")
    digits = "LTRIM(SPLIT_PART(kf_p, '.', 1), '-0')"
    frac = "RTRIM(SPLIT_PART(kf_p, '.', 2), '0')"
    fits = f"LENGTH({digits}) <= {nb} AND LENGTH({frac}) <= {na}"
    value = (f"{{'b': CASE WHEN {fits} THEN {digits} ELSE NULL END, 'a': RPAD({frac}, {na}, '0'), "
             f"'n': STARTS_WITH(kf_p, '-')}}")
    return (f"CASE WHEN fj_arg0 IS NULL THEN NULL ELSE LIST_TRANSFORM([{plain}], kf_p -> CASE WHEN kf_p IS NULL "
            f"THEN NULL ELSE LIST_TRANSFORM([{value}], kf_v -> CASE " + " ".join(cases) + " END)[1] END)[1] END")
