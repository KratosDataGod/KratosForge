"""SQL page > Explorer > Diagram: Export as SVG writes a new file in Downloads and refuses anything active."""
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, server

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10" role="img" '
       'aria-label="Diagram of 1 table"><rect width="100%" height="100%" fill="#ffffff"/><text x="1" y="5">dim_customer</text></svg>')


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("time.strftime", lambda *_a, **_k: "2026-11-16 120000")
    return server.Service(client_id="x", root=tmp_path)


def call(service, params):
    return server.handle(service, json.dumps({"id": 1, "method": "sql_diagram_export", "params": params}))


def test_the_diagram_is_saved_as_a_new_file_in_downloads(service, tmp_path):
    first = call(service, {"svg": SVG, "name": "SalesWH/../gold diagram"})["result"]
    second = call(service, {"svg": SVG, "name": "SalesWH/../gold diagram"})["result"]
    assert Path(first["path"]).name == "SalesWHgold diagram 2026-11-16 120000.svg"
    assert Path(second["path"]).name == "SalesWHgold diagram 2026-11-16 120000 (2).svg"  # never overwritten
    assert Path(first["path"]).parent == tmp_path / "Downloads"
    assert Path(first["path"]).read_text(encoding="utf-8") == SVG
    assert Path(call(service, {"svg": SVG})["result"]["path"]).name.startswith("Diagram ")


@pytest.mark.parametrize("bad", [
    "<html><svg></svg></html>", "<svg >", '<svg x="1"><script>alert(1)</script></svg>',
    '<svg x="1"><foreignObject><div/></foreignObject></svg>', '<svg x="1"><rect onclick="x()"/></svg>',
    '<svg x="1"><rect\nonload="x()"/></svg>', '<svg x="1"><a href="https://example.com">t</a></svg>',
    '<svg x="1"><image xlink:href="file:///c:/x"/></svg>', '<svg x="1"><!DOCTYPE x [<!ENTITY a "b">]></svg>',
    '<svg x="1"><rect style="fill:url(https://e.com/a)"/></svg>', '<svg x="1"><rect fill="url(#a)"/></svg>',
    '<svg x="1"><?xml-stylesheet href="a.css"?></svg>'])
def test_anything_a_diagram_never_has_is_refused_and_nothing_is_written(service, tmp_path, bad):
    assert "error" in call(service, {"svg": bad})
    assert not (tmp_path / "Downloads").exists() or not list((tmp_path / "Downloads").iterdir())


def test_a_table_name_that_looks_like_an_attribute_stays_text(service):
    # The page escapes < and >, so a name like this is text inside an element, never an attribute.
    svg = SVG.replace("dim_customer", "x onload=1 href=y")
    assert "path" in call(service, {"svg": svg})["result"]


def test_too_big_is_refused(service):
    big = SVG.replace("dim_customer", "a" * (4 * 1024 * 1024))
    assert "too big" in call(service, {"svg": big})["error"]


def test_the_export_is_audited_without_the_drawing():
    assert audit.audited("sql_diagram_export")
    assert audit.target({"svg": SVG, "name": "gold"})["svg"] == "(given)"
