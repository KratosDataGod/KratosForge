"""Mutation-kill tests for replay.py: bundle versions, the code and input notes, id scrubbing, the
bundle file checks and replay verdicts. Pure functions and zips in tmp_path, no kernel."""

from __future__ import annotations

import json
import zipfile

import pytest

from fabric_desktop import replay


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


def zipped(path, bundle, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("bundle.json", json.dumps(bundle))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def minimal(**kw):
    return {"format": replay.FORMAT, "version": 1, "cells": [], "failed_cell": 1,
            "inputs": {"tables": [], "files": []}, "samples": []} | kw


# ---- versions

def test_bundles_are_version_one_and_version_one_opens(tmp_path):
    assert replay.VERSION == 1
    b = replay.build("nb", [{"type": "code", "source": "1/0", "count": 1}], 0, {})
    assert b["version"] == 1
    assert replay.read(zipped(tmp_path / "b.zip", minimal()))["version"] == 1


def test_a_version_two_bundle_asks_for_an_update(tmp_path):
    with pytest.raises(ValueError, match="newer KratosForge"):
        replay.read(zipped(tmp_path / "b.zip", minimal(version=2)))


def test_a_sample_holds_at_most_a_thousand_rows(tmp_path):
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq
    assert replay.SAMPLE_ROWS == 1000
    out = replay.write(tmp_path / "b.zip", minimal(), {"dbo.big": pa.table({"id": list(range(1001))}),
                                                      "dbo.small": pa.table({"id": [1, 2]})})
    assert out["samples"] == [{"table": "dbo.big", "file": "samples/1.parquet", "rows": 1000},
                              {"table": "dbo.small", "file": "samples/2.parquet", "rows": 2}]
    with zipfile.ZipFile(tmp_path / "b.zip") as z:
        assert pq.read_table(io.BytesIO(z.read("samples/1.parquet"))).num_rows == 1000


# ---- the code and its notes

def cell(source="x = 1", count=1, **kw):
    return {"type": "code", "source": source, "count": count} | kw


def test_failed_must_be_a_code_cell_in_range():
    cells = [cell(), {"type": "markdown", "source": "# h"}]
    for failed in (-1, 2, 1):
        with pytest.raises(ValueError, match="Pick the code cell that failed"):
            replay.code(cells, failed)
    assert len(replay.code(cells, 0)[0]) == 1


def test_code_carries_tags_and_frozen_and_notes_cells_that_had_not_run():
    cells = [cell("p = 1", 1, meta={"tags": ["parameters"]}), cell("y = 2", None),
             cell("z = 3", None, meta={"frozen": True}), {"type": "markdown", "source": "t"}, cell("   ", None),
             cell("1/0", 2)]
    out, notes = replay.code(cells, 5)
    assert out[0] == {"type": "code", "source": "p = 1", "parameters": True}
    assert out[1] == {"type": "code", "source": "y = 2"}
    assert out[2] == {"type": "code", "source": "z = 3", "frozen": True}
    assert out[3] == {"type": "markdown", "source": "t"}
    assert notes == ["Cell 2 hadn't run in this session when cell 6 failed, so a replay runs code the failure didn't."]


@pytest.mark.parametrize("counts,noted", [((1, 2, 3), False), ((1, 3, 2), True), ((2, 2), True), ((1,), False)])
def test_cells_run_out_of_order_are_noted(counts, noted):
    _, notes = replay.code([cell(f"x{i} = 1", c) for i, c in enumerate(counts)], len(counts) - 1)
    assert any("different order" in n for n in notes) is noted
    assert len(notes) == int(noted)


def test_a_secret_in_a_cell_is_removed_and_noted():
    key = "AccountKey=" + "A" * 40 + "=="
    out, notes = replay.code([cell("x = 1", 1), cell(f"conn = '{key}'", 2)], 1)
    assert key not in out[1]["source"]
    assert notes == ["Cell 2 looked like it held a secret, which isn't in the bundle; a replay may fail there."]


def test_error_is_the_first_error_output_with_the_home_folder_removed(tmp_path):
    home = str(tmp_path / "home")
    outs = [{"type": "stream", "text": "hi"},
            {"type": "error", "ename": "ZeroDivisionError", "evalue": f"in {home}/a.py", "traceback": f"{home}/b"},
            {"type": "error", "ename": "Other", "evalue": "second"}]
    assert replay.error([{"outputs": outs}], 0) == {"ename": "ZeroDivisionError", "evalue": "in ~/a.py",
                                                     "traceback": "~/b"}
    assert replay.error([{"outputs": [{"type": "stream"}]}], 0) is None
    assert replay.error([{"outputs": [{"type": "error"}]}], 0) == {"ename": "", "evalue": "", "traceback": ""}


def test_a_one_character_home_is_left_in_text(monkeypatch):
    monkeypatch.setattr(replay.Path, "home", classmethod(lambda cls: replay.Path("/")))
    assert replay._private("a/b") == "a/b"


# ---- inputs

def test_inputs_sort_hash_and_mark_created_tables(tmp_path):
    same, changed = tmp_path / "same.csv", tmp_path / "changed.csv"
    same.write_bytes(b"a,b\n")
    changed.write_bytes(b"x\n")
    st = same.stat()
    reads = {"tables": {"1": {"table": "b", "lakehouse": "L"}, "2": {"table": "A", "lakehouse": "L"},
                        "3": {"table": "new", "lakehouse": "K", "first": "write", "version": None},
                        "4": {"table": "kept", "lakehouse": "K", "first": "write", "version": 3},
                        "5": {"table": "r", "lakehouse": "K", "first": "read", "version": None}},
             "files": {"x": {"path": "Files/z", "lakehouse": "L", "local": str(same), "bytes": st.st_size,
                             "mtime_ns": st.st_mtime_ns},
                       "y": {"path": "Files/a", "lakehouse": "L", "local": str(changed), "bytes": 99, "mtime_ns": 1},
                       "z": {"path": "Files/gone", "lakehouse": "K", "local": str(tmp_path / "nope")}}}
    found, notes = replay.inputs(reads)
    assert [(t["lakehouse"], t["table"]) for t in found["tables"]] == [
        ("K", "kept"), ("K", "new"), ("K", "r"), ("L", "A"), ("L", "b")]
    assert [t.get("created") for t in found["tables"]] == [None, True, None, None, None]
    assert [f["path"] for f in found["files"]] == ["Files/gone", "Files/a", "Files/z"]
    z = found["files"][2]
    assert "local" not in z and "mtime_ns" not in z
    assert z["sha256"] == replay.hashlib.sha256(b"a,b\n").hexdigest()
    assert found["files"][0]["sha256"] is None and found["files"][1]["sha256"] is None
    assert notes == [f"{p} changed on this PC after a cell read it, so its SHA-256 isn't recorded."
                     for p in ("Files/gone", "Files/a")]


def test_a_file_with_the_same_size_but_another_mtime_is_not_hashed(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"abc")
    st = f.stat()
    found, notes = replay.inputs({"files": {"f": {"path": "p", "local": str(f), "bytes": 3,
                                                  "mtime_ns": st.st_mtime_ns + 1}}})
    assert found["files"][0]["sha256"] is None and len(notes) == 1


def test_sha256_reads_files_larger_than_one_chunk(tmp_path):
    f = tmp_path / "big"
    data = bytes(range(256)) * 8193
    f.write_bytes(data)
    assert replay._sha256(f) == replay.hashlib.sha256(data).hexdigest()


# ---- ids

G1, G2 = "0f6e2a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b", "6A7B8C9D-0E1F-4A2B-9C3D-4E5F6A7B8C9D"


def test_scrub_ids_replaces_known_ids_addresses_and_guids():
    text = (f"abfss://Sales@onelake.dfs.fabric.microsoft.com/LH.Lakehouse/Tables/t "
            f"https://onelake.blob.fabric.microsoft.com/ws/lh/Files/x {G2} myws-1")
    assert replay.scrub_ids(text, ("myws-1",)) == (
        "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>/Tables/t "
        "https://onelake.blob.fabric.microsoft.com/<workspace>/<lakehouse>/Files/x <id> <id>")
    assert replay.scrub_ids("ABFSS://w@ONELAKE.FABRIC.MICROSOFT.COM/l") == \
        "ABFSS://<workspace>@ONELAKE.FABRIC.MICROSOFT.COM/<lakehouse>"
    assert replay.scrub_ids(f"x{G1}y") == f"x{G1}y"  # not a whole word


def test_scrub_strings_reaches_keys_and_lists_and_leaves_other_values():
    v = {G1: [G1, {"k": G1}], "n": 3, "none": None}
    assert replay._scrub_strings(v, ()) == {"<id>": ["<id>", {"k": "<id>"}], "n": 3, "none": None}


def bundle_with_ids(source="x = 1"):
    return {"lakehouse": {"name": "LH", "workspace_id": "wsid-long", "lakehouse_id": "lh", "workspace_name": "Sales"},
            "inputs": {"tables": [{"table": "t", "workspace_id": "tblws", "lakehouse_id": "tbllh", "version": 1}],
                       "files": [{"path": "Files/a", "workspace_id": "", "lakehouse_id": None}]},
            "cells": [{"type": "code", "source": source}], "error": {"evalue": "in wsid-long and tbllh"},
            "limitations": []}


def test_strip_ids_removes_ids_and_names_and_notes_changed_code():
    b = bundle_with_ids()
    out = replay.strip_ids(b)
    assert out["lakehouse"] == {"name": "LH"}
    assert out["ids_included"] is False
    assert out["inputs"]["tables"] == [{"table": "t", "version": 1}]
    assert out["inputs"]["files"] == [{"path": "Files/a"}]
    assert out["error"]["evalue"] == "in <id> and <id>"
    assert out["limitations"] == []
    assert b["lakehouse"]["workspace_id"] == "wsid-long"  # the bundle passed in is unchanged
    changed = replay.strip_ids(bundle_with_ids("path = 'tblws'"))
    assert changed["cells"][0]["source"] == "path = '<id>'"
    assert len(changed["limitations"]) == 1 and "Put your own back" in changed["limitations"][0]


def test_strip_ids_replaces_the_longest_id_first():
    b = bundle_with_ids("a = 'wsid-long'")
    b["lakehouse"]["lakehouse_id"] = "wsid"
    assert replay.strip_ids(b)["cells"][0]["source"] == "a = '<id>'"


def test_strip_ids_with_no_limitations_key_adds_one():
    b = bundle_with_ids("a = 'lh'")
    del b["limitations"]
    assert len(replay.strip_ids(b)["limitations"]) == 1


# ---- the bundle file

def test_read_rejects_files_that_are_not_bundles(tmp_path):
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with zipfile.ZipFile(tmp_path / "empty.zip", "w") as z:
        z.writestr("other.txt", "x")
    with zipfile.ZipFile(tmp_path / "badjson.zip", "w") as z:
        z.writestr("bundle.json", "{not json")
    for name in ("junk.zip", "empty.zip", "badjson.zip"):
        with pytest.raises(ValueError, match=r"^" + name.replace(".", r"\.") + r" isn't a replay bundle\.$"):
            replay.read(tmp_path / name)
    for body in ([1], minimal(format="other")):
        with pytest.raises(ValueError, match=r"isn't a replay bundle\.$"):
            replay.read(zipped(tmp_path / "x.zip", body))


def test_read_refuses_a_bundle_json_over_the_limit_before_reading(tmp_path, monkeypatch):
    assert replay.MAX_BUNDLE_JSON == 64 * 1024 * 1024
    path = zipped(tmp_path / "b.zip", minimal())
    size = zipfile.ZipFile(path).getinfo("bundle.json").file_size
    monkeypatch.setattr(replay, "MAX_BUNDLE_JSON", size)
    assert replay.read(path)["format"] == replay.FORMAT
    monkeypatch.setattr(replay, "MAX_BUNDLE_JSON", size - 1)
    with pytest.raises(ValueError, match=r"^b\.zip is too large to be a replay bundle\.$"):
        replay.read(path)


def test_read_without_a_version_opens(tmp_path):
    b = minimal()
    del b["version"]
    assert replay.read(zipped(tmp_path / "b.zip", b))["format"] == replay.FORMAT


@pytest.mark.parametrize("table", ["dbo.sales", "sales", "LH/dbo.sales"])
def test_good_sample_names_open(tmp_path, table):
    b = minimal(inputs={"tables": [{"table": "dbo.sales"}], "files": []},
                samples=[{"table": table, "file": "samples/1.parquet"}])
    assert replay.read(zipped(tmp_path / "b.zip", b, {"samples/1.parquet": b"x"}))["samples"][0]["table"] == table


@pytest.mark.parametrize("tables,samples,members,needle", [
    ([{"table": "../x"}], [], {}, "isn't a replay bundle: "),
    ([{"table": "a/b.t"}], [], {}, "isn't a replay bundle: "),
    ([{"table": "dbo.../x"}], [], {}, "isn't a replay bundle: "),
    ([], [{"table": "LH/../t", "file": "samples/1.parquet"}], {"samples/1.parquet": b"x"}, "isn't a replay bundle: "),
    ([], [{"table": "t", "file": "samples/1.parquet"}], {}, "isn't in the bundle's samples folder"),
    ([], [{"table": "t", "file": "../1.parquet"}], {"../1.parquet": b"x"}, "isn't in the bundle's samples folder"),
    ([], [{"table": "t", "file": "samples/1.parquet\n"}], {"samples/1.parquet\n": b"x"}, "samples folder"),
    ([], [{"table": "t", "file": "xsamples/1.parquet"}], {"xsamples/1.parquet": b"x"}, "samples folder"),
    ([], [{"table": "t", "file": "samples/a.parquet"}], {"samples/a.parquet": b"x"}, "samples folder"),
    ([], [{"table": "t"}], {}, "isn't a replay bundle: "),
    ([{"name": "t"}], [], {}, "isn't a replay bundle: "),
])
def test_bad_names_are_refused(tmp_path, tables, samples, members, needle):
    b = minimal(inputs={"tables": tables, "files": []}, samples=samples)
    with pytest.raises(ValueError, match=needle):
        replay.read(zipped(tmp_path / "b.zip", b, members))


def test_a_sample_file_over_the_limit_is_refused(tmp_path, monkeypatch):
    assert replay.MAX_SAMPLE_FILE == 256 * 1024 * 1024
    b = minimal(samples=[{"table": "t", "file": "samples/1.parquet"}])
    path = zipped(tmp_path / "b.zip", b, {"samples/1.parquet": b"1234"})
    monkeypatch.setattr(replay, "MAX_SAMPLE_FILE", 4)
    assert replay.read(path)
    monkeypatch.setattr(replay, "MAX_SAMPLE_FILE", 3)
    with pytest.raises(ValueError, match=r"isn't a replay bundle: the sample file samples/1\.parquet is too large"):
        replay.read(path)


def test_write_returns_its_size_and_x_mode_keeps_an_existing_file(tmp_path):
    path = tmp_path / "sub" / "b.zip"
    out = replay.write(path, minimal(samples=[{"stale": 1}]))
    assert out == {"path": str(path), "bytes": path.stat().st_size, "samples": []}
    assert replay.read(path)["samples"] == []
    with pytest.raises(FileExistsError):
        replay.write(path, minimal(), mode="x")
    assert path.exists()


def test_a_failed_exclusive_write_leaves_no_file(tmp_path):
    class Bad:
        def slice(self, *a):
            raise RuntimeError("boom")
    path = tmp_path / "b.zip"
    with pytest.raises(RuntimeError):
        replay.write(path, minimal(), {"t": Bad()}, mode="x")
    assert not path.exists()
    with pytest.raises(RuntimeError):
        replay.write(path, minimal(), {"t": Bad()}, mode="w")
    assert path.exists()  # mode "w" leaves what it wrote; only "x" created the file itself


# ---- opening and replaying

def test_sample_tables_and_their_keys():
    b = {"inputs": {"tables": [{"table": "dbo.a", "lakehouse": "LH", "version": 0}, {"table": "b", "version": 2},
                               {"table": "c", "version": None}]}}
    assert [t["table"] for t in replay.sample_tables(b)] == ["dbo.a", "b"]
    assert replay.samples(b, lambda t: t["version"]) == {"LH/dbo.a": 0, "b": 2}


def test_notebook_cells_keep_tags_and_frozen():
    b = {"cells": [{"type": "code", "source": "p", "parameters": True}, {"type": "code", "source": "f", "frozen": True},
                   {"type": "markdown", "source": "m"}]}
    assert replay.notebook_cells(b) == [
        {"type": "code", "source": "p", "meta": {"tags": ["parameters"]}},
        {"type": "code", "source": "f", "meta": {"frozen": True}},
        {"type": "markdown", "source": "m", "meta": {}}]


def test_tables_to_pin():
    tables = [{"table": "made", "created": True, "version": 1, "lakehouse_id": "other"},
              {"table": "away", "lakehouse_id": "other", "lakehouse": "LH2", "version": 1},
              {"table": "away2", "lakehouse_id": "other", "version": 1},
              {"table": "here", "lakehouse_id": "lh", "version": 1},
              {"table": "noid", "version": 4},
              {"table": "latest", "lakehouse_id": "lh", "version": None}]
    pin, notes = replay.tables_to_pin({"inputs": {"tables": tables}}, "lh")
    assert [t["table"] for t in pin] == ["here", "noid"]
    assert notes == ["away was read from another lakehouse (LH2), so this replay can't read it.",
                     "away2 was read from another lakehouse (not named), so this replay can't read it."]
    pin, notes = replay.tables_to_pin({"inputs": {"tables": tables}, "ids_included": False}, "lh")
    assert [t["table"] for t in pin] == ["away", "away2", "here", "noid"] and notes == []


def err(ename, evalue):
    return [{"type": "error", "ename": ename, "evalue": evalue}]


@pytest.mark.parametrize("stopped,status,outputs,verdict,text", [
    (2, "ok", [], "stopped", "The replay stopped at cell 2 before reaching cell 3."),
    (3, "ok", [], "not_reproduced", "Cell 3 ran without an error this time. Not reproduced."),
    (4, "ok", [], "not_reproduced", "Cell 3 ran without an error this time. Not reproduced."),
    (2, "error", err("KeyError", "k"), "failed_earlier", "Failed earlier, at cell 2: KeyError: k"),
    (3, "error", err("ZeroDivisionError", "division by zero"), "reproduced",
     "Failed the same way as in the bundle. Reproduced."),
    (3, "error", err("ZeroDivisionError", "other"), "same_kind",
     "Failed with the same kind of error, but a different message: ZeroDivisionError: other"),
    (3, "error", err("KeyError", "division by zero"), "different",
     "Failed differently: KeyError: division by zero (the bundle has ZeroDivisionError)"),
])
def test_outcome(stopped, status, outputs, verdict, text):
    b = {"failed_cell": 3, "error": {"ename": "ZeroDivisionError", "evalue": "division by zero"}}
    got = replay.outcome(b, stopped, status, outputs)
    assert got["verdict"] == verdict and got["text"] == text
    assert ("error" in got) is (status == "error")


def test_outcome_compares_scrubbed_messages_only_for_a_bundle_without_ids():
    b = {"failed_cell": 1, "error": {"ename": "E", "evalue": "no table in <id>"}, "ids_included": False}
    assert replay.outcome(b, 1, "error", err("E", f"no table in {G1}"))["verdict"] == "reproduced"
    assert replay.outcome(b | {"ids_included": True}, 1, "error", err("E", f"no table in {G1}"))["verdict"] == "same_kind"
    assert replay.outcome({k: v for k, v in b.items() if k != "ids_included"}, 1, "error",
                          err("E", f"no table in {G1}"))["verdict"] == "same_kind"


def test_input_differences():
    b = {"inputs": {"files": [{"path": "a", "sha256": "1"}, {"path": "b", "sha256": "2"}, {"path": "c"},
                              {"path": "d", "sha256": None}]}}
    log = {"files": {"d": {}, "a": {"sha256": "1"}, "b": {"sha256": "x"}, "c": {"sha256": "3"}, "new": {}}}
    assert replay.input_differences(b, log, {"t2": "latest", "t1": "sample"}) == [
        "t1 was read from the bundle's sample, not at the bundle's version: not the same input.",
        "t2 was read at its latest version, not at the bundle's version: not the same input.",
        "b is different now from the file the bundle's run read (SHA-256 differs).",
        "c has no SHA-256 in the bundle, so it can't be checked.",
        "d has no SHA-256 in the bundle, so it can't be checked.",
        "new wasn't read in the bundle's run."]
    assert replay.input_differences(b, {}, {}) == []


def test_build_fills_the_bundle():
    cells = [cell("import json\nx = 1", 1), cell("1/0", 2, outputs=err("ZeroDivisionError", "division by zero"))]
    b = replay.build("nb", cells, 1, {}, {"name": "LH", "workspace_id": "w", "extra": "no"}, kernel={"python": "3"})
    assert b["format"] == replay.FORMAT and b["notebook"] == "nb" and b["failed_cell"] == 2
    assert b["ids_included"] is True and b["samples"] == []
    assert b["lakehouse"] == {"name": "LH", "workspace_id": "w", "workspace_name": "", "lakehouse_id": ""}
    assert b["error"]["ename"] == "ZeroDivisionError"
    assert "machine" not in b["environment"] and b["environment"]["kernel"] == {"python": "3"}
    assert b["limitations"][-1].startswith("Inputs are what the cells read through lakehouse")
    assert "kernel" not in replay.build("nb", cells, 1, {})["environment"]
    assert replay.build("nb", cells, 1, {})["lakehouse"] == {k: "" for k in
                                                              ("name", "workspace_id", "workspace_name", "lakehouse_id")}
