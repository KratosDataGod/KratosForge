"""Fabric Desktop sidecar: local compute, OneLake storage."""

__version__ = "0.20.0"

try:  # DuckDB 1.x stops reading SQL at a NUL character; guard its entry points before any module uses them
    from . import duckdb_nul
except ImportError:  # silent-ok: without DuckDB there is nothing to guard
    pass
else:
    duckdb_nul.install()
