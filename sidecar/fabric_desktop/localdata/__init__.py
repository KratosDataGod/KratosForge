"""Local data projects: SQL over files and tables on this PC, starting no Fabric job (FD-LDE).

OneLake reads and writes (a later publish) still use the workspace's capacity.

    projects.py    the projects, their settings and registered folders (project.json)
    discovery.py   which files in a folder become which tables
    engine.py      one DuckDB database per project: views over the files, queries, cancel, limits
    delta.py       the project's own Delta tables: SQL that writes them, reading one as of a version
    manager.py     the open engines, and queries run in the background for the app to poll

Plan and story map: docs/local-data-engine-plan.md.
"""

from .engine import EngineBusy, LocalEngine, QueryCancelled
from .manager import LocalData

__all__ = ["EngineBusy", "LocalData", "LocalEngine", "QueryCancelled"]
