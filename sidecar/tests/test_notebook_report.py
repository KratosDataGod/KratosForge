"""Notebook > Share > Open as report (Preview): the engine keeps one report in memory for the report
window, the page is the web page export with the code hidden, and Export as HTML writes that page."""
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, known_folders, server
from fabric_desktop import notebook_formats as nbf
from tests.test_notebook_formats import svc  # noqa: F401  (fixture)

CELLS = [{"type": "markdown", "source": "Revenue by <region>"},
         {"type": "code", "source": "secret_code = 1", "outputs": [], "count": 1},
         {"type": "code", "source": "df.show()", "count": 2,
          "outputs": [{"type": "table", "columns": ["region"], "rows": [["West"]]}, {"type": "html", "html": "<script>x()</script>"}]}]
RAN = "Outputs from the run at 10:42 on 14 Nov, on this PC."


def test_the_report_shows_markdown_and_outputs_only(svc):  # noqa: F811
    out = svc.notebook_report_open("Weekly sales", CELLS, RAN)
    page = svc.notebook_report_page()
    assert out["name"] == "Weekly sales" and page["name"] == "Weekly sales" and page["stale"] is False
    html = page["html"]
    assert "secret_code" not in html and "df.show()" not in html and "Cell 1" not in html
    assert "Revenue by &lt;region&gt;" in html and "<td>West</td>" in html and "<script" not in html
    assert f"{RAN} Code is hidden. 3 cells, 1 of them Markdown." in html
    assert "default-src 'none'" in html  # the export's own policy: no script, nothing loads
    assert html == nbf.to_html("Weekly sales", CELLS, report=f"{RAN} Code is hidden. 3 cells, 1 of them Markdown.")


def test_a_new_report_replaces_the_last_and_says_when_it_is_out_of_date(svc):  # noqa: F811
    assert svc.notebook_report_page() == {}
    svc.notebook_report_open("A", CELLS, RAN)
    svc.notebook_report_open("B", CELLS[:1], RAN, stale=True)
    page = svc.notebook_report_page()
    assert page["name"] == "B" and page["stale"] is True and "Code is hidden. 1 cell, Markdown." in page["html"]


def test_export_writes_the_page_the_window_shows_to_a_new_file(svc):  # noqa: F811
    with pytest.raises(ValueError, match="no report to export"):
        svc.notebook_report_export()
    svc.notebook_report_open("Weekly/../sales", CELLS, RAN)
    first, second = svc.notebook_report_export(), svc.notebook_report_export()
    assert Path(first["path"]).name == "Weeklysales report.html"
    assert Path(second["path"]).name == "Weeklysales report (2).html"
    assert Path(first["path"]).parent == known_folders.downloads()
    assert Path(first["path"]).read_text(encoding="utf-8") == svc.notebook_report_page()["html"]


def test_bad_input_and_a_huge_report_are_refused(svc, monkeypatch):  # noqa: F811
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_report_open("A", "x", RAN)
    with pytest.raises(ValueError, match="short line"):
        svc.notebook_report_open("A", CELLS, "x" * 201)
    from fabric_desktop import notebook_formats_service as nfs
    monkeypatch.setattr(nfs, "REPORT_MAX_BYTES", 100)
    with pytest.raises(ValueError, match="larger than 64 MB"):
        svc.notebook_report_open("A", CELLS, RAN)
    assert svc.notebook_report_page() == {}


def test_the_methods_are_served_and_only_export_is_audited():
    assert {"notebook_report_open", "notebook_report_page", "notebook_report_export"} <= server.METHODS
    assert {"notebook_report_open", "notebook_report_page"} <= audit.READ_ONLY
    assert audit.audited("notebook_report_export")
    assert audit.target({"name": "A", "cells": CELLS, "ran": RAN})["cells"] == "(given)"
    json.dumps(audit.target({"cells": CELLS}))
