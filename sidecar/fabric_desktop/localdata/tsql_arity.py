"""WR-278: how many arguments each T-SQL built-in function takes, checked on the script's tokens before sqlglot reads
it. sqlglot reads a call with the wrong number of arguments in many ways: ``DAY()`` stopped the translator with a
Python error, ``CEILING(1, 2, 3)`` and ``GROUPING_ID()`` reached DuckDB as SQL it couldn't read, and ``LEN('a', 'b')``,
``ROUND(1)`` and ``ISNULL(1)`` gave an answer where SQL Server stops. Here such a call is refused in SQL Server's own
words: "The ceiling function requires 1 argument(s)." (Msg 174) or "The round function requires 2 to 3 arguments."
(Msg 189). The counts are each function's syntax on Microsoft Learn (learn.microsoft.com/sql/t-sql/functions/).

A window function written without OVER (``RANK()``) is refused as SQL Server refuses it, "The function 'RANK' must
have an OVER clause.", where DuckDB said it had no scalar function of that name.
"""

from __future__ import annotations

from sqlglot.tokens import Token, TokenType

_MOST = 254  # CONCAT, CONCAT_WS, GREATEST and LEAST take at most 254 arguments

# name: (fewest, most); most None for no limit
ARITY: dict[str, tuple[int, int | None]] = {
    **{name: (0, 0) for name in (
        "GETDATE", "GETUTCDATE", "SYSDATETIME", "SYSUTCDATETIME", "SYSDATETIMEOFFSET", "NEWID", "NEWSEQUENTIALID",
        "PI", "ROW_NUMBER", "RANK", "DENSE_RANK", "CUME_DIST", "PERCENT_RANK", "HOST_NAME", "APP_NAME")},
    **{name: (1, 1) for name in (
        "ABS", "ACOS", "ASIN", "ATAN", "COS", "COT", "DEGREES", "EXP", "FLOOR", "CEILING", "RADIANS", "SIGN", "SIN",
        "SQRT", "SQUARE", "TAN", "LOG10", "ASCII", "CHAR", "NCHAR", "UNICODE", "LEN", "LOWER", "UPPER", "REVERSE",
        "SOUNDEX", "SPACE", "DATALENGTH", "DAY", "MONTH", "YEAR", "ISDATE", "ISNUMERIC", "NTILE", "FIRST_VALUE",
        "LAST_VALUE", "STDEV", "STDEVP", "VAR", "VARP", "SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_BIG", "GROUPING",
        "CHECKSUM_AGG", "APPROX_COUNT_DISTINCT", "BIT_COUNT", "PERCENTILE_CONT", "PERCENTILE_DISC", "TRIM",
        "COMPRESS", "DECOMPRESS")},
    **{name: (0, 1) for name in ("RAND", "DB_NAME", "SCHEMA_NAME")},
    **{name: (1, 2) for name in ("LTRIM", "RTRIM", "LOG", "EOMONTH", "JSON_QUERY", "QUOTENAME", "ISJSON", "UNISTR")},
    **{name: (1, 3) for name in ("STR", "LAG", "LEAD", "OBJECT_ID")},
    **{name: (2, 2) for name in (
        "ATN2", "POWER", "DIFFERENCE", "LEFT", "RIGHT", "REPLICATE", "NULLIF", "ISNULL", "STRING_ESCAPE",
        "LEFT_SHIFT", "RIGHT_SHIFT", "GET_BIT", "DATEPART", "DATENAME", "DATETRUNC", "JSON_VALUE", "STRING_AGG",
        "PATINDEX", "JARO_WINKLER_SIMILARITY", "JARO_WINKLER_DISTANCE", "SWITCHOFFSET", "TODATETIMEOFFSET",
        "HASHBYTES")},
    # SUBSTRING's length is optional from SQL Server 2025 on (functions/substring-transact-sql)
    **{name: (2, 3) for name in (
        "ROUND", "CHARINDEX", "STRING_SPLIT", "SET_BIT", "EDIT_DISTANCE", "SUBSTRING", "CONVERT", "TRY_CONVERT",
        "FORMAT", "REGEXP_LIKE", "GENERATE_SERIES")},
    **{name: (3, 3) for name in (
        "REPLACE", "TRANSLATE", "DATEADD", "DATEDIFF", "DATEDIFF_BIG", "DATEFROMPARTS", "IIF", "JSON_MODIFY")},
    "DATE_BUCKET": (3, 4), "STUFF": (4, 4), "TIMEFROMPARTS": (5, 5), "SMALLDATETIMEFROMPARTS": (5, 5),
    "DATETIMEFROMPARTS": (7, 7), "DATETIME2FROMPARTS": (8, 8), "DATETIMEOFFSETFROMPARTS": (10, 10),
    "REGEXP_COUNT": (2, 4), "REGEXP_REPLACE": (2, 6), "REGEXP_SUBSTR": (2, 6), "REGEXP_INSTR": (2, 7),
    "CONCAT": (2, _MOST), "CONCAT_WS": (3, _MOST), "GREATEST": (1, _MOST), "LEAST": (1, _MOST),
    "CHOOSE": (2, None), "COALESCE": (2, None), "GROUPING_ID": (1, None), "CHECKSUM": (1, None),
    "BINARY_CHECKSUM": (1, None),
}
# Window functions: queries/select-over-clause-transact-sql
WINDOWED = {"ROW_NUMBER", "RANK", "DENSE_RANK", "NTILE", "CUME_DIST", "PERCENT_RANK", "LAG", "LEAD", "FIRST_VALUE",
            "LAST_VALUE"}
_CASTS = {"CAST", "TRY_CAST"}
_WITHIN_GROUP = {"PERCENTILE_CONT", "PERCENTILE_DISC"}  # functions/percentile-cont-transact-sql
_TABLE_FUNCTIONS = {"STRING_SPLIT", "GENERATE_SERIES"}
# A name after these is a table's, a routine's or a variable's, followed by its column list or parameters
_NAMING = {TokenType.DOT, TokenType.PARAMETER, TokenType.INTO, TokenType.TABLE, TokenType.REFERENCES,
           TokenType.VIEW, TokenType.FUNCTION, TokenType.PROCEDURE, TokenType.UPDATE}
_FROM = {TokenType.FROM, TokenType.JOIN, TokenType.APPLY}
_NOT_YET = "This T-SQL can't be run on the local engine: "


def check(tokens: list[Token]) -> None:
    """Raise ValueError for a built-in function called with a number of arguments T-SQL doesn't take."""
    for at, token in enumerate(tokens[:-1]):
        name = token.text.upper()
        if tokens[at + 1].token_type != TokenType.L_PAREN or token.token_type in (
                TokenType.IDENTIFIER, TokenType.STRING, TokenType.NATIONAL_STRING):
            continue
        if name == "CURRENT_DATE":
            raise ValueError(_NOT_YET + "CURRENT_DATE takes no brackets in T-SQL. Write CURRENT_DATE on its own. "
                             "Nothing ran.")
        if name not in ARITY and name not in _CASTS:
            continue
        before = tokens[at - 1].token_type if at else None
        if before in _NAMING or (before in _FROM and name not in _TABLE_FUNCTIONS) or (
                before == TokenType.ON and at >= 3 and tokens[at - 3].text.upper() in ("INDEX", "STATISTICS")):
            continue
        count, end = _arguments(tokens, at + 1)
        if end is None:
            return  # unbalanced: the parse reports it
        after = tokens[end + 1:end + 3]
        if after and after[0].token_type == TokenType.ALIAS and len(after) > 1 and \
                after[1].token_type == TokenType.L_PAREN:
            continue  # a CTE's name and column list: WITH Year (a, b) AS (...)
        if name in _CASTS:
            if count != 1:
                raise ValueError(_NOT_YET + f"{name} takes one value and the type to convert it to, as "
                                 f"{name}(value AS type), and this call has {count} arguments. Nothing ran.")
            continue
        fewest, most = ARITY[name]
        if count < fewest or (most is not None and count > most):
            raise ValueError(_message(name, count, fewest, most))
        if name in WINDOWED and not _over_follows(tokens, end + 1):
            raise ValueError(f"The function '{name}' must have an OVER clause.")
        if name in _WITHIN_GROUP and [t.text.upper() for t in tokens[end + 1:end + 3]] != ["WITHIN", "GROUP"]:
            raise ValueError(_NOT_YET + f"{name} takes WITHIN GROUP (ORDER BY ...) and OVER (...) in T-SQL, such as "
                             f"{name}(0.5) WITHIN GROUP (ORDER BY x) OVER (), and this call has no WITHIN GROUP. "
                             "Nothing ran.")


def _message(name: str, count: int, fewest: int, most: int | None) -> str:
    if most is None:
        return (_NOT_YET + f"{name} takes at least {fewest} argument{'s' if fewest > 1 else ''}, and this call has "
                f"{count}. Nothing ran.")
    if fewest == most:
        return f"The {name.lower()} function requires {fewest} argument(s)."  # Msg 174
    return f"The {name.lower()} function requires {fewest} to {most} arguments."  # Msg 189


def _arguments(tokens: list[Token], open_at: int) -> tuple[int, int | None]:
    """The number of arguments of the call whose ``(`` is at ``open_at``, and where its ``)`` is."""
    depth, commas, empty = 0, 0, True
    for at in range(open_at, len(tokens)):
        kind = tokens[at].token_type
        if kind in (TokenType.L_PAREN, TokenType.L_BRACKET):
            depth += 1
        elif kind in (TokenType.R_PAREN, TokenType.R_BRACKET):
            depth -= 1
            if depth == 0:
                return (0 if empty else commas + 1), at
        elif depth == 1 and kind == TokenType.COMMA:
            commas += 1
        if at > open_at:
            empty = False
    return 0, None


def _over_follows(tokens: list[Token], at: int) -> bool:
    """Whether OVER follows at ``at``, after an IGNORE NULLS or RESPECT NULLS."""
    words = [t.text.upper() for t in tokens[at:at + 3]]
    if words[:2] in (["IGNORE", "NULLS"], ["RESPECT", "NULLS"]):
        words = words[2:]
    return bool(words) and words[0] == "OVER"
