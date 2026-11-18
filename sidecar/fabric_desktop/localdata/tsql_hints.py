"""Local T-SQL's hints, query labels and statistics statements as a Fabric Warehouse has them (WR-231).

From Microsoft Learn's Fabric Data Warehouse pages (T-SQL surface area, query labels, statistics, transactions,
and the Hints (Transact-SQL) page's Fabric section) as they read on 2026-11-18:

- ``OPTION (LABEL = '...')`` names a query in a warehouse's query insights. It changes no rows, so it is left
  out of the run here, with a note.
- A warehouse runs "a subset of query and join hints". The Fabric list: HASH GROUP, ORDER GROUP, MERGE UNION,
  HASH UNION, CONCAT UNION, FORCE ORDER, FORCE SINGLE NODE PLAN, FORCE DISTRIBUTED PLAN, DISABLE_RESULT_SET_CACHE,
  and USE HINT with the four ASSUME_..._FILTER_ESTIMATES names. Each changes how the query is planned, not its
  rows, so each is left out of the run here with a note. Any other query hint (MAXDOP, RECOMPILE, LOOP JOIN,
  FAST n ...) is refused by name, since Microsoft doesn't list it for a warehouse. A join hint written in FROM
  (``INNER HASH JOIN``) changes no rows either and is left out with a note.
- Locking table hints (NOLOCK, HOLDLOCK, TABLOCK, READPAST ...): a warehouse always runs snapshot isolation and
  ignores an isolation change, and here reads are snapshot reads too, so the hint can't change the rows. It is
  left out with a note that says Microsoft's pages don't say whether a warehouse takes it. Index hints (INDEX,
  FORCESEEK, FORCESCAN, NOEXPAND) are refused by name: a warehouse has no indexes to name.
- ``CREATE STATISTICS`` (one column), ``UPDATE STATISTICS`` and ``DROP STATISTICS`` help a warehouse plan and
  change no rows. Here each checks that its table (and column) exist, as a warehouse would, and changes nothing,
  with a note. A statistic on several columns is refused as a warehouse refuses it. Options other than
  FULLSCAN and SAMPLE, and filtered statistics, are refused by name.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

_NOT_YET = "This T-SQL can't be run on the local engine: "
_MARK = "\x01kf-"  # a label the text pass writes in place of what sqlglot can't read; nobody types \x01

_JOIN = {"LOOP", "HASH", "MERGE"}  # the join hints page marks these as applying to a Fabric Warehouse
_PLAN = {"HASH GROUP", "ORDER GROUP", "MERGE UNION", "HASH UNION", "CONCAT UNION", "FORCE ORDER"}
_PLAN_WORDS = {("FORCE", "SINGLE", "NODE", "PLAN"), ("FORCE", "DISTRIBUTED", "PLAN"), ("DISABLE_RESULT_SET_CACHE",)}
_ASSUME = {"ASSUME_MIN_SELECTIVITY_FOR_FILTER_ESTIMATES", "ASSUME_FULL_INDEPENDENCE_FOR_FILTER_ESTIMATES",
           "ASSUME_PARTIAL_CORRELATION_FOR_FILTER_ESTIMATES", "ASSUME_JOIN_PREDICATE_DEPENDS_ON_FILTERS"}
_LOCKING = {"NOLOCK", "READUNCOMMITTED", "READCOMMITTED", "READCOMMITTEDLOCK", "REPEATABLEREAD", "SERIALIZABLE",
            "HOLDLOCK", "SNAPSHOT", "TABLOCK", "TABLOCKX", "UPDLOCK", "XLOCK", "ROWLOCK", "PAGELOCK", "READPAST",
            "NOWAIT"}
_STARTS = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "ALTER", "TRUNCATE", "WITH", "EXEC",
           "EXECUTE", "DECLARE", "SET", "IF", "BEGIN", "COMMIT", "ROLLBACK", "PRINT", "USE", "GRANT", "DENY",
           "REVOKE", "COPY", "OPTION"}
_PUNCTUATION = {TokenType.L_PAREN, TokenType.R_PAREN, TokenType.COMMA, TokenType.DOT, TokenType.SEMICOLON,
                TokenType.STRING, TokenType.NUMBER}
_AFTER_NAME = {"ON", "WITH", "WHERE"}

LABEL_NOTE = ("OPTION (LABEL = ...) names the query in a warehouse's query insights. It changes no rows, so it's left "
              "out of the run here.")
LOCK_NOTE = ("Reads here are snapshot reads, as a warehouse's always are, so the locking hint {hint} changes no rows "
             "and is left out of the run. Microsoft's pages don't say whether a warehouse takes it; test it there.")
_PLAN_NOTE = ("{hint} changes how a warehouse plans the query, not its rows. The local engine plans on its own, so "
              "it's left out of the run here.")
_STATS_NOTE = {
    "CREATE": "CREATE STATISTICS helps a warehouse plan and changes no rows. The local engine keeps its own, so here "
              "it only checks that the table and column exist.",
    "UPDATE": "UPDATE STATISTICS helps a warehouse plan and changes no rows. The local engine keeps its own, so here "
              "it only checks that the table exists.",
    "DROP": "DROP STATISTICS changes no rows. The local engine keeps no statistics of yours, so here it only checks "
            "that the table exists, not the statistic.",
}


# ---- the text pass: what sqlglot can't read, before it reads the batch

def take(batch: str, tokenize: Callable[[str], list[Token]]) -> str:
    """The batch with its statistics statements and the Fabric hints sqlglot can't read written as a query the
    tree pass recognises. ``tokenize`` is the T-SQL tokenizer. Refuses what isn't carried by name."""
    upper = batch.upper()
    if "STATISTICS" not in upper and "OPTION" not in upper:
        return batch
    try:
        tokens: list[Token] = tokenize(batch)
    except SqlglotError:
        return batch  # the parse reports it
    out: list[str] = []
    last = 0
    text: str | None
    i = 0
    while i < len(tokens):
        word = tokens[i].text.upper()
        if word == "UPDATE STATISTICS" and tokens[i].token_type == TokenType.COMMAND:
            # sqlglot reads UPDATE STATISTICS as a command and the rest of the statement as one string, so the
            # rest is read again on its own, and what follows the statement goes through this pass again
            rest = batch[tokens[i].end + 1:]
            try:
                more = tokenize(rest)
            except SqlglotError:
                return batch
            if more and more[0].text.upper() == "SET":  # UPDATE statistics SET ...: a table called statistics
                i += 1
                continue
            words = [Token(TokenType.UPDATE, "UPDATE"), Token(TokenType.VAR, "STATISTICS")] + more
            stop, checks = _statistics(words, 0, "UPDATE")
            after = rest[more[stop - 2].end + 1:] if stop >= 2 else rest
            return "".join(out) + batch[last:tokens[i].start] + checks + take(after, tokenize)
        if word in ("CREATE", "UPDATE", "DROP") and i + 1 < len(tokens) and tokens[i + 1].text.upper() == "STATISTICS" \
                and not (i + 2 < len(tokens) and tokens[i + 2].text.upper() == "SET"):  # UPDATE statistics SET ...
            end, text = _statistics(tokens, i, word)
        elif word == "OPTION" and i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN:
            end, text = _options(tokens, i, batch)
        else:
            i += 1
            continue
        if text is not None:
            out.append(batch[last:tokens[i].start])
            out.append(text)
            last = tokens[end].end + 1
        i = end + 1
    return "".join(out) + batch[last:] if out else batch


def _label(kind: str, what: str) -> str:
    return "LABEL = '" + (_MARK + kind + ":" + what).replace("'", "''") + "'"


def _options(tokens: list[Token], at: int, batch: str) -> tuple[int, str | None]:
    """OPTION (...) with FORCE ... PLAN, DISABLE_RESULT_SET_CACHE or USE HINT written as marked labels."""
    depth, j, items, start = 0, at + 1, [], at + 2
    while j < len(tokens):
        t = tokens[j].token_type
        depth += t == TokenType.L_PAREN
        depth -= t == TokenType.R_PAREN
        if depth == 1 and t == TokenType.COMMA or depth == 0:
            items.append((start, j - 1))
            start = j + 1
        if depth == 0:
            break
        j += 1
    else:
        return at, None
    changed, parts = False, []
    for a, b in items:
        words = tuple(tokens[k].text.upper() for k in range(a, b + 1))
        text = batch[tokens[a].start:tokens[b].end + 1] if b >= a else ""
        if words in _PLAN_WORDS:
            parts.append(_label("hint", " ".join(words)))
            changed = True
        elif words[:2] == ("USE", "HINT"):
            names = [tokens[k].text.upper() for k in range(a, b + 1) if tokens[k].token_type == TokenType.STRING]
            others = [n for n in names if n not in _ASSUME]
            if others or not names:
                raise ValueError(_NOT_YET + f"USE HINT ('{(others or [''])[0]}') isn't carried yet. A Fabric Warehouse "
                                 "takes only the four ASSUME_..._FILTER_ESTIMATES names, which change how it plans, "
                                 "not the rows. Remove the hint, or use one of those four names.")
            parts.append(_label("hint", "USE HINT"))
            changed = True
        else:
            parts.append(text)
    return j, ("OPTION (" + ", ".join(parts) + ")") if changed else None


def _name(tokens: list[Token], i: int) -> tuple[str, int]:
    """A one-, two- or three-part name from tokens[i], bracketed again, and the index after it."""
    parts = []
    while i < len(tokens) and tokens[i].token_type not in _PUNCTUATION and tokens[i].text.upper() not in _AFTER_NAME:
        parts.append("[" + tokens[i].text.replace("]", "]]") + "]")
        i += 1
        if i < len(tokens) and tokens[i].token_type == TokenType.DOT:
            i += 1
            continue
        break
    if not parts:
        raise ValueError(_NOT_YET + "this statistics statement isn't written as T-SQL has it, such as CREATE "
                         "STATISTICS s ON dbo.t (c).")
    return ".".join(parts), i


def _statistics(tokens: list[Token], at: int, verb: str) -> tuple[int, str]:
    """The statement from tokens[at] to its end, and the query that checks its table (and column)."""
    i = at + 2
    if verb == "CREATE":
        _, i = _name(tokens, i)
        if i >= len(tokens) or tokens[i].text.upper() != "ON":
            raise ValueError(_NOT_YET + "CREATE STATISTICS is read here as CREATE STATISTICS s ON dbo.t (c).")
        table, i = _name(tokens, i + 1)
        if i >= len(tokens) or tokens[i].token_type != TokenType.L_PAREN:
            raise ValueError(_NOT_YET + "CREATE STATISTICS is read here as CREATE STATISTICS s ON dbo.t (c).")
        columns, i = [], i + 1
        while i < len(tokens) and tokens[i].token_type != TokenType.R_PAREN:
            if tokens[i].token_type != TokenType.COMMA:
                columns.append("[" + tokens[i].text.replace("]", "]]") + "]")
            i += 1
        if len(columns) != 1:
            raise ValueError("Multi-column statistics aren't supported in a Fabric Warehouse, which creates and "
                             "updates single-column statistics only. Create one statistic for each column.")
        i += 1
        checks = [f"SELECT TOP 0 {columns[0]} FROM {table}"]
    elif verb == "UPDATE":
        table, i = _name(tokens, i)
        if i < len(tokens) and tokens[i].token_type == TokenType.L_PAREN:
            while i < len(tokens) and tokens[i].token_type != TokenType.R_PAREN:
                i += 1
            i += 1
        elif i < len(tokens) and tokens[i].text.upper() not in _STARTS | {"WITH"} \
                and tokens[i].token_type != TokenType.SEMICOLON:
            i += 1  # UPDATE STATISTICS t s
        checks = [f"SELECT TOP 0 * FROM {table}"]
    else:
        checks = []
        while True:
            name, i = _name(tokens, i)
            if "." not in name:
                raise ValueError(_NOT_YET + "DROP STATISTICS names table.statistic, such as DROP STATISTICS dbo.t.s.")
            checks.append(f"SELECT TOP 0 * FROM {name.rsplit('.', 1)[0]}")
            if i < len(tokens) and tokens[i].token_type == TokenType.COMMA:
                i += 1
                continue
            break
    if i < len(tokens) and tokens[i].text.upper() == "WHERE":
        raise ValueError(_NOT_YET + "filtered statistics (CREATE STATISTICS ... WHERE) aren't carried yet. Microsoft's "
                         "pages for a Fabric Warehouse show statistics on a whole column only. Remove the WHERE to "
                         "make a statistic on the whole column.")
    if i < len(tokens) and tokens[i].text.upper() == "WITH" and verb != "DROP":
        i += 1
        while i < len(tokens) and tokens[i].token_type != TokenType.SEMICOLON \
                and tokens[i].text.upper() not in _STARTS:
            word = tokens[i].text.upper()
            if word == "SAMPLE":
                i += 3  # SAMPLE n PERCENT | ROWS
            elif word in ("FULLSCAN", ","):
                i += 1
            else:
                raise ValueError(_NOT_YET + f"the statistics option {tokens[i].text} isn't carried yet. Use FULLSCAN "
                                 "or SAMPLE n PERCENT, which change nothing here.")
    end = i - 1
    if i < len(tokens) and tokens[i].token_type == TokenType.SEMICOLON:
        end = i
    elif i < len(tokens) and tokens[i].text.upper() not in _STARTS:
        raise ValueError(_NOT_YET + f"{verb} STATISTICS is followed by {tokens[i].text}, which isn't read here. Write "
                         "it as Microsoft's page does, such as CREATE STATISTICS s ON dbo.t (c) WITH FULLSCAN.")
    mark = " OPTION (" + _label("statistics", verb) + ")"
    return end, "; ".join(c + mark for c in checks) + ";"


# ---- the tree pass

def check(tree: exp.Expression) -> list[str]:
    """Leaves out, with a note, the labels and hints that change no rows; refuses the rest by name."""
    notes: list[str] = []
    for owner in [n for n in tree.walk() if isinstance(n, exp.Expression) and n.args.get("options")]:
        kept = []
        for option in owner.args["options"]:
            name = option.this.sql(dialect="tsql").upper() if option.this is not None else ""
            if name == "LABEL":
                notes.append(_labelled(option.expression))
            elif name in _PLAN:
                notes.append(_PLAN_NOTE.format(hint=f"OPTION ({name})"))
            elif name == "MAXRECURSION":
                kept.append(option)  # tsql_ctes refuses it
            else:
                raise ValueError(_NOT_YET + f"OPTION ({name}) isn't carried yet. It isn't among the query hints "
                                 "Microsoft lists for a Fabric Warehouse; those that are (FORCE ORDER, HASH GROUP, "
                                 "MERGE UNION and others) change how it plans, not the rows. Remove the hint.")
        owner.set("options", kept or None)
    for hint in list(tree.find_all(exp.WithTableHint)):
        for h in hint.expressions:
            word = (h.name if isinstance(h, (exp.Var, exp.Anonymous)) else h.sql(dialect="tsql")).upper()
            if word not in _LOCKING:
                raise ValueError(_NOT_YET + f"the table hint {word} isn't carried yet. A Fabric Warehouse has no "
                                 "indexes to name, and Microsoft's pages don't list table hints for it. Remove the hint.")
            notes.append(LOCK_NOTE.format(hint=f"WITH ({word})"))
        hint.pop()
    for join in tree.find_all(exp.Join):
        if join.args.get("hint"):
            word = str(join.args["hint"]).upper()
            if word not in _JOIN:
                raise ValueError(_NOT_YET + f"the join hint {word} JOIN isn't carried yet. Microsoft's join hints page "
                                 "lists LOOP, HASH and MERGE for a Fabric Warehouse, not " + word + ". Remove the hint.")
            notes.append(_PLAN_NOTE.format(hint=f"The join hint {str(join.args['hint']).upper()} JOIN"))
            join.set("hint", None)
    return list(dict.fromkeys(notes))


def _labelled(value: exp.Expression | None) -> str:
    text = value.name if isinstance(value, exp.Literal) else ""
    if text.startswith(_MARK + "statistics:"):
        return _STATS_NOTE.get(text.rsplit(":", 1)[1], LABEL_NOTE)
    if text.startswith(_MARK + "hint:"):
        return _PLAN_NOTE.format(hint=f"OPTION ({text.split(':', 1)[1]})")
    return LABEL_NOTE
