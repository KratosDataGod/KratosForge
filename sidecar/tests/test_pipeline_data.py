"""Lookup and Get Metadata in local pipeline runs (pipelines plan P1): they read the lakehouse their
dataset names, at the run's pinned table versions, and write nothing."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from tests.test_pipeline_notebooks import LH, act, by_name, code, finish, notebook, pipeline, svc  # noqa: F401

LS = {"name": "LH_Sales", "properties": {"type": "Lakehouse", "typeProperties": {
    "workspaceId": "ws1", "artifactId": "lh1", "rootFolder": "Tables"}}}


def table_ds(table, schema="dbo", ls=LS):
    return {"annotations": [], "linkedService": ls, "type": "LakehouseTable", "schema": [],
            "typeProperties": {"schema": schema, "table": table}}


def file_ds(folder, name=None, kind="Binary"):
    loc = {"type": "LakehouseLocation", "folderPath": folder} | ({"fileName": name} if name else {})
    return {"linkedService": LS, "type": kind, "typeProperties": {"location": loc}}


def lookup(name, ds, depends=(), **tp):
    return act(name, "Lookup", depends, source={"type": "LakehouseTableSource"}, datasetSettings=ds, **tp)


def metadata(name, ds, fields, depends=()):
    return act(name, "GetMetadata", depends, datasetSettings=ds, fieldList=fields)


def start(svc, text, **kw):
    started = svc.pipeline_run_start(text=text, read_lakehouses=True, **kw)
    assert started["ok"] is True, started
    return finish(svc, started["run_id"])


def files(svc, monkeypatch):
    """Get Metadata's listing reads the test lakehouse folder instead of OneLake."""
    def listing(ref, rel):
        folder = svc.lh / rel
        if not folder.is_dir():
            raise FileNotFoundError(rel)
        return [{"name": p.name, "path": f"{rel}/{p.name}", "is_dir": p.is_dir(),
                 "size": 0 if p.is_dir() else p.stat().st_size,
                 "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
                for p in sorted(folder.iterdir())]

    def info(ref, rel):
        p = svc.lh / rel
        if not p.exists():
            return None
        return {"is_dir": p.is_dir(), "size": 0 if p.is_dir() else p.stat().st_size, "modified": "2026-10-22T00:00:00Z"}

    monkeypatch.setattr(svc, "_pipeline_list_files", listing)
    monkeypatch.setattr(svc, "_pipeline_file_info", info)


def test_lookup_returns_the_first_row_or_all_rows(svc):
    r = start(svc, pipeline(lookup("First", table_ds("orders")),
                            lookup("All", table_ds("orders"), firstRowOnly=False)))
    acts = by_name(r)
    assert r["state"] == "succeeded", r
    assert acts["First"]["output"] == {"firstRow": {"id": 1, "region": "EMEA", "amount": 10.0}}
    assert acts["All"]["output"]["count"] == 3 and [row["id"] for row in acts["All"]["output"]["value"]] == [1, 2, 3]


def test_lookup_reads_the_pinned_version(svc, monkeypatch):
    # The run pins each table at the version it had when the run first used the lakehouse.
    write_deltalake(str(svc.lh / "Tables" / "dbo" / "orders"), pa.table({"id": [9], "region": ["X"], "amount": [1.0]}),
                    mode="overwrite")
    monkeypatch.setattr(svc, "_proof_tables", lambda ref: type("T", (), {"versions": lambda self: {"dbo.orders": 0}})())
    r = start(svc, pipeline(lookup("First", table_ds("orders"))))
    assert by_name(r)["First"]["output"]["firstRow"]["id"] == 1


def test_a_foreach_over_a_lookup_runs_notebook_steps(svc):
    # KF-DE-021's acceptance: a ForEach over a Lookup result runs Notebook activities on this PC.
    write_deltalake(str(svc.lh / "Tables" / "dbo" / "regions"), pa.table({"region": ["EMEA", "APAC"]}))
    svc.notebooks.save("Count region", [
        code("region = 'ALL'", tags=["parameters"]),
        code("from fabric_desktop import lakehouse\no = lakehouse.read_table('dbo.orders')\n"
             "notebookutils.notebook.exit(f\"{region}={o.filter(o['region'] == region).height}\")")], LH)
    text = pipeline(
        lookup("Regions", table_ds("regions"), firstRowOnly=False),
        act("Each", "ForEach", ["Regions"], items={"value": "@activity('Regions').output.value", "type": "Expression"},
            isSequential=True,
            activities=[notebook("Count", "nb", parameters={"region": {"value": "@item().region", "type": "string"}}),
                        act("Keep", "AppendVariable", ["Count"], variableName="seen",
                            value="@activity('Count').output.result.exitValue")]),
        variables={"seen": {"type": "Array"}})
    r = start(svc, text, notebooks={"nb": "Count region"})
    assert r["state"] == "succeeded", r
    assert r["variables"]["seen"] == ["EMEA=2", "APAC=1"]


def test_lookup_reads_what_a_notebook_step_wrote_in_the_run(svc):
    from tests.test_pipeline_notebooks import FILTER
    svc.notebooks.save("Filter orders", FILTER, LH)
    text = pipeline(notebook("Filter", "nb", parameters={"region": {"value": "EMEA", "type": "string"}}),
                    lookup("Picked", table_ds("picked"), ["Filter"], firstRowOnly=False))
    r = start(svc, text, notebooks={"nb": "Filter orders"})
    assert r["state"] == "succeeded", r
    assert by_name(r)["Picked"]["output"]["count"] == 2
    assert not (svc.lh / "Tables" / "dbo" / "picked").exists()


def test_lookup_table_name_can_come_from_a_parameter(svc):
    ds = table_ds({"value": "@pipeline().parameters.table", "type": "Expression"})
    r = start(svc, pipeline(lookup("First", ds), parameters={"table": {"type": "string", "defaultValue": "orders"}}))
    assert by_name(r)["First"]["output"]["firstRow"]["id"] == 1


def test_lookup_returns_at_most_5000_rows(svc):
    write_deltalake(str(svc.lh / "Tables" / "dbo" / "big"), pa.table({"n": list(range(6000))}))
    r = start(svc, pipeline(lookup("All", table_ds("big"), firstRowOnly=False)))
    assert by_name(r)["All"]["output"]["count"] == 5000


def test_lookup_fails_when_the_table_isnt_there(svc):
    r = start(svc, pipeline(lookup("Missing", table_ds("nope"))))
    assert r["state"] == "failed" and by_name(r)["Missing"]["error"]["errorCode"] == "LocalDataNotFound"


def test_get_metadata_on_a_table(svc):
    r = start(svc, pipeline(metadata("Orders", table_ds("orders"), ["exists", "itemName", "structure", "columnCount"]),
                            metadata("Nope", table_ds("nope"), ["exists"])))
    acts = by_name(r)
    assert acts["Orders"]["output"] == {"exists": True, "itemName": "orders", "columnCount": 3, "structure": [
        {"name": "id", "type": "Int64"}, {"name": "region", "type": "String"}, {"name": "amount", "type": "Double"}]}
    assert acts["Nope"]["output"] == {"exists": False}


def test_get_metadata_on_files_and_folders(svc, monkeypatch):
    files(svc, monkeypatch)
    (svc.lh / "Files" / "raw" / "2026").mkdir(parents=True)
    (svc.lh / "Files" / "raw" / "a.csv").write_bytes(b"id\n1\n")
    text = pipeline(metadata("Folder", file_ds("raw"), ["exists", "itemType", "childItems"]),
                    metadata("File", file_ds("raw", "a.csv", "DelimitedText"), ["itemName", "itemType", "size"]),
                    metadata("Gone", file_ds("landing"), ["exists"]))
    r = start(svc, text)
    acts = by_name(r)
    assert r["state"] == "succeeded", r
    assert acts["Folder"]["output"] == {"exists": True, "itemType": "Folder", "childItems": [
        {"name": "2026", "type": "Folder"}, {"name": "a.csv", "type": "File"}]}
    assert acts["File"]["output"] == {"itemName": "a.csv", "itemType": "File", "size": 5}
    assert acts["Gone"]["output"] == {"exists": False}


def test_what_cant_be_read_here_is_refused_before_the_run(svc):
    def codes(text, **kw):
        return [p["code"] for p in svc.pipeline_run_check(text=text, **kw)["problems"]]

    plain = pipeline(lookup("L", table_ds("orders")))
    # External tools don't turn lakehouse reads on, so Lookup still needs a mock there.
    assert codes(plain) == ["data_needs_engine"]
    assert codes(plain, read_lakehouses=True) == []
    query = pipeline(act("Q", "Lookup", source={"type": "LakehouseTableSource", "sqlReaderQuery": "select 1"},
                         datasetSettings=table_ds("orders")))
    assert codes(query, read_lakehouses=True) == ["data_query"]
    sql = {"name": "SQL", "properties": {"type": "AzureSqlDatabase", "typeProperties": {}}}
    assert codes(pipeline(lookup("S", table_ds("t", ls=sql))), read_lakehouses=True) == ["data_not_lakehouse"]
    assert codes(pipeline(metadata("M", table_ds("orders"), ["contentMD5"])), read_lakehouses=True) == ["data_field"]
    own = json.loads(json.dumps(LS))
    own["properties"]["typeProperties"]["workspaceId"] = "00000000-0000-0000-0000-000000000000"
    text = pipeline(lookup("O", table_ds("orders", ls=own)))
    assert codes(text, read_lakehouses=True) == ["data_workspace_unknown"]
    assert codes(text, read_lakehouses=True, workspace_id="ws1") == []


def test_the_pipelines_own_workspace_is_used_when_the_dataset_leaves_it_out(svc):
    own = json.loads(json.dumps(LS))
    del own["properties"]["typeProperties"]["workspaceId"]
    r = start(svc, pipeline(lookup("O", table_ds("orders", ls=own))), workspace_id="ws1")
    assert by_name(r)["O"]["output"]["firstRow"]["id"] == 1


def test_lookup_and_get_metadata_write_nothing(svc, monkeypatch):
    files(svc, monkeypatch)
    before = DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version()
    listing = sorted(str(p.relative_to(svc.lh)) for p in svc.lh.rglob("*"))
    start(svc, pipeline(lookup("L", table_ds("orders"), firstRowOnly=False),
                        metadata("M", file_ds(""), ["childItems"])))
    assert DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version() == before
    assert sorted(str(p.relative_to(svc.lh)) for p in svc.lh.rglob("*")) == listing


def test_file_info_reads_the_parent_folder_and_a_missing_folder_is_none(svc, monkeypatch):
    import requests
    from fabric_desktop.pipeline_service import PipelineRunMixin

    def listing(ref, rel):
        if rel == "Files/gone":
            raise requests.HTTPError(response=type("R", (), {"status_code": 404})())
        return [{"name": "a.csv", "is_dir": False, "size": 5, "modified": "2026-10-22T00:00:00Z"}]

    monkeypatch.setattr(svc, "_pipeline_list_files", listing)
    info = PipelineRunMixin._pipeline_file_info
    assert info(svc, LH, "Files/raw/a.csv")["size"] == 5
    assert info(svc, LH, "Files/raw/b.csv") is None and info(svc, LH, "Files/gone/x") is None
    assert info(svc, LH, "Files")["is_dir"] is True


def test_cli_reads_a_lakehouse_with_read_lakehouse(svc, tmp_path, capsys, monkeypatch):
    import pytest
    from fabric_desktop import cli
    monkeypatch.setattr(cli, "Service", lambda: svc)
    path = tmp_path / "p.json"
    path.write_text(pipeline(lookup("First", table_ds("orders"))), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(path), "--read-lakehouse", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert done.value.code == 0 and by_name(record)["First"]["output"]["firstRow"]["id"] == 1
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(path)])
    assert done.value.code == 2 and "refused: data_needs_engine" in capsys.readouterr().out


def test_a_read_that_fails_fails_the_activity_with_secrets_removed(svc, monkeypatch):
    def broken(ref):
        raise RuntimeError("401 for Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.secretpart.sig")
    monkeypatch.setattr(svc, "_proof_tables", broken)
    r = start(svc, pipeline(lookup("L", table_ds("orders"))))
    error = by_name(r)["L"]["error"]
    assert error["errorCode"] == "LocalDataReadFailed" and "secretpart" not in error["message"]


def test_lookup_and_get_metadata_use_onelakes_short_read_limits(svc, monkeypatch):
    """#1448: a OneLake read for a Lookup or Get Metadata gives up within about a minute when offline,
    with #1303's read_storage_options, not the upload limits that retry for up to 30 minutes."""
    import deltalake

    from fabric_desktop import onelake
    real, seen = deltalake.DeltaTable, []

    def table(uri, *args, storage_options=None, **kw):
        seen.append(storage_options)
        return real(uri, *args, **kw)

    root, _ = svc._proof_location(LS)
    monkeypatch.setattr(svc, "_proof_location", lambda ref: (root, lambda: onelake.storage_options("tok")))
    monkeypatch.setattr(deltalake, "DeltaTable", table)
    r = start(svc, pipeline(lookup("First", table_ds("orders")), metadata("Meta", table_ds("orders"), ["columnCount"])))
    assert r["state"] == "succeeded", r
    assert len(seen) == 2 and all(o == onelake.read_storage_options("tok") for o in seen)
    assert all(o["retry_timeout"] == onelake.READ_RETRY_TIMEOUT and o["bearer_token"] == "tok" for o in seen)
