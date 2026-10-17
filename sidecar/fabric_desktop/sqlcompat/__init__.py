"""SQL-002: check SQL against a Fabric SQL engine before running it, without running it.

Fabric has several SQL engines and they don't take the same SQL: Spark SQL in a lakehouse, T-SQL in a
Fabric Warehouse, in its SQL analytics endpoint (read-only tables) and in a Fabric SQL database, and
DuckDB, which runs the SQL page's queries on this PC. ``analyze(query, target)`` reads the SQL with
sqlglot and the target's tokenizer and applies the target's rules (rules.py), each taken from
Microsoft's Fabric documentation and carrying its source, a confidence and the rule set's version.

What it never does:
- run the SQL, connect to Fabric, or change the SQL;
- say the SQL is compatible. No findings means no *known* problem; static checks can't prove that a
  query runs, or gives the same answer, in Fabric. Constructs it can't check are reported as
  Unverified, not passed;
- treat a query that runs on this PC (DuckDB) as proof that it runs in a Fabric Warehouse.
"""

from __future__ import annotations

from .analyzer import analyze, report_json
from .profiles import PROFILES, RULE_VERSION, profile, profiles
from .rules import RULES

__all__ = ["analyze", "report_json", "PROFILES", "RULE_VERSION", "RULES", "profile", "profiles"]
