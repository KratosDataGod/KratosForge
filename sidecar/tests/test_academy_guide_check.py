"""ACD-020: the study guide check finds what changed on the vendor's page, does nothing when nothing did,
and reports a page it can't read or fetch without failing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop.academy import guide_check, registry

FIXTURE = Path(__file__).parent / "fixtures" / "academy_guide" / "dp-700.html"
TOOL = Path(__file__).resolve().parents[1] / "tools" / "academy_guide_check.py"


@pytest.fixture
def cert():
    return registry.certification("dp-700")


@pytest.fixture
def page():
    return FIXTURE.read_text(encoding="utf-8")


def test_the_fixture_is_the_registrys_own_list(cert, page):
    got = guide_check.extract(page)
    assert got["updated"] == cert["source"]["page_updated"]
    assert [len(d["skills"]) for d in got["domains"]] == [len(d["skills"]) for d in cert["domains"]]
    assert sum(len(s["objectives"]) for d in got["domains"] for s in d["skills"]) == len(registry.objectives(cert))


def test_an_unchanged_page_does_nothing(cert, page):
    got = guide_check.check(cert, page)
    assert got == {"changed": False, "diff": got["diff"], "updated": "2026-10-30", "report": ""}
    assert got["diff"]["crosswalk"] == {}


def test_a_reworded_objective_is_reported_with_its_crosswalk(cert, page):
    changed = page.replace("Configure domain workspace settings", "Configure domain and subdomain workspace settings")
    got = guide_check.check(cert, changed)
    assert got["changed"]
    assert got["diff"]["reworded"] == [{"id": "1.1.2", "new_id": "1.1.2", "was": "Configure domain workspace settings",
                                        "now": "Configure domain and subdomain workspace settings"}]
    assert not got["diff"]["added"] and not got["diff"]["removed"]
    assert got["diff"]["crosswalk"]["1.1.2"] == ["1.1.2"]
    assert "Objectives reworded" in got["report"] and "Configure domain and subdomain workspace settings" in got["report"]


def test_an_added_and_a_removed_objective_renumber_the_rest(cert, page):
    changed = page.replace("<li><p>Endorse items</p></li>", "")
    changed = changed.replace("<li><p>Configure Spark workspace settings</p></li>",
                              "<li><p>Configure Spark workspace settings</p></li><li><p>Configure Copilot workspace settings</p></li>")
    diff = guide_check.check(cert, changed)["diff"]
    assert diff["added"] == [{"new_id": "1.1.2", "text": "Configure Copilot workspace settings"}]
    assert [r["text"] for r in diff["removed"]] == ["Endorse items"]
    assert diff["crosswalk"][next(r["id"] for r in diff["removed"])] == []
    assert {"id": "1.1.2", "new_id": "1.1.3", "text": "Configure domain workspace settings"} in diff["moved"]


def test_a_new_weight_and_skill_area_title_are_reported(cert, page):
    title = cert["domains"][2]["title"]
    changed = page.replace(f"{title} (30–35%)</h3>", f"{title} (25–30%)</h3>")
    changed = changed.replace(">Optimize performance</h4>", ">Optimize performance and cost</h4>")
    diff = guide_check.check(cert, changed)["diff"]
    assert diff["domains"] == [{"id": "3", "new_id": "3", "was": f"{title} (30-35%)", "now": f"{title} (25-30%)"}]
    assert [s["now"] for s in diff["skills"]] == ["Optimize performance and cost"]


def test_page_text_cant_break_or_link_out_of_the_report(cert, page):
    changed = page.replace("Endorse items", "Endorse | items [x](https://example.com) &lt;script&gt;")
    body = guide_check.check(cert, changed)["report"]
    assert "](https://" not in body and "<script>" not in body and "Endorse   items" in body
    flat = guide_check._cell("see https://evil.example/x, www.evil.example and @someone")
    assert "://" not in flat and "www.evil" not in flat and "@someone" not in flat and "someone" in flat


def test_a_page_without_the_skills_list_is_refused(cert):
    with pytest.raises(ValueError, match="layout may have changed"):
        guide_check.check(cert, "<html><h2>Skills measured</h2><h3>Audience profile</h3><ul><li>x</li></ul></html>")
    with pytest.raises(ValueError, match="over 4 MB"):
        guide_check.extract("x" * (guide_check.MAX_PAGE_BYTES + 1))


def test_only_learn_guides_are_checked():
    assert guide_check.checkable(registry.certification("dp-700"))
    assert not guide_check.checkable(registry.certification("dbx-de-associate"))


def _tool(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(TOOL), "--out", str(out), *args], capture_output=True, text=True,
                       timeout=120, check=False, cwd=TOOL.parents[1])
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    return p, summary


def test_the_tool_writes_a_report_only_for_a_changed_page(tmp_path, page):
    changed = tmp_path / "changed.html"
    changed.write_text(page.replace("Endorse items", "Endorse and certify items"), encoding="utf-8")
    p, summary = _tool(tmp_path, "--page", f"dp-700={changed}")
    assert p.returncode == 0, p.stderr
    assert summary["dp-700"]["status"] == "changed" and summary["changed"] is True
    assert "Endorse and certify items" in (tmp_path / "out" / "dp-700.md").read_text(encoding="utf-8")
    assert summary["dbx-de-associate"]["status"] == "not_checked"

    same = tmp_path / "same.html"
    same.write_text(page, encoding="utf-8")
    p, summary = _tool(tmp_path, "--page", f"dp-700={same}")
    assert p.returncode == 0 and summary["changed"] is False and summary["dp-700"]["status"] == "unchanged"


def test_a_fetch_failure_is_reported_and_exits_cleanly(tmp_path):
    p, summary = _tool(tmp_path, "--page", f"dp-700={tmp_path / 'missing.html'}")
    assert p.returncode == 0, p.stderr
    assert summary["dp-700"]["status"] == "failed" and summary["changed"] is False and summary["failed"] is True
    assert "couldn't" in summary["dp-700"]["detail"]
