"""The Fabric API capability matrix (FEA-106): every control's reads are known, checked against
Microsoft's reference and usable without a Fabric administrator, and the doc matches the module."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from types import SimpleNamespace

import pytest

from fabric_desktop.excellence import api_matrix as am, catalog

REG = catalog.registry()
DOC = pathlib.Path(__file__).resolve().parents[2] / "docs" / "optimize" / "excellence-api-matrix.md"


def fake(*controls) -> SimpleNamespace:
    return SimpleNamespace(controls=lambda: list(controls))


def ctl(cid: str, kinds: tuple[str, ...], optional: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(id=cid, evidence_kinds=kinds, optional_kinds=optional)


def test_catalog_has_no_gaps():
    assert am.gaps(REG) == []


def test_every_payload_kind_has_a_source():
    assert set(catalog.PAYLOADS) <= set(am.SOURCES)
    for srcs in am.SOURCES.values():
        assert srcs and all(s in am.ENDPOINTS or s in am.LOCAL for s in srcs)


def test_every_control_kind_has_a_source():
    for c in REG.controls():
        for k in (*c.evidence_kinds, *c.optional_kinds):
            assert k in am.SOURCES, (c.id, k)


@pytest.mark.parametrize("eid", list(am.ENDPOINTS))
def test_endpoints_are_checked_and_referenced(eid):
    e = am.ENDPOINTS[eid]
    assert e.id == eid
    assert e.checked == am.CHECKED
    assert e.reference.startswith("https://learn.microsoft.com/")
    assert e.scopes and e.role and e.paging and e.throttling
    assert not e.admin_only


def test_every_endpoint_is_used_by_some_control():
    used = am.controls_by_endpoint(REG)
    assert all(used[e] for e in am.ENDPOINTS), {e: v for e, v in used.items() if not v}


def test_unmapped_kind_is_a_gap():
    out = am.gaps(fake(ctl("X-1", ("nope",))))
    assert out == [{"control_id": "X-1", "problems": ["evidence nope has no source in the matrix"]}]


def test_optional_kind_counts():
    assert am.gaps(fake(ctl("X-2", (), ("nope",))))[0]["control_id"] == "X-2"


def test_unchecked_and_admin_only_endpoints_are_gaps(monkeypatch):
    kind, src = next((k, v[0]) for k, v in am.SOURCES.items() if v[0] in am.ENDPOINTS)
    e = am.ENDPOINTS[src]
    monkeypatch.setitem(am.ENDPOINTS, src, dataclasses.replace(e, checked="", admin_only=True))
    probs = am.gaps(fake(ctl("X-3", (kind,))))[0]["problems"]
    assert f"{src} isn't checked against the reference" in probs
    assert f"{src} needs a Fabric administrator" in probs


def test_unknown_source_is_a_gap(monkeypatch):
    monkeypatch.setitem(am.SOURCES, "made-up", ("no-such-endpoint",))
    probs = am.gaps(fake(ctl("X-4", ("made-up",))))[0]["problems"]
    assert probs == ["no-such-endpoint isn't a known endpoint"]


def test_local_source_isnt_a_fabric_call():
    assert am.LOCAL_USAGE in am.LOCAL and am.LOCAL_USAGE not in am.ENDPOINTS


def test_doc_matches_module():
    text = DOC.read_text(encoding="utf-8")
    start, end = "<!-- api-matrix:start -->\n", "<!-- api-matrix:end -->"
    assert start in text and end in text
    body = text.split(start, 1)[1].split(end, 1)[0].strip("\n") + "\n"
    assert body == am.markdown(REG)


def test_as_dict_is_json():
    d = json.loads(json.dumps(am.as_dict(REG)))
    assert d["checked"] == am.CHECKED
    assert [e["id"] for e in d["endpoints"]] == list(am.ENDPOINTS)
    assert set(d["sources"]) == set(am.SOURCES)


def test_matrix_reads_nothing(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network"))
    am.markdown(REG)
    am.as_dict(REG)
