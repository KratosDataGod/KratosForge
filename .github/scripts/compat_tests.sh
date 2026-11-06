#!/usr/bin/env bash
# The engine tests again on other versions of two packages pyproject allows, run from sidecar/ after
# the pinned install (ci.yml's sidecar job on Linux, and compat-nightly.yml):
#   sqlglot  the SQL tests on the oldest and newest sqlglot of pyproject's range (#1252)
#   duckdb   the whole engine suite on the next DuckDB major (#1341)
# Each version goes into its own folder ahead of the pinned one on PYTHONPATH; nothing else changes.
set -euo pipefail
case "${1:-}" in
  sqlglot)
    for v in $(python tests/test_sqlglot_version.py); do
      python -m pip install --quiet --no-deps --target "$RUNNER_TEMP/sqlglot-$v" "sqlglot==$v"
      PYTHONPATH="$RUNNER_TEMP/sqlglot-$v:$PWD" python -m pytest -q -n auto --dist loadfile -p no:cacheprovider \
        -k "sql or spark or sqlglot or ossie or databricks or tsql or snowflake"
    done
    ;;
  duckdb)
    v=$(python tests/test_duckdb_version.py)
    python -m pip install --quiet --no-deps --target "$RUNNER_TEMP/duckdb-$v" "duckdb==$v"
    PYTHONPATH="$RUNNER_TEMP/duckdb-$v:$PWD" python -m pytest -q -n auto --dist loadfile -p no:cacheprovider
    ;;
  *)
    echo "usage: compat_tests.sh sqlglot|duckdb" >&2
    exit 2
    ;;
esac
