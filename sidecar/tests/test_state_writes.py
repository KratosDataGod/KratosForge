"""#1204: files the engine reads back are written whole, through jsonfile, or not at all.

``jsonfile.write_text`` writes a temporary file and swaps it in with ``os.replace``, so a crash,
power cut or virus-scanner lock mid-save leaves the old file or the new one, never half of each.
This test parses ``fabric_desktop`` and fails on a ``write_text`` / ``write_bytes`` / ``open(..., "w")``
straight onto a file, unless it writes a temporary file the same function then swaps in, or it is
listed below with why writing in place is right there. Appends (``"a"``) are logs and are not checked.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_json_files import write_fails_midway  # noqa: F401

import fabric_desktop

PACKAGE = Path(fabric_desktop.__file__).parent

# file:function -> why it writes in place
IN_PLACE = {
    "backup.py:restore": "the safety copy of a file being replaced goes to a new folder; the restored file itself is "
                         "written aside, checked against its hash and swapped in",
    "benchmark.py:write": "writes the example benchmark notebooks into the repository (examples/benchmark)",
    "central/entra_service.py:_entra_script": "a generated script the person downloads",
    "dbx_livetest.py:_finish": "the report of a finished live test, written once to its own new folder",
    "environments.py:_run": "pip's constraints file, rewritten before each install and read only by that install",
    "fabric_compute.py:run_bundle": "runs on Fabric as a stand-alone bundle (no engine package there) and writes "
                                    "its result once to a new file named for the request",
    "frames.py:write_xlsx": "a member inside a new xlsx export, not a file on disk",
    "learn/guides.py:<module>": "a build script run by hand to regenerate a data file in the repository",
    "learn/patterns.py:<module>": "a build script run by hand to regenerate a data file in the repository",
    "leases.py:create": "written aside and linked into place with os.link, which refuses to replace a file",
    "livetest.py:_finish": "the report of a finished live test, written once to its own new folder",
    "meta/snapshot.py:restore": "written into a new temporary folder that is checked and then swapped in whole",
    "model_dictionary_service.py:meta_model_dictionary_export": "an export the person asked for",
    "notebook_batch_service.py:notebook_batch_stop": "a stop marker: only whether it exists is read",
    "notebookutils_local.py:_write": "notebookutils.fs.put from the person's own notebook code, as on Fabric",
    "onelake.py:download": "a downloaded file, streamed as it arrives; the caller owns where it goes",
    "proof_runtime.py:save": "runs in the notebook's kernel (on Fabric or this PC) and writes a proof side's log once",
    "prove_service.py:replay_bundle_open": "named by the hash of its own bytes, so writing it again writes the same file",
    "smoketest.py:_finish": "the report of a finished smoke test, written once to its own new folder",
    "spark_stream.py:_to_files": "a streaming sink's output part, a new uniquely named file per batch",
}


class _Finder(ast.NodeVisitor):
    def __init__(self, file: str):
        self.file, self.where, self.found = file, [], set()

    def visit_FunctionDef(self, node):
        swaps = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "replace"
                    and (len(n.args) == 2 or (isinstance(n.func.value, ast.Name) and "tmp" in n.func.value.id))
                    for n in ast.walk(node))
        self.where.append((node.name, swaps))
        self.generic_visit(node)
        self.where.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        target = _written(node)
        if target is not None:
            name, swaps = self.where[-1] if self.where else ("<module>", False)
            if not (swaps and "tmp" in ast.unparse(target).lower()):
                self.found.add(f"{self.file}:{name}")
        self.generic_visit(node)


def _written(node: ast.Call) -> ast.expr | None:
    """What ``node`` writes over, when it is a whole-file write."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes") and not (
            isinstance(func.value, ast.Name) and func.value.id == "jsonfile"):
        return func.value
    mode = None
    if isinstance(func, ast.Name) and func.id == "open" and len(node.args) > 1:
        mode, target = node.args[1], node.args[0]
    elif isinstance(func, ast.Attribute) and func.attr == "open" and node.args:
        mode, target = node.args[0], func.value
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "w" in mode.value:
        return target
    return None


def _in_place() -> list[str]:
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "jsonfile.py":
            continue
        finder = _Finder(path.relative_to(PACKAGE).as_posix())
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        found |= finder.found
    return sorted(found)


def test_every_whole_file_write_is_swapped_in_or_listed():
    in_place = _in_place()
    unlisted = [w for w in in_place if w not in IN_PLACE]
    assert not unlisted, ("write these with jsonfile.write_text, or list them above with why in place is right: "
                          f"{unlisted}")
    stale = sorted(set(IN_PLACE) - set(in_place))
    assert not stale, f"listed above but no longer written in place; remove them: {stale}"


def test_the_check_finds_a_write_in_place(tmp_path, monkeypatch):
    (tmp_path / "new.py").write_text("def save(p, tmp):\n    p.write_text('x')\n    tmp.write_text('y')\n"
                                     "    tmp.replace(p)\n\ndef ok(p):\n    tmp = p.with_suffix('.tmp')\n"
                                     "    tmp.write_text('z')\n    tmp.replace(p)\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "PACKAGE", tmp_path)
    assert _in_place() == ["new.py:save"]


def test_a_live_test_ledger_save_cut_short_keeps_the_last_ledger(tmp_path, write_fails_midway):
    """The ledger is what cleanup reads to delete what a live test made: half of one would leave
    Fabric items behind with nothing pointing at them."""
    from fabric_desktop.livetest import Ledger
    ledger = Ledger(tmp_path / "ledger.json", "run-1", "ws-1", [{"type": "Lakehouse", "id": "lh-1"}])
    before = json.dumps({"run_id": "run-1", "workspace_id": "ws-1", "created": ledger.created}, indent=2)
    ledger.path.write_bytes(before.encode("utf-8"))
    with pytest.raises(OSError):
        ledger.add(type="Notebook", id="nb-1")
    assert ledger.path.read_bytes() == before.encode("utf-8")
    assert Ledger.load(ledger.path).created == [{"type": "Lakehouse", "id": "lh-1"}]
