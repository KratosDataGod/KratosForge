"""Job definitions and local execution.

A job is a TOML file. SQL jobs run in DuckDB; Python jobs run a script whose
``transform()`` returns a Polars DataFrame (or anything Arrow-compatible).

    name = "sales_by_region"
    engine = "duckdb"            # or "polars"
    sql = "select region, sum(amount) as total from read_csv_auto('data/sales.csv') group by region"
    # script = "transform.py"    # for engine = "polars"

    [target]
    workspace_id = "<workspace guid>"
    lakehouse = "Sales"          # created if missing
    schema = "dbo"
    table = "sales_by_region"
    mode = "overwrite"           # or "append"
"""

from __future__ import annotations

import os
import runpy
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa

from .duckdb_rows import keep_try_cast
from .onelake import check_name

ENGINES = ("duckdb", "polars")
MODES = ("overwrite", "append")


@dataclass(frozen=True)
class Target:
    workspace_id: str
    lakehouse: str
    table: str
    schema: str = "dbo"
    mode: str = "overwrite"


@dataclass(frozen=True)
class Job:
    name: str
    engine: str
    base_dir: Path
    target: Target
    sql: str | None = None
    script: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Job":
        path = Path(path).resolve()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        target = Target(**raw.pop("target"))
        job = cls(base_dir=path.parent, target=target, **raw)
        job.validate()
        return job

    def validate(self) -> None:
        if self.engine not in ENGINES:
            raise ValueError(f"engine must be one of {ENGINES}, got {self.engine!r}")
        if self.engine == "duckdb" and not self.sql:
            raise ValueError("duckdb jobs need a `sql` query")
        if self.engine == "polars" and not self.script:
            raise ValueError("polars jobs need a `script` path")
        if self.target.mode not in MODES:
            raise ValueError(f"target.mode must be one of {MODES}")
        check_name("schema", self.target.schema)
        check_name("table", self.target.table)
        # The name is a folder on this PC (publish.stage_locally), so it must stay one plain folder name.
        if not self.name.strip() or self.name in (".", "..", "_writes") or any(c in self.name for c in "/\\:"):
            raise ValueError(f"Invalid job name {self.name!r}: use a plain name, without / \\ : or .., and not _writes.")


@contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run_local(job: Job) -> pa.Table:
    """Run the job on this machine and return the result as Arrow."""
    with _cwd(job.base_dir):
        if job.engine == "duckdb":
            with keep_try_cast(duckdb.connect()) as con:
                # UTC like Fabric, not this PC. SET (not connect's config) uses the built-in ICU;
                # config makes DuckDB try to download it, which fails offline and on locked-down PCs.
                con.execute("SET TimeZone = 'UTC'")
                rel = con.sql(job.sql)
                # to_arrow_table replaced fetch_arrow_table in newer DuckDB releases.
                return (getattr(rel, "to_arrow_table", None) or rel.fetch_arrow_table)()
        namespace = runpy.run_path(str(job.base_dir / job.script))
        if "transform" not in namespace:
            raise ValueError(f"{job.script} must define transform()")
        result = namespace["transform"]()
    if hasattr(result, "to_arrow"):
        result = result.to_arrow()
    if not isinstance(result, pa.Table):
        result = pa.table(result)
    return result
