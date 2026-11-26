"""`fabric-desktop directlake-check` (cli.py): the Direct Lake fallback prediction for a model on this PC, from a
TMDL folder or model.bim and local Delta tables written in tmp_path. Checks what it prints, the exit code each
verdict gives, and that it refuses URIs and never opens a connection. Nothing reaches a tenant."""

from __future__ import annotations

import json
import socket

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import cli, tmdl
from fabric_desktop import model_directlake as dl
from tests.test_model_directlake_predict import ONELAKE, _bim


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every connection attempt fails the test: the command reads only local files."""
    attempts = []

    def refuse(*a, **k):
        attempts.append(a)
        raise AssertionError("directlake-check opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield attempts
    assert attempts == []


def _delta(path, rows=10, files=1):
    """A local Delta table with column k, written in one commit as ``files`` Parquet files."""
    data = {"k": list(range(rows)), "p": [i % files for i in range(rows)]}
    write_deltalake(str(path), pa.table(data), partition_by=["p"] if files > 1 else None)
    return str(path)


def _bim_file(tmp_path, bim=None):
    path = tmp_path / "model.bim"
    path.write_text(json.dumps(bim or _bim()), encoding="utf-8")
    return str(path)


def _tmdl_folder(tmp_path, bim=None):
    folder = tmp_path / "Sales.SemanticModel"
    for rel, text in tmdl.from_bim(bim or _bim())[0].items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return str(folder)


def _run(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["directlake-check", *argv])
    return stop.value.code, capsys.readouterr().out


def _both(tmp_path, fact_files=1, sku="F64", extra=()):
    fact = _delta(tmp_path / "fact", rows=max(10, fact_files), files=fact_files)
    dim = _delta(tmp_path / "dim", rows=5)
    return ["--table", f"FactSales={fact}", "--table", f"DimCustomer={dim}", "--sku", sku, *extra]


@pytest.mark.parametrize("model", [_bim_file, _tmdl_folder], ids=["bim", "tmdl"])
def test_a_small_model_is_within_limits(tmp_path, capsys, model):
    code, out = _run(["--model", model(tmp_path), *_both(tmp_path)], capsys)
    lines = out.splitlines()
    assert code == 0
    assert lines[0].startswith("Within limits: Every table is within F64's")
    assert lines[1] == dl.ENDPOINT_CAVEAT
    assert "  FactSales (gold.factsales): Within limits." in out
    assert "Smallest capacity that fits: F2 today, F2 in 90 days." in out
    assert dl.PREDICTED in out
    assert out.index(dl.PREDICTED) < out.index("What this can't see from this PC:")


def test_the_definition_folder_itself_is_read(tmp_path, capsys):
    folder = _tmdl_folder(tmp_path)
    code, _ = _run(["--model", f"{folder}/definition", *_both(tmp_path)], capsys)
    assert code == 0


def test_near_the_file_limit_might_fall_back(tmp_path, capsys):
    code, out = _run(["--model", _bim_file(tmp_path), *_both(tmp_path, fact_files=950, sku="F2")], capsys)
    assert code == 1
    assert out.startswith("Might fall back: FactSales is within 10% of F2's Parquet file limit")
    assert "Deciding table: FactSales" in out
    assert "Fix: Compact the table, and batch the appends." in out
    assert "Forecast: Not enough history" in out
    assert "Smallest capacity that fits: F64 today" in out


def test_past_the_file_limit_will_fall_back(tmp_path, capsys):
    code, out = _run(["--model", _bim_file(tmp_path), *_both(tmp_path, fact_files=1001, sku="F2")], capsys)
    assert code == 1
    assert out.startswith("Will fall back: FactSales is past F2's Parquet file limit.")
    assert "FactSales (gold.factsales): Will fall back." in out


def test_a_folder_that_isnt_a_delta_table_is_a_view_that_will_fall_back(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    dim = _delta(tmp_path / "dim")
    code, out = _run(["--model", _bim_file(tmp_path), "--table", f"FactSales={tmp_path / 'empty'}",
                      "--table", f"DimCustomer={dim}"], capsys)
    assert code == 1
    assert out.startswith("Will fall back: FactSales's source is a SQL view")
    assert "On every capacity" in out and "FactSales." in out


def test_a_onelake_model_says_will_fail(tmp_path, capsys):
    bim = _bim(source=ONELAKE)
    code, out = _run(["--model", _bim_file(tmp_path, bim), *_both(tmp_path, fact_files=1001, sku="F2")], capsys)
    assert code == 1
    assert out.startswith("Will fail: ")


def test_a_table_with_no_path_is_not_checked(tmp_path, capsys):
    fact = _delta(tmp_path / "fact")
    code, out = _run(["--model", _bim_file(tmp_path), "--table", f"FactSales={fact}"], capsys)
    assert code == 2
    assert out.startswith("Not checked: 1 table wasn't read")
    assert "Deciding table: DimCustomer" in out
    assert "  DimCustomer (gold.dimcustomer): Not checked. Its Delta table wasn't read. No --table was given" in out
    assert "Within limits: " not in out
    assert "1 table that couldn't be read: DimCustomer." in out
    assert "Smallest capacity that fits: not known today, not known in 90 days." in out


def test_no_tables_at_all_is_not_checked_in_json(tmp_path, capsys):
    code, out = _run(["--model", _bim_file(tmp_path), "--json"], capsys)
    doc = json.loads(out)
    assert code == doc["exit_code"] == 2
    assert doc["verdict"] == "not_checked"
    assert {u["table"] for u in doc["unread"]} == {"FactSales", "DimCustomer"}


def test_a_table_is_named_by_its_entity_too(tmp_path, capsys):
    fact, dim = _delta(tmp_path / "fact"), _delta(tmp_path / "dim")
    code, _ = _run(["--model", _bim_file(tmp_path), "--table", f"gold.factsales={fact}",
                    "--table", f"dimcustomer={dim}"], capsys)
    assert code == 0


def test_json_shape(tmp_path, capsys):
    code, out = _run(["--model", _bim_file(tmp_path), *_both(tmp_path, extra=("--json", "--horizon-days", "30"))],
                     capsys)
    doc = json.loads(out)
    assert code == 0
    assert set(doc) == {"model", "sku", "horizon_days", "verdict", "label", "exit_code", "unread", "findings",
                        "counts", "prediction"}
    assert (doc["sku"], doc["horizon_days"], doc["verdict"], doc["label"]) == ("F64", 30, "within", "Within limits")
    assert doc["prediction"]["label"] == dl.PREDICTED
    assert set(doc["prediction"]["by_sku"]) == set(dl.SKUS)
    assert [t["table"] for t in doc["prediction"]["tables"]] == ["FactSales", "DimCustomer"]


@pytest.mark.parametrize("alias, sku", [("P1", "F64"), ("ft1", "F64"), ("P3", "F256")], ids=["p1", "ft1", "p3"])
def test_premium_and_trial_skus_are_read_as_their_f_sku(tmp_path, capsys, alias, sku):
    code, out = _run(["--model", _bim_file(tmp_path), *_both(tmp_path, sku=alias, extra=("--json",))], capsys)
    assert code == 0
    assert json.loads(out)["sku"] == sku


@pytest.mark.parametrize("uri", ["abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/fact",
                                 "https://example.invalid/fact", "file:///tmp/fact", "//server/share/fact",
                                 "\\\\server\\share\\fact"], ids=["abfss", "https", "file", "unc", "unc-win"])
def test_a_uri_or_network_share_is_refused(tmp_path, capsys, uri):
    dim = _delta(tmp_path / "dim")
    code, out = _run(["--model", _bim_file(tmp_path), "--table", f"FactSales={uri}",
                      "--table", f"DimCustomer={dim}"], capsys)
    assert code == 2
    assert "isn't a path on this PC. directlake-check reads only local files" in out


def test_a_model_uri_is_refused(tmp_path, capsys):
    code, out = _run(["--model", "abfss://ws@onelake.dfs.fabric.microsoft.com/m/model.bim"], capsys)
    assert code == 2
    assert "isn't a path on this PC" in out


def _text(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("make, says", [
    (lambda p: str(p / "nowhere"), "isn't a folder or file on this PC"),
    (lambda p: _text(p / "x.bim", "{nope"), "isn't valid JSON"),
    (lambda p: _text(p / "x.bim", "[1]"), "isn't a semantic model"),
    (lambda p: (p / "f").mkdir() or str(p / "f"), "has neither definition/ (TMDL) nor model.bim"),
], ids=["missing", "json", "not-model", "empty-folder"])
def test_a_bad_model_path_is_bad_input(tmp_path, capsys, make, says):
    code, out = _run(["--model", make(tmp_path)], capsys)
    assert code == 2
    assert says in out


@pytest.mark.parametrize("argv, says", [
    (["--sku", "F3"], "isn't a capacity SKU this check knows"),
    (["--horizon-days", "0"], "--horizon-days is from 1 to"),
    (["--table", "FactSales"], "should be NAME=PATH"),
    (["--table", "Nope=."], "The model has no Direct Lake table named Nope."),
    (["--table", "FactSales=nowhere-here"], "isn't a folder on this PC"),
], ids=["sku", "horizon", "no-equals", "unknown-table", "missing-folder"])
def test_bad_arguments_are_bad_input(tmp_path, capsys, monkeypatch, argv, says):
    monkeypatch.chdir(tmp_path)
    code, out = _run(["--model", _bim_file(tmp_path), *argv], capsys)
    assert code == 2
    assert says in out


def test_a_model_with_no_direct_lake_tables_is_not_checked(tmp_path, capsys):
    bim = {"name": "M", "model": {"tables": [{"name": "T", "partitions": [{"name": "T", "mode": "import"}]}]}}
    code, out = _run(["--model", _bim_file(tmp_path, bim)], capsys)
    assert code == 2
    assert out.startswith("Not checked: the model has no Direct Lake tables.")


def test_long_and_odd_table_names_are_cut_and_made_printable(tmp_path, capsys):
    long = "T" * 300 + "\x1b[2J"
    fact = _delta(tmp_path / "fact")
    bim = _bim(names=(long,))
    code, out = _run(["--model", _bim_file(tmp_path, bim), "--table", f"{long}={fact}"], capsys)
    assert code == 0
    assert "\x1b" not in out
    assert "T" * 300 not in out
    assert max(len(w) for w in out.split()) <= cli.DL_NAME_CHARS + 4  # "(name):"


def test_help_documents_the_exit_codes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "directlake-check" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["directlake-check", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "--model" in text and "NAME=PATH" in text
