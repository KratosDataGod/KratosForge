"""Mutation batch 96: receipts' messages, list order and limit, cell defaults, number formats, and the
Markdown and HTML reports line by line. Receipts go to a temporary folder."""

from __future__ import annotations

import hashlib

import pytest

from fabric_desktop import receipts
from fabric_desktop.receipts import ReceiptError, ReceiptStore

RID = "0123456789ab" + "c" * 52


def rich():
    return {
        "schema": receipts.SCHEMA, "kind": "proof", "created_at": "2026-11-01T10:00:00+00:00",
        "notebook": {"name": "Sales", "parameters": {"year": 2026, "region": "EU"},
                     "code": {"revision": "ab" * 32, "cells": [{"id": "c1"}, {"id": "c2"}]}},
        "verification": {"verdict": "same", "text": "Both match", "level": "values"},
        "outputs": [{"table": "gold.sales", "result": "same", "level": "values", "pc": {"rows": 1234},
                     "fabric": {"rows": 1234}, "note": "a|b"}],
        "inputs": {"tables": [{"name": "raw.sales", "version": 7}],
                   "files": [{"path": "data/x.csv", "sha256": "0123456789abcdef" + "f" * 48}]},
        "runs": {"pc": {"seconds": 2.5, "status": "ok", "engine": "duckdb", "peak_mb": 1536, "extra": {"a": 1}},
                 "fabric": {"seconds": 10.0, "status": "ok"}},
        "cost": {"text": "PC runs use no capacity.",
                 "pc": {"measurement": {"value": None, "classification": "not_applicable"}},
                 "fabric": {"measurement": {"value": 12.5, "label": "Measured", "reason": "from the capacity app"}},
                 "assumptions": ["Prices as listed today."]},
        "environment": {"pc": {"python": "3.12.1", "packages": {f"p{i:02d}": f"1.{i}" for i in range(13)}},
                        "note": "not a side"},
        "limitations": ["Row order isn't compared."],
    }


MARKDOWN = [
    '# Sales: proof receipt',
    '',
    'Receipt `eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee`, created 2026-11-01T10:00:00+00:00.',
    "Check it: the SHA-256 of the receipt's canonical JSON is its id (fabric-desktop/run-receipt@1).",
    '',
    'Redacted in this export: who ran it (user and tenant), row values shown as examples of differences.',
    '',
    '## Result',
    '',
    '**Both match**',
    '',
    'Verification: values.',
    '',
    '## Outputs',
    '',
    '| Table | Result | Level | Rows (PC) | Rows (Fabric) | Note |',
    '| --- | --- | --- | --- | --- | --- |',
    '| gold.sales | same | values | 1,234 | 1,234 | a/b |',
    '',
    '## Inputs',
    '',
    '- Table `raw.sales` at Delta version 7',
    '- File `data/x.csv` SHA-256 `0123456789abcdef…`',
    '',
    '## Measurements',
    '',
    '| | Duration | Detail |',
    '| --- | --- | --- |',
    '| This PC | 2.5 s (ok) | engine duckdb; peak mb 1,536 |',
    '| Fabric | 10 s (ok) |  |',
    '',
    '## Cost',
    '',
    'PC runs use no capacity.',
    '',
    '- This PC: Fabric job CU not known (**not_applicable**)',
    '- Fabric: Fabric job CU 12.5 CU-seconds (**Measured**; from the capacity app)',
    '- Prices as listed today.',
    '',
    '## Environment',
    '',
    '- pc: Python 3.12.1; p00 1.0, p01 1.1, p02 1.2, p03 1.3, p04 1.4, p05 1.5, p06 1.6, p07 1.7, p08 1.8, p09 1.9, p10 1.10, p11 1.11',
    '',
    '## Code',
    '',
    'Revision `abababababababab…`, 2 cells.',
    '',
    'Parameters:',
    '',
    '```json',
    '{',
    '  "region": "EU",',
    '  "year": 2026',
    '}',
    '```',
    '',
    '## Limitations',
    '',
    "- Row order isn't compared.",
    '',
]

HTML_BODY = [
    '<h1>Sales: proof receipt</h1>',
    '<p>Receipt <code>eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee</code>, created 2026-11-01T10:00:00+00:00.</p>',
    '<p>Check it: the SHA-256 of the receipt&#x27;s canonical JSON is its id (fabric-desktop/run-receipt@1).</p>',
    '<h2>Result</h2>',
    '<p><b>Both match</b></p>',
    '<p>Verification: values.</p>',
    '<h2>Outputs</h2>',
    '<table>',
    '<tr><th>Table</th><th>Result</th><th>Level</th><th>Rows (PC)</th><th>Rows (Fabric)</th><th>Note</th></tr>',
    '<tr><td>gold.sales</td><td>same</td><td>values</td><td>1,234</td><td>1,234</td><td>a/b</td></tr>',
    '</table>',
    '<h2>Inputs</h2>',
    '<li>Table <code>raw.sales</code> at Delta version 7</li>',
    '<li>File <code>data/x.csv</code> SHA-256 <code>0123456789abcdef…</code></li>',
    '<h2>Measurements</h2>',
    '<table>',
    '<tr><th></th><th>Duration</th><th>Detail</th></tr>',
    '<tr><td>This PC</td><td>2.5 s (ok)</td><td>engine duckdb; peak mb 1,536</td></tr>',
    '<tr><td>Fabric</td><td>10 s (ok)</td><td></td></tr>',
    '</table>',
    '<h2>Cost</h2>',
    '<p>PC runs use no capacity.</p>',
    '<li>This PC: Fabric job CU not known (<b>not_applicable</b>)</li>',
    '<li>Fabric: Fabric job CU 12.5 CU-seconds (<b>Measured</b>; from the capacity app)</li>',
    '<li>Prices as listed today.</li>',
    '<h2>Environment</h2>',
    '<li>pc: Python 3.12.1; p00 1.0, p01 1.1, p02 1.2, p03 1.3, p04 1.4, p05 1.5, p06 1.6, p07 1.7, p08 1.8, p09 1.9, p10 1.10, p11 1.11</li>',
    '<h2>Code</h2>',
    '<p>Revision <code>abababababababab…</code>, 2 cells.</p>',
    '<p>Parameters:</p>',
    '<pre>',
    '{',
    '  &quot;region&quot;: &quot;EU&quot;,',
    '  &quot;year&quot;: 2026',
    '}',
    '</pre>',
    '<h2>Limitations</h2>',
    '<li>Row order isn&#x27;t compared.</li></body></html>',
]


def test_the_markdown_report_line_by_line():
    assert receipts.to_markdown(rich(), "e" * 64, hide=["values", "identity"]).split("\n") == MARKDOWN


def test_the_html_report_line_by_line():
    page = receipts.to_html(rich(), "e" * 64)
    head, body = page.split("<body>", 1)
    assert head.startswith("<!doctype html><html><head><meta charset=\"utf-8\"><title>Sales receipt</title><style>")
    assert body.split("\n") == HTML_BODY


def test_a_receipt_without_a_notebook_is_titled_notebook():
    body = {"schema": receipts.SCHEMA, "kind": "run", "created_at": "t"}
    assert "<title>Notebook receipt</title>" in receipts.to_html(body, "e" * 64)
    assert receipts.to_markdown(body, "e" * 64).split("\n")[0] == "# Notebook: run receipt"


def test_a_verdict_without_text_or_level_is_shown_alone():
    body = {"schema": receipts.SCHEMA, "kind": "proof", "verification": {"verdict": "differs"}}
    lines = receipts.to_markdown(body, "e" * 64).split("\n")
    assert lines[5:9] == ["## Result", "", "**differs**", ""]
    assert not any(x.startswith("Verification:") for x in lines)


def test_inputs_with_only_files_are_listed_and_a_missing_hash_is_blank():
    body = {"schema": receipts.SCHEMA, "kind": "run", "inputs": {"files": [{"path": "a.csv"}]}}
    lines = receipts.to_markdown(body, "e" * 64).split("\n")
    assert lines[5:9] == ["## Inputs", "", "- File `a.csv` SHA-256 `…`", ""]


def test_cells_without_ids_or_kinds_are_numbered_from_one_and_count_as_code():
    rev = receipts.code_revision([{"source": "x = 1"}, {"cell_id": "", "type": "", "source": None}])
    assert [(c["id"], c["type"]) for c in rev["cells"]] == [("#1", "code"), ("#2", "code")]
    assert rev["cells"][1]["sha256"] == hashlib.sha256(b"").hexdigest()  # a missing source hashes as empty


def test_canonical_json_keeps_text_as_utf8():
    assert receipts.canonical({"name": "Kraków", "b": [1, 2]}) == '{"b":[1,2],"name":"Kraków"}'.encode("utf-8")


def test_numbers_are_formatted_for_reading():
    assert receipts._fmt(1234567) == "1,234,567"
    assert receipts._fmt(True) == "True"
    assert receipts._fmt(None) == "–"
    assert receipts._fmt(2.50) == "2.5"
    assert receipts._fmt(1e15) == "1.000e+15"
    assert receipts._fmt(999_999_999_999_999.0) == "999,999,999,999,999"


def test_the_store_names_a_receipt_by_its_first_twelve_characters(tmp_path):
    store = ReceiptStore(tmp_path)
    with pytest.raises(LookupError) as missing:
        store.get(RID)
    assert str(missing.value) == "No receipt 0123456789ab"
    (tmp_path / f"{RID}.json").write_bytes(b"{}")
    with pytest.raises(ReceiptError) as changed:
        store.get(RID)
    assert str(changed.value) == ("Receipt 0123456789ab has been changed since it was written; "
                                  "it can't be trusted.")


def test_a_conflicting_put_names_the_receipt_by_its_first_twelve_characters(tmp_path):
    store = ReceiptStore(tmp_path)
    body = {"schema": receipts.SCHEMA, "kind": "run", "created_at": "t"}
    rid = store.put(body)
    (tmp_path / f"{rid}.json").write_bytes(b"{}")
    with pytest.raises(ReceiptError) as caught:
        store.put(body)
    assert str(caught.value) == f"Receipt {rid[:12]} already exists with different content; it is left as it was."


def _put(store, day, **extra):
    body = {"schema": receipts.SCHEMA, "kind": "run", "notebook": {"name": "Sales"}, **extra}
    if day is not None:
        body["created_at"] = f"2026-10-{day:02d}T09:00:00+00:00"
    return store.put(body)


def test_the_list_is_newest_first_with_undated_receipts_last(tmp_path):
    store = ReceiptStore(tmp_path)
    days = [3, 1, 6, 2, 5, 4]
    ids = {d: _put(store, d) for d in days}
    undated = _put(store, None, verification={"verdict": "same"})
    rows = store.list()
    assert [r["id"] for r in rows] == [ids[d] for d in (6, 5, 4, 3, 2, 1)] + [undated]
    assert rows[-1]["verdict"] == "same" and rows[0]["verdict"] is None


def test_the_list_shows_fifty_unless_asked_for_more(tmp_path):
    store = ReceiptStore(tmp_path)
    for day in range(1, 27):
        _put(store, day)
        _put(store, day, kind="proof")
    assert len(store.list()) == 50
    assert len(store.list(limit=60)) == 52


@pytest.mark.xfail(strict=True, reason="the redaction note lowercases 'This PC' to 'this pc'")
def test_the_redaction_note_keeps_this_pc_as_written():
    body = {"schema": receipts.SCHEMA, "kind": "run"}
    md = receipts.to_markdown(body, "e" * 64, hide=["machine"])
    assert "Redacted in this export: this PC's name and hardware." in md
