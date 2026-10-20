"""Validate notebook (NB-017, NB-018): code checks that read the cells, and a clean run that runs
them from the top in a fresh kernel process, with table writes kept in a sandbox folder."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, validate
from fabric_desktop.server import handle
from fabric_desktop.service import Service

LH = {"name": "LH_Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


def code(source, **meta):
    return {"type": "code", "source": source, "meta": meta}


def findings(out, code_=None):
    return [f for f in out["findings"] if code_ is None or f["code"] == code_]


# ---- code checks

def test_a_name_defined_in_a_later_cell_is_an_error_and_an_unknown_one_a_warning():
    cells = [code("from fabric_desktop import lakehouse"), code("total = rows + 1"), code("rows = 3"),
             code("print(nmae)")]
    out = validate.check(cells, None)
    later = findings(out, "defined_later")
    assert [(f["severity"], f["cell"]) for f in later] == [("error", 2)]
    assert "rows is defined in cell 3" in later[0]["text"] and later[0]["fix"].startswith("Move cell 3 above cell 2")
    assert [(f["severity"], f["cell"]) for f in findings(out, "undefined")] == [("warning", 4)]
    assert validate.verdict(out["findings"], None) == "not_ready"


def test_a_name_read_only_inside_a_function_body_is_read_when_the_function_is_called():
    cells = [code("def f(x):\n    return x + y\nadd = lambda v: v + y"), code("y = 2\nprint(f(1), add(1))")]
    out = validate.check(cells, None)
    [f] = findings(out, "defined_later")
    assert f["severity"] == "info" and f["cell"] == 1 and "when it's called" in f["text"]
    assert validate.verdict(out["findings"], None) == "checked"
    # Read at definition time (a default value), it's still an error.
    out = validate.check([code("def f(x=y):\n    return x"), code("y = 2")], None)
    assert [g["severity"] for g in findings(out, "defined_later")] == ["error"]


def test_a_name_used_above_its_definition_in_the_same_cell_is_an_error():
    [f] = findings(validate.check([code("x = y + 1\ny = 2")], None), "defined_later")
    assert f["severity"] == "error" and f["title"] == "Cell 1 uses y before defining it"
    assert f["fix"] == "Define y above the line that uses it."


def test_a_cell_that_does_not_parse_is_an_error():
    out = validate.check([code("x = (1,")], None)
    [f] = findings(out, "syntax")
    assert f["severity"] == "error" and f["cell"] == 1 and "parse" in f["title"]


def test_a_secret_in_code_is_an_error_and_is_shown_masked():
    out = validate.check([code('client_secret = "s3cr3t-value-123456"')], None)
    [f] = findings(out, "secret")
    assert f["severity"] == "error" and "s3cr3t-value-123456" not in json.dumps(out)


def test_where_it_runs_decides_whether_a_fabric_only_cell_is_an_error():
    cells = [code("%%sql\nMERGE INTO dbo.t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")]
    sev = {where: [f["severity"] for f in findings(validate.check(cells, LH, where), "runs_on_pc")]
           for where in ("pc", "fabric", None)}
    assert sev == {"pc": ["error"], "fabric": ["info"], None: ["warning"]}


def test_the_clock_pip_parameters_frozen_cells_and_the_lakehouse_are_reported():
    cells = [code("run_date = '2026-10-18'", tags=["parameters"]), code("%pip install rich\nimport datetime\nt = datetime.datetime.now()"),
             code("from fabric_desktop import lakehouse\ndf = lakehouse.read_table('dbo.orders')\n"
                  "lakehouse.write_table(df, 'dbo.out')"), code("x = 1", frozen=True)]
    out = validate.check(cells, LH, "pc")
    by = {f["code"]: f for f in out["findings"]}
    assert by["side_effect"]["severity"] == "warning" and by["side_effect"]["title"] == "Cell 2 reads the clock or random values"
    assert by["pip"]["severity"] == "warning"
    assert by["parameters"]["severity"] == "info" and "run_date" in by["parameters"]["text"]
    assert by["frozen"]["text"].startswith("Frozen cells don't run")
    assert by["lakehouse"]["text"] == "Reads dbo.orders. Writes dbo.out."
    assert out["counts"]["error"] == 0 and validate.verdict(out["findings"], None) == "checked"


def test_no_lakehouse_is_an_error_when_the_cells_read_tables():
    out = validate.check([code("from fabric_desktop import lakehouse\nlakehouse.read_table('dbo.t')")], None)
    assert [f["severity"] for f in findings(out, "no_lakehouse")] == ["error"]


def test_the_run_plan_refuses_writes_it_cannot_contain_and_lists_what_it_cannot_contain():
    direct = validate.run_plan([code("from deltalake import write_deltalake\nwrite_deltalake('abfss://x', t)")], LH)
    assert not direct["ok"] and direct["blockers"][0]["cell"] == 1
    web = validate.run_plan([code("import requests\nrequests.get('https://example.com')")], LH)
    assert web["ok"] and web["uncontained"] == [{"cell": 1, "cell_id": None, "line": 2,
                                                 "text": "A clean run really makes the call."}]


def test_spark_notebooks_get_code_checks_only_for_now():
    out = validate.check([code("df = spark.read.table('dbo.orders')\ndisplay(df)")], LH, "pc")
    [f] = findings(out, "spark_code_checks_only")
    assert f["severity"] == "info" and f["cell"] == 1 and not out["run"]["ok"]
    assert any("doesn't run Spark cells yet" in t for t in out["limits"])


def test_local_files_and_keys_the_code_checks_find():
    effects = lambda src: [f["title"] for f in findings(validate.check([code(src)], None), "side_effect")]
    assert effects("text = open('a').read()") == []  # opening to read is not a write
    assert effects("open('a', 'w').write('x')") == ["Cell 1 writes files on this PC"]
    assert effects("import os\nopen(os.path.join('a', 'b'), 'w')") == ["Cell 1 writes files on this PC"]
    assert effects("import os\nopen(os.path.join('a', 'b')).read()") == []
    assert effects("import pandas as pd\npd.DataFrame().to_csv('out.csv')") == ["Cell 1 writes files on this PC"]
    assert effects("import pandas as pd\ns = pd.DataFrame().to_csv(index=False)") == []
    out = validate.check([code('key = "sk-proj-abcdefghijklmnopqrstuvwx"')], None)
    assert [f["severity"] for f in findings(out, "secret")] == ["error"] and "abcdefghijklmnop" not in json.dumps(out)


# ---- the clean run

class LocalTables:
    def __init__(self, root: Path):
        self.root = root

    def versions(self):
        return {f"{d.parent.name}.{d.name}": DeltaTable(str(d)).version()
                for d in sorted(self.root.glob("*/*")) if (d / "_delta_log").exists()}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for name in ("DeltaTable", "FileStore", "publish", "_attached", "write_guard"):
        monkeypatch.setattr(lakehouse, name, getattr(lakehouse, name))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    lh = tmp_path / "lake"
    write_deltalake(str(lh / "Tables" / "dbo" / "orders"), pa.table({"id": [1, 2, 3], "amount": [10.0, 20.0, 5.0]}))
    s = Service(client_id="x", root=tmp_path / "app")
    s._auth = type("Auth", (), {"storage_token": lambda self: "tok"})()
    s._schema_cache = {"lh1": True}
    monkeypatch.setattr(s, "_proof_location", lambda ref: (str(lh), lambda: {}))
    monkeypatch.setattr(s, "_proof_tables", lambda ref: LocalTables(lh / "Tables"))
    monkeypatch.setattr(s, "_proof_delete_folder", lambda ref, rel: shutil.rmtree(lh / rel, ignore_errors=True))
    s.lh = lh
    return s


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def refused(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return json.dumps(out["error"])


def done(s, vid, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = call(s, "notebook_validate_status", id=vid)
        if st["state"] != "running":
            return st
        time.sleep(0.2)
    raise AssertionError("the validation didn't finish")


WRITES = [code("from fabric_desktop import lakehouse\norders = lakehouse.read_table('dbo.orders')"),
          code("big = orders.filter(orders['amount'] > 6)\nlakehouse.write_table(big, 'dbo.big_orders')"),
          code("print(lakehouse.read_table('dbo.big_orders').height)")]


def test_a_clean_run_writes_to_a_sandbox_that_it_deletes_and_leaves_the_tables_alone(svc):
    before = DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version()
    st = done(svc, call(svc, "notebook_validate_start", name="Orders", cells=WRITES, lakehouse_ref=LH, runs_on="pc")["id"])
    assert st["state"] == "done" and st["run"]["status"] == "ok", st
    assert st["verdict"] == "ready" and st["counts"]["error"] == 0
    assert [c["status"] for c in st["cells"]] == ["ok", "ok", "ok"]
    assert st["run"]["sandbox_tables"] == ["dbo.big_orders"] and st["sandbox"].startswith("Files/_fabric_desktop/proofs/validate-")
    assert not (svc.lh / "Tables" / "dbo" / "big_orders").exists()  # never the lakehouse's own tables
    assert not (svc.lh / "Files" / "_fabric_desktop" / "proofs" / st["id"]).exists()  # the sandbox is gone
    assert DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version() == before
    # Kept on this PC, so it outlives the engine.
    assert call(svc, "notebook_validate_list", name="Orders")[0]["verdict"] == "ready"
    assert json.loads((svc.root / "validations.json").read_text())[-1]["id"] == st["id"]


def test_hidden_state_fails_the_clean_run_and_leaves_the_persons_kernel_alone(svc):
    svc.kernel.run("leftover = 41")  # the person's kernel has it from an earlier run
    cells = [code("answer = leftover + 1"), code("leftover = 41")]
    st = done(svc, call(svc, "notebook_validate_start", name="Hidden", cells=cells, runs_on="pc")["id"])
    assert st["verdict"] == "not_ready" and st["run"]["failed_cell"] == 1
    [f] = [f for f in st["findings"] if f["severity"] == "error"]
    assert f["code"] == "defined_later" and f["found_by"] == ["code", "run"]  # one finding, found both ways
    assert st["summary"].startswith("The clean run stopped at cell 1")
    assert any(v["name"] == "leftover" for v in svc.kernel.variables())


def test_a_failing_cell_is_reported_with_its_error_and_secrets_removed(svc):
    cells = [code("raise ValueError('bad rows password=hunter22')")]
    st = done(svc, call(svc, "notebook_validate_start", name="Fails", cells=cells)["id"])
    [f] = [f for f in st["findings"] if f["code"] == "run_failed"]
    assert f["cell"] == 1 and f["text"].startswith("ValueError: bad rows") and "hunter22" not in json.dumps(st)


def test_the_clean_run_asks_first_and_refuses_what_it_cannot_contain(svc):
    web = [code("import requests\nrequests.get('https://example.com')")]
    assert "Confirm to continue" in refused(svc, "notebook_validate_start", name="Web", cells=web, lakehouse_ref=LH)
    direct = [code("from deltalake import write_deltalake\nwrite_deltalake('abfss://x', t)")]
    assert "can't contain cell 1" in refused(svc, "notebook_validate_start", name="Direct", cells=direct, lakehouse_ref=LH)


def test_a_spark_notebook_is_told_it_gets_code_checks_only(svc):
    cells = [code("df = spark.read.table('dbo.orders')\ndf.write.saveAsTable('dbo.copy')")]
    assert "code checks only" in refused(svc, "notebook_validate_start", name="Spark", cells=cells, lakehouse_ref=LH)


def test_one_validation_at_a_time_and_cancel_ends_it(svc):
    slow = [code("import time\ntime.sleep(60)")]
    vid = call(svc, "notebook_validate_start", name="Slow", cells=slow)["id"]
    assert "already running" in refused(svc, "notebook_validate_start", name="Other", cells=[code("x = 1")])
    deadline = time.monotonic() + 30
    while call(svc, "notebook_validate_status", id=vid)["cell"] is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert call(svc, "notebook_validate_cancel", id=vid) == {"cancelled": True}
    st = done(svc, vid)
    assert st["state"] == "cancelled" and st["verdict"] is None


def test_save_report_writes_a_new_file_in_downloads_each_time(svc, tmp_path):
    st = done(svc, call(svc, "notebook_validate_start", name="Report me", cells=[code("x = 1")])["id"])
    a = call(svc, "notebook_validate_save", id=st["id"])
    b = call(svc, "notebook_validate_save", id=st["id"], format="json")
    c = call(svc, "notebook_validate_save", id=st["id"])
    assert len({a["path"], b["path"], c["path"]}) == 3 and Path(a["path"]).parent == tmp_path / "home" / "Downloads"
    text = Path(a["path"]).read_text(encoding="utf-8")
    assert text.startswith("# Validate: Report me") and "**Ready**" in text and "| 1 | ok |" in text
    assert json.loads(Path(b["path"]).read_text(encoding="utf-8"))["id"] == st["id"]


def test_save_report_never_replaces_a_file_with_its_name(svc):
    st = done(svc, call(svc, "notebook_validate_start", name="Keep mine", cells=[code("x = 1")])["id"])
    first = Path(call(svc, "notebook_validate_save", id=st["id"])["path"])
    first.write_text("my notes", encoding="utf-8")  # hand-edited after the first export
    second = Path(call(svc, "notebook_validate_save", id=st["id"])["path"])
    assert second == first.with_name(f"{first.stem}-2.md") and first.read_text(encoding="utf-8") == "my notes"


def test_save_report_is_created_exclusively_even_if_the_name_was_free_a_moment_ago(svc, monkeypatch):
    # Another save makes the file between the free-name check and the write: Path.exists says
    # every name is free, and the file already there must still not be replaced.
    st = done(svc, call(svc, "notebook_validate_start", name="Race", cells=[code("x = 1")])["id"])
    first = Path(call(svc, "notebook_validate_save", id=st["id"])["path"])
    first.write_text("my notes", encoding="utf-8")
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: False)
    second = Path(call(svc, "notebook_validate_save", id=st["id"])["path"])
    assert first.read_text(encoding="utf-8") == "my notes"
    assert second == first.with_name(f"{first.stem}-2.md")



def test_save_report_gives_up_after_999_taken_names(svc, monkeypatch):
    st = done(svc, call(svc, "notebook_validate_start", name="Full", cells=[code("x = 1")])["id"])
    tried, real_open = [], Path.open
    def taken(self, mode="r", *a, **k):
        if mode != "x":
            return real_open(self, mode, *a, **k)
        tried.append(self.name)
        raise FileExistsError(self)
    monkeypatch.setattr(Path, "open", taken)
    assert "free file name" in refused(svc, "notebook_validate_save", id=st["id"])
    assert len(tried) == 999

def test_checks_and_status_are_not_audited_but_starting_and_saving_are():
    from fabric_desktop import audit, server
    assert {"notebook_validate_check", "notebook_validate_status", "notebook_validate_list"} <= audit.READ_ONLY
    assert {"notebook_validate_start", "notebook_validate_cancel", "notebook_validate_save"} <= server.METHODS - audit.READ_ONLY
