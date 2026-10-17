"""Reads SQL into statements and tokens for the rules, and turns their findings into a report."""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError, TokenError
from sqlglot.tokens import TokenType

from .profiles import RULE_VERSION, doc, profile

SEVERITIES = ("error", "warning", "information", "unverified")
SCHEMA = "sql-compat-report@1"
MAX_QUERY = 500_000  # characters (about 7 s on a laptop); a bigger script is checked in parts
MAX_DIAGNOSTICS = 1_000  # listed in the report; the counts always cover every finding


@dataclass
class Tok:
    type: TokenType
    raw: str      # exactly as written, quotes included
    text: str     # sqlglot's text: names without their quotes
    kw: str       # upper-case word for keyword matching; empty for names in quotes and strings
    start: int    # offset in the whole query
    end: int      # exclusive
    depth: int    # parentheses depth within the statement


@dataclass
class Stmt:
    index: int
    start: int
    end: int
    text: str
    toks: list[Tok]
    target: str
    ast: exp.Expression | None = None
    error: str = ""
    error_at: int | None = None

    @property
    def kws(self) -> list[str]:
        return [t.kw for t in self.toks]

    def first(self, n: int = 1) -> list[str]:
        return [t.kw for t in self.toks[:n]]

    def find(self, *words: str | tuple[str, ...], start: int = 0) -> list[int]:
        """Indexes where the keywords ``words`` appear in a row (a tuple matches any of its words)."""
        found = []
        for i in range(start, len(self.toks) - len(words) + 1):
            if all(self.toks[i + k].kw in ((w,) if isinstance(w, str) else w) for k, w in enumerate(words)):
                found.append(i)
        return found

    def has(self, *words: str | tuple[str, ...]) -> bool:
        return bool(self.find(*words))


@dataclass
class Hit:
    rule: str
    start: int
    end: int
    detail: dict[str, Any] = field(default_factory=dict)


def _tokens(query: str, dialect: str) -> list[Any]:
    return sqlglot.Dialect.get_or_raise(dialect).tokenize(query)


def split(query: str, target: str) -> tuple[list[Stmt], str]:
    """The statements of ``query``, split at ``;`` (and T-SQL's ``GO``), each with its tokens."""
    p = profile(target)
    try:
        raw = _tokens(query, p["sqlglot"])
    except (TokenError, SqlglotError, ValueError) as exc:
        return [], f"The SQL couldn't be read: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
    statements: list[list[Any]] = [[]]
    for t in raw:
        is_go = (t.token_type == TokenType.COMMAND and t.text.upper() == "GO") or \
                (p["sqlglot"] == "tsql" and t.token_type == TokenType.VAR and t.text.upper() == "GO" and _alone_on_line(query, t))
        if t.token_type == TokenType.SEMICOLON or is_go:
            statements.append([])
        else:
            statements[-1].append(t)
    out = []
    for group in statements:
        if not group:
            continue
        depth = 0
        toks = []
        for t in group:
            if t.token_type == TokenType.R_PAREN:
                depth = max(0, depth - 1)
            raw_text = query[t.start:t.end + 1]
            quoted = t.token_type in (TokenType.IDENTIFIER, TokenType.STRING, TokenType.NATIONAL_STRING)
            if not quoted and any(ch.isspace() for ch in raw_text):
                # sqlglot reads ORDER BY, PRIMARY KEY, ... as one token; the rules match word by word
                for m in re.finditer(r"\S+", raw_text):
                    word = m.group()
                    toks.append(Tok(t.token_type, word, word, word.upper(), t.start + m.start(), t.start + m.end(), depth))
            else:
                toks.append(Tok(t.token_type, raw_text, t.text, "" if quoted else raw_text.upper(), t.start,
                                t.end + 1, depth))
            if t.token_type == TokenType.L_PAREN:
                depth += 1
        start, end = toks[0].start, toks[-1].end
        out.append(Stmt(len(out), start, end, query[start:end], toks, target))
    return out, ""


def _alone_on_line(query: str, t: Any) -> bool:
    line_start = query.rfind("\n", 0, t.start) + 1
    line_end = query.find("\n", t.end + 1)
    line = query[line_start:len(query) if line_end == -1 else line_end]
    return line.strip().upper() == "GO"


# Statements sqlglot doesn't read but that are ordinary commands of the engine: the token rules check
# them, and a parse failure isn't reported as "not checked".
COMMANDS = {
    "tsql": {"SAVE", "DBCC", "BACKUP", "RESTORE", "GRANT", "DENY", "REVOKE", "PRINT", "RAISERROR", "THROW",
             "WAITFOR", "OPEN", "CLOSE", "FETCH", "DEALLOCATE", "CHECKPOINT", "USE"},
    "spark": {"CACHE", "UNCACHE", "REFRESH", "MSCK", "OPTIMIZE", "VACUUM", "ANALYZE", "SHOW", "DESCRIBE", "DESC",
              "USE", "SET", "RESET", "ADD", "LIST", "CLEAR"},
    "duckdb": {"SHOW", "DESCRIBE", "SUMMARIZE", "PRAGMA", "SET", "RESET", "USE", "CHECKPOINT"},
}
# T-SQL that Fabric documents but sqlglot doesn't read; blanked (same length, so positions hold) for parsing only
_TSQL_BLANK = [re.compile(r"\bNOT\s+ENFORCED\b", re.IGNORECASE), re.compile(r"\bNONCLUSTERED\b(?!\s*\()", re.IGNORECASE)]
_log = logging.getLogger("sqlglot")


def parse(stmt: Stmt, dialect: str) -> None:
    text = stmt.text
    if dialect == "tsql":
        for pattern in _TSQL_BLANK:
            text = pattern.sub(lambda m: " " * len(m.group()), text)
    level = _log.level
    _log.setLevel(logging.ERROR)  # sqlglot's warnings quote the SQL, which can hold secrets; keep it out of logs
    try:
        trees = [t for t in sqlglot.parse(text, read=dialect, error_level=sqlglot.ErrorLevel.RAISE) if t is not None]
        stmt.ast = trees[0] if trees else None
    except (ParseError, TokenError, SqlglotError, RecursionError, ValueError) as exc:
        if stmt.toks and stmt.toks[0].kw in COMMANDS.get(dialect, ()):
            return
        errors = getattr(exc, "errors", None) or []
        first = errors[0] if errors else {}
        stmt.error = (first.get("description") or str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
        stmt.error = stmt.error.split("<Token")[0].strip().rstrip(":") or "unexpected syntax"
        if first.get("start") is not None:
            stmt.error_at = stmt.start + int(first["start"])
    finally:
        _log.setLevel(level)


def analyze(query: str, target: str | None = None) -> dict[str, Any]:
    """Check ``query`` against ``target``'s rules without running it. Returns the report."""
    from .rules import RULES, run_checks  # rules import this module's types

    p = profile(target)
    report: dict[str, Any] = {
        "schema": SCHEMA, "rule_version": RULE_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"id": p["id"], "label": p["label"], "engine": p["engine"], "runs_here": p["runs_here"]},
        "statements": 0, "diagnostics": [], "truncated": False,
        "notes": ["Static analysis: the SQL was read, not run, and nothing was sent to Fabric.",
                  "No findings means no known problem, not proof that the query runs or gives the same results in "
                  f"{p['short']}.",
                  "A warning marks a risk or a difference; it doesn't mean the query is wrong."],
    }
    if len(query) > MAX_QUERY:
        report["outcome"] = _outcome([], 0, p, f"The SQL is longer than {MAX_QUERY:,} characters, so it wasn't checked.")
        report["counts"] = _counts([])
        return report
    stmts, problem = split(query, p["id"])
    lines = _line_starts(query)
    diags: list[dict[str, Any]] = []
    if problem:
        diags.append(_diag(RULES["SYN-007"], Hit("SYN-007", 0, min(len(query), 1), {"error": problem}), p, lines, 0))
    for stmt in stmts:
        parse(stmt, p["sqlglot"])
        for hit in run_checks(stmt, p["id"]):
            diags.append(_diag(RULES[hit.rule], hit, p, lines, stmt.index + 1))
    diags = _dedupe(diags)
    diags.sort(key=lambda d: (d["from"], SEVERITIES.index(d["severity"]), d["rule"]))
    report.update(statements=len(stmts), diagnostics=diags[:MAX_DIAGNOSTICS], truncated=len(diags) > MAX_DIAGNOSTICS,
                  counts=_counts(diags), outcome=_outcome(diags, len(stmts), p))
    return report


def report_json(report: dict[str, Any], query: str) -> str:
    """The report as the JSON file people export: the SQL it was about is included, with its hash."""
    out = {**report, "sql": query, "sql_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest()}
    return json.dumps(out, indent=2, ensure_ascii=False)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _pos(lines: list[int], offset: int) -> tuple[int, int]:
    line = bisect.bisect_right(lines, offset) - 1
    return line + 1, offset - lines[line] + 1


def _diag(rule: Any, hit: Hit, p: dict[str, Any], lines: list[int], statement: int) -> dict[str, Any]:
    line, col = _pos(lines, hit.start)
    end_line, end_col = _pos(lines, max(hit.start, hit.end))
    detail = dict(hit.detail)
    severity = detail.pop("severity", None) or rule.level(p["id"])
    fill = {"target": p["short"], "engine": p["short"], **detail}
    return {
        "severity": severity, "rule": rule.id, "title": rule.title.format(**fill), "category": rule.category,
        "message": rule.reason.format(**fill), "action": rule.action.format(**fill), "target": p["id"],
        "line": line, "col": col, "end_line": end_line, "end_col": end_col, "from": hit.start, "to": max(hit.start, hit.end),
        "statement": statement, "doc": doc(rule.doc), "confidence": rule.confidence,
    }


def _dedupe(diags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for d in diags:
        key = (d["rule"], d["from"], d["to"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _counts(diags: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for d in diags if d["severity"] == s) for s in SEVERITIES}


def _outcome(diags: list[dict[str, Any]], statements: int, p: dict[str, Any], why: str = "") -> dict[str, str]:
    counts = _counts(diags)
    if why:
        return {"status": "not_checked", "text": why}
    if not statements and not diags:
        return {"status": "not_checked", "text": "There is no SQL to check."}
    if counts["error"]:
        return {"status": "known_problems",
                "text": f"Known problems: this SQL uses things {p['short']} doesn't support. Change it before running it there."}
    if counts["warning"]:
        return {"status": "review",
                "text": f"Review the warnings: {p['short']} may run this SQL differently than you expect, or it changes data."}
    if counts["unverified"]:
        return {"status": "unverified",
                "text": f"Not verified: some of this SQL couldn't be checked against {p['short']}. Test it there."}
    return {"status": "no_known_problems",
            "text": f"No known problems found by static checks. That isn't proof it runs in {p['short']}; test it there."}
