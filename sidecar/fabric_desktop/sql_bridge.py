"""Small CLI for the Rust engine: SQL classification and translation via sqlglot.

Reads one JSON object from stdin, writes one JSON object to stdout.

  classify: {"query": "...", "dialect": "spark", "allow": ["read_only", ...]}
  translate: {"query": "...", "dialect": "spark"}
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import sql, sql_policy


def _read() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line.strip():
        raise ValueError("Expected JSON on stdin.")
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def classify(payload: dict[str, Any]) -> dict[str, Any]:
    query = payload.get("query")
    dialect = str(payload.get("dialect") or "spark")
    allow = payload.get("allow") or ["read_only"]
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    if not isinstance(allow, list):
        raise ValueError("allow must be a list.")
    statements = sql.parse(query, dialect)
    verdicts = sql_policy.classify(statements)
    stop = sql_policy.refused(verdicts, tuple(allow))
    return {
        "statements": len(statements),
        "permission": sql_policy.strongest(verdicts),
        "verdicts": [{"statement": v.statement, "kind": v.kind, "reason": v.reason} for v in verdicts],
        "refused": None
        if stop is None
        else {
            "permission": stop.kind,
            "statement": stop.statement,
            "label": sql_policy.LABELS.get(stop.kind, ""),
            "message": f"Statement {stop.statement} {sql_policy.LABELS[stop.kind]} ({stop.reason}), so nothing ran.",
        },
    }


def translate(payload: dict[str, Any]) -> dict[str, Any]:
    query = payload.get("query")
    dialect = str(payload.get("dialect") or "spark")
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    statements = sql.parse(query, dialect)
    return {
        "statements": [s.sql("duckdb") for s in statements],
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m fabric_desktop.sql_bridge classify|translate")
    op = sys.argv[1]
    payload = _read()
    try:
        if op == "classify":
            _write(classify(payload))
        elif op == "translate":
            _write(translate(payload))
        else:
            raise ValueError(f"unknown op {op!r}")
    except Exception as exc:  # noqa: BLE001 - CLI reports errors as JSON
        _write({"error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
