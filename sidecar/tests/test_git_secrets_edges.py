"""Secrets in files about to be committed: the edges of the scanner and the redactor."""

import json

from fabric_desktop.git import secrets

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"  # split so this file isn't itself a finding


def test_a_very_long_line_is_scanned_only_in_its_first_20000_characters():
    near = "x" * 100 + " " + AWS
    far = "x" * 20_000 + " " + AWS
    assert [f["kind"] for f in secrets.scan_text(near)] == ["AWS access key"]
    assert secrets.scan_text(far) == []


def test_a_binary_file_is_named_if_sensitive_but_its_bytes_are_not_scanned():
    data = b"\0\1\2" + AWS.encode()
    assert secrets.scan_file("blob.bin", data) == []
    found = secrets.scan_file("certs/server.pfx", data)
    assert [f["kind"] for f in found] == ["Sensitive file"]
    assert found[0]["match"] == "server.pfx" and "gitignore" in found[0]["remediation"]


def test_a_notebook_that_is_not_json_is_scanned_as_text():
    found = secrets.scan_file("broken.ipynb", f"{{ not json\nkey = {AWS}\n".encode())
    assert [(f["kind"], f["line"]) for f in found] == [("AWS access key", 2)]
    assert "cell" not in found[0]


def test_a_json_file_without_cells_is_scanned_as_text():
    for doc in ([AWS], {"metadata": {}, "note": AWS}, {"cells": "not a list", "k": AWS}):
        found = secrets.scan_file("odd.ipynb", json.dumps(doc).encode())
        assert [f["kind"] for f in found] == ["AWS access key"]
        assert "cell" not in found[0]


def test_notebook_findings_name_the_cell_and_whether_it_was_output():
    nb = {"cells": [
        "not a cell",
        {"source": ["k = '", AWS, "'"], "outputs": []},
        {"source": 42, "outputs": [
            "not an output",
            {"text": ["printed ", AWS]},
            {"data": {"text/plain": AWS, "application/json": {"key": AWS}}},
        ]},
    ]}
    found = secrets.scan_file("nb.ipynb", json.dumps(nb).encode())
    assert {(f["cell"], f["where"]) for f in found} == {(2, "cell 2"), (3, "cell 3 output")}
    assert sum(f["where"] == "cell 3 output" for f in found) == 3  # text, the plain data and the JSON data


def test_redact_all_hides_each_value_and_keeps_empty_ones_empty():
    out = secrets.redact_all(["https://user:hunter2@example.com/repo.git", "", "plain words"])
    assert "hunter2" not in out[0]
    assert out[1:] == ["", "plain words"]
    assert secrets.redact(None) == ""
