"""Project.version reads the project's own files and never walks .local.

.local holds the environment's tables on this PC, and every run adds files to them. Version was
worked out by listing every file under the project and then dropping .local's, twice per run, so
each run got slower than the last: in an 8-hour soak, a metadata run went from 83 ms to 1.4 s in
30 minutes, 346 ms of it per version at 6,950 files (local-performance/soak.md).
"""

import hashlib
import os

from fabric_desktop.meta import project as project_mod
from fabric_desktop.meta.project import Project
from fabric_desktop.meta.templates import init_project


def listed_then_dropped(p: Project) -> str:
    """Version as it was worked out before: every path listed, the ignored ones dropped."""
    h = hashlib.sha1()
    for f in sorted(p.root.rglob("*")):
        rel = f.relative_to(p.root)
        if f.is_file() and not (set(rel.parts) & project_mod._IGNORED) and f.suffix != ".pyc" \
                and rel.as_posix() not in project_mod._UNVERSIONED:
            h.update(rel.as_posix().encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:7]


def project_with_tables(tmp_path):
    root = tmp_path / "p"
    init_project(root, "3", "Chris", sample=False)
    for folder in (".local/dev/LH_Bronze/Tables/dbo/t/_delta_log", "transforms/__pycache__", ".git/objects",
                   "metadata/sub/.pytest_cache"):
        (root / folder).mkdir(parents=True, exist_ok=True)
        for i in range(30):
            (root / folder / f"{i:05d}.json").write_text(str(i))
    (root / "transforms" / "clean.py").write_text("def clean(df):\n    return df\n")
    (root / "transforms" / "clean.pyc").write_bytes(b"\0")
    (root / "notes.txt").write_text("a\r\nb\n")
    return Project(root)


def test_the_version_is_the_same_hash_as_before(tmp_path):
    p = project_with_tables(tmp_path)
    assert p.version == listed_then_dropped(p)
    (p.root / "transforms" / "clean.py").write_text("def clean(df):\n    return df.head()\n")
    assert p.version == listed_then_dropped(p)  # still follows every edit


def test_the_version_never_walks_the_tables_on_this_pc(tmp_path, monkeypatch):
    p = project_with_tables(tmp_path)
    walked = []
    real = os.walk

    def walk(top, *a, **kw):
        for folder, dirs, files in real(top, *a, **kw):
            walked.append(os.path.relpath(folder, p.root))
            yield folder, dirs, files
    monkeypatch.setattr(project_mod.os, "walk", walk)
    assert p.version
    assert walked and not [f for f in walked if set(f.split(os.sep)) & project_mod._IGNORED]
