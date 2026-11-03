"""KF-DX-009/010/011: the Impact tab, the PDF report and goals with the development roadmap.

What keeps them honest: Impact shows only figures a row measured (never a sum, an estimate or a
saving), a roadmap milestone is met only by verified evidence and can't be ticked, suggestions sit
next to work already shown, no milestone needs a Fabric run, and the PDF is the same report with no
game terms, readable by any PDF reader."""

from __future__ import annotations

import json
import re

import pytest

from fabric_desktop import audit, pdfreport, progress, ranks, roadmap
from fabric_desktop.learn import content

from test_profile import call, plain, profile_with, validation  # noqa: F401 - the signed-in service fixture
from test_profile_learn import LM01, lab, passed


def fix_row(rule="SQ001", before=2.0, after=0.5, runs=("run-a", "run-b"), where="dbo.orders query"):
    return progress.from_fix(rule, "Performance", {"result": "match", "rows": {"after": 10}, "level": "complete",
                                                   "seconds": {"before": before, "after": after}},
                             {"before": runs[0], "after": runs[1]}, where)


def proof(name="Orders", revision="r1", pc=12.5, fabric=95.0):
    record = {"name": name, "state": "done", "verdict": {"verdict": "same", "level": "complete"},
              "code": {"revision": revision}, "tables": [{"name": "t"}], "finished_at": "2026-10-21T10:00:00+00:00",
              "scorecard": {"time": {"pc": {"kind": "measured", "seconds": pc},
                                     "fabric": {"kind": "measured", "total_seconds": fabric}}}}
    return progress.from_proof(record, "a" * 64)


def live_modules():
    return content.modules(include_drafts=False)


# ---- Impact


def test_impact_lists_only_measured_figures_and_counts_outcomes():
    p = profile_with(validation(), validation(name="Sales", revision="r2"))
    progress.add(p, fix_row())
    progress.add(p, fix_row(rule="SQ002", before=1.0, after=None, runs=("c", "d")))   # untimed: an outcome, not a figure
    progress.add(p, proof())
    progress.add(p, progress.from_lab(passed(kinds=("result_matches", "faster_than"),
                                             measured={"seconds": 0.1, "baseline_seconds": 0.4, "speedup": 4.0}), LM01))
    out = progress.impact(p)
    kinds = sorted(x["kind"] for x in out["measured"])
    assert kinds == ["fix", "proof", "speed"]
    fix = next(x for x in out["measured"] if x["kind"] == "fix")
    assert fix["before_seconds"] == 2.0 and fix["after_seconds"] == 0.5 and fix["change_percent"] == 75.0
    pr = next(x for x in out["measured"] if x["kind"] == "proof")
    assert pr["this_pc_seconds"] == 12.5 and pr["fabric_seconds"] == 95.0
    assert out["faster"] == 2
    assert out["outcomes"] == {"notebooks_validated": 2, "notebooks_proven": 1, "fixes_checked": 2, "quality_labs": 0}
    assert out["capacity"]["measured"] is False
    text = json.dumps(out).lower()
    for word in ("saving\"", "cost\":", "estimate", "total_seconds_saved", "$"):
        assert word not in text


def test_an_excluded_row_leaves_impact():
    p = progress.empty()
    row = fix_row()
    progress.add(p, row)
    assert len(progress.impact(p)["measured"]) == 1
    p["excluded"].append(row["id"])
    assert progress.impact(p)["measured"] == [] and progress.impact(p)["outcomes"]["fixes_checked"] == 0


def test_a_slower_fix_is_shown_as_measured_not_hidden():
    p = progress.empty()
    progress.add(p, fix_row(before=1.0, after=1.5))
    out = progress.impact(p)
    assert out["measured"][0]["change_percent"] == -50.0 and out["faster"] == 0


# ---- the roadmap


def test_every_milestone_is_well_formed_and_can_be_met_on_this_pc():
    shipped = {m["id"] for m in live_modules()}
    assert len(roadmap.TRACKS) == 7
    titles = [t["title"] for t in roadmap.TRACKS]
    assert titles == ["Data Engineering", "SQL & Warehouse", "Semantic Modeling", "Data Integration", "Data Governance",
                      "Performance Optimization", "Platform Engineering"]
    for m in roadmap.MILESTONES.values():
        assert m["skills"] and set(m["skills"]) <= set(progress.DIMENSIONS), m["id"]
        assert m["activities"] and m["needs"], m["id"]
        for need in m["needs"]:
            (kind, want), = need.items()
            assert kind in ("modules", "notebooks", "fixes", "exercises", "labs", "achievements"), m["id"]
            if kind == "modules":
                assert set(want) <= shipped, f"{m['id']} needs a lesson that hasn't shipped"
            if kind == "achievements":
                assert "proof-forged" not in want, "a milestone must never need a Fabric run"
        assert set(m["achievements"]) <= set(progress.ACHIEVEMENTS), m["id"]
        assert set(m["learn"]) <= shipped, m["id"]
    assert len(roadmap.MILESTONES) == sum(len(t["milestones"]) for t in roadmap.TRACKS), "milestone ids must be unique"


def test_a_milestone_is_met_by_verified_evidence_and_suggests_its_tracks_next_step():
    p = profile_with(validation())
    plan = roadmap.roadmap(p, live_modules())
    de = next(t for t in plan["tracks"] if t["id"] == "data-engineering")
    assert de["milestones"][0]["met"] and not de["milestones"][1]["met"]
    assert [s["id"] for s in plan["suggestions"]] == ["de-reliable-loads"]   # only next to what was shown
    p["excluded"].append(p["evidence"][0]["id"])
    plan = roadmap.roadmap(p, live_modules())
    assert not any(m["met"] for t in plan["tracks"] for m in t["milestones"]) and plan["suggestions"] == []


def test_a_finished_lesson_meets_its_milestone():
    lm05 = {**lab("LM-05", "keys", kind="module")}
    p = progress.empty()
    progress.add(p, progress.from_lab(passed(module=lm05, exercise="keys", kinds=("integrity",)), lm05))
    progress.add(p, progress.module_complete(p, lm05))
    plan = roadmap.roadmap(p, live_modules())
    sm = next(t for t in plan["tracks"] if t["id"] == "semantic-modeling")
    assert sm["milestones"][0]["met"] and sm["milestones"][0]["achievements"][0]["name"] == "The Architect's Eye"


def test_goals_milestone_or_own_words():
    p = progress.empty()
    roadmap.add_goal(p, milestone="sql-tuning")
    with pytest.raises(ValueError, match="already one of your goals"):
        roadmap.add_goal(p, milestone="sql-tuning")
    with pytest.raises(ValueError, match="isn't on the roadmap"):
        roadmap.add_goal(p, milestone="nope")
    own = roadmap.add_goal(p, text="  Present the   Gold layer design to the team ")
    assert own["text"] == "Present the Gold layer design to the team"
    with pytest.raises(ValueError, match="can't be ticked"):
        roadmap.mark_goal(p, "m-sql-tuning", True)
    roadmap.mark_goal(p, own["id"], True)
    plan = roadmap.roadmap(p, live_modules())
    by = {g["id"]: g for g in plan["goals"]}
    assert by["m-sql-tuning"]["met"] is False and by["m-sql-tuning"]["source"] == "milestone"
    assert by[own["id"]]["met"] is True and by[own["id"]]["source"] == "entered"
    assert next(m for t in plan["tracks"] for m in t["milestones"] if m["id"] == "sql-tuning")["goal"]
    roadmap.remove_goal(p, "m-sql-tuning")
    with pytest.raises(LookupError):
        roadmap.remove_goal(p, "m-sql-tuning")
    for i in range(roadmap.MAX_GOALS - 1):
        roadmap.add_goal(p, text=f"goal {i}")
    with pytest.raises(ValueError, match="up to"):
        roadmap.add_goal(p, text="one too many")


def test_a_hand_edited_goals_list_is_cleaned_on_load(tmp_path):
    path = tmp_path / "profile.json"
    p = progress.empty()
    p["goals"] = [{"id": "m-gone", "milestone": "gone"}, "junk", {"id": "x", "milestone": None, "text": ""},
                  {"id": "m-sql-tuning", "milestone": "sql-tuning", "text": "ignored", "done_at": "2026-01-01", "extra": 1},
                  {"id": "g-1", "milestone": None, "text": "Mine", "done_at": None}]
    progress.save(path, p)
    goals = progress.load(path)["goals"]
    assert [g["id"] for g in goals] == ["m-sql-tuning", "g-1"]
    assert goals[0] == {"id": "m-sql-tuning", "milestone": "sql-tuning", "text": "", "set_at": "", "done_at": None}


# ---- the report's development section


def test_the_report_lists_goals_with_their_source_and_no_game_terms():
    p = profile_with(validation())
    roadmap.add_goal(p, milestone="de-notebook")
    roadmap.add_goal(p, milestone="sql-tuning")
    roadmap.add_goal(p, text="Mentor a new *starter*")
    plan = roadmap.roadmap(p, live_modules())
    md = progress.report(p, plan=plan)["markdown"]
    section = md[md.index("## Development goals"):md.index("## Evidence appendix")]
    assert "Notebook development. Met: Validate a notebook with a clean run. *(Verified)*" in section
    assert "Query tuning. In progress: Finish LM\\-18 and LAB\\-02 \\(0 of 2\\). *(Goal set by you)*" in section
    assert "Mentor a new \\*starter\\*: in progress. *(Entered by you)*" in section
    assert "Loads that are safe to run again. *(Suggested next step, next to verified work)*" in section
    for word in ("XP", "Level", "achievement", "rank"):
        assert word not in section
    assert "## Development goals" not in progress.report(p)["markdown"]   # only when the service passes the plan


# ---- the PDF


def _objects_ok(pdf: bytes) -> None:
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.endswith(b"%%EOF\n")
    xref = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))
    assert pdf[xref:xref + 4] == b"xref"
    table = pdf[xref:].split(b"\n")
    count = int(table[1].split()[1])
    for i in range(1, count):
        offset = int(table[2 + i].split()[0])
        assert pdf[offset:].startswith(f"{i} 0 obj".encode()), f"object {i} isn't where the xref says"


def test_the_pdf_is_a_valid_text_only_file_and_the_same_text_gives_the_same_bytes():
    p = profile_with(validation(name="Orders (v2) \\ *x*"))
    md = progress.report(p, plan=roadmap.roadmap(p, live_modules()))["markdown"]
    pdf = pdfreport.render(md)
    _objects_ok(pdf)
    assert pdf == pdfreport.render(md)
    text = pdf.decode("latin-1")
    assert "Orders \\(v2\\) \\\\ *x*" in text            # PDF string escapes, the Markdown escapes resolved
    assert "/Helvetica-Oblique" in text and "(Verified, E1)" in text.replace("\\(", "(").replace("\\)", ")")
    for banned in ("/JavaScript", "/JS", "/OpenAction", "/Launch", "/URI", "/EmbeddedFile", "/FontFile"):
        assert banned not in text
    assert "XP" not in text


def test_a_long_report_runs_over_pages_with_numbered_footers():
    md = "# Report\n\n## Key contributions\n\n" + "\n".join(f"- Item {i} " + "word " * 40 for i in range(80))
    pdf = pdfreport.render(md)
    _objects_ok(pdf)
    n = int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", pdf).group(1))
    assert n > 3 and f"(Page {n} of {n})".encode() in pdf


def test_lines_fit_the_page_and_long_words_break():
    room = pdfreport.PAGE_W - 2 * pdfreport.MARGIN
    rows = pdfreport._wrap(pdfreport._runs("a" * 400 + " short words here", "F1"), pdfreport.BODY, room)
    assert len(rows) > 1
    for row in rows:
        assert sum(pdfreport.width(t, f, pdfreport.BODY) for t, f in row) <= room + 0.01


def test_characters_outside_the_font_print_as_a_question_mark():
    pdf = pdfreport.render("Data 狼 · café — ok")
    assert b"(Data ? \xb7 caf\xe9 \x97 ok)" in pdf


# ---- through the engine


def test_save_pdf_writes_a_new_file_in_downloads(plain):  # noqa: F811
    first = call(plain, "profile_report_pdf")["path"]
    second = call(plain, "profile_report_pdf", days=365)["path"]
    assert first.endswith("accomplishments.pdf") and second.endswith("accomplishments-2.pdf")
    with open(first, "rb") as f:
        _objects_ok(f.read())


def test_goals_through_the_engine_and_their_text_stays_out_of_the_audit_log(plain):  # noqa: F811
    view = call(plain, "profile_goal_add", milestone="gov-quality")
    assert [g["id"] for g in view["roadmap"]["goals"]] == ["m-gov-quality"]
    view = call(plain, "profile_goal_add", text="Learn Direct Lake tuning for the finance model")
    own = next(g for g in view["roadmap"]["goals"] if g["source"] == "entered")
    view = call(plain, "profile_goal_done", id=own["id"], done=True)
    assert next(g for g in view["roadmap"]["goals"] if g["id"] == own["id"])["met"]
    assert audit.target({"text": "Learn Direct Lake tuning for the finance model"}) == {"text": "(given)"}
    assert "profile_goal_add" not in audit.READ_ONLY and "profile_report_pdf" not in audit.READ_ONLY
    view = call(plain, "profile_goal_remove", id="m-gov-quality")
    assert [g["id"] for g in view["roadmap"]["goals"]] == [own["id"]]
    assert "impact" in view and view["impact"]["capacity"]["measured"] is False


def test_a_goal_needs_a_milestone_or_words_not_both(plain):  # noqa: F811
    from fabric_desktop.server import handle
    for params in ({}, {"milestone": "sql-tuning", "text": "x"}):
        out = handle(plain, json.dumps({"id": 1, "method": "profile_goal_add", "params": params}))
        assert "error" in out


def test_the_roadmap_shares_the_ranks_vocabulary():
    # A milestone's checks are worded and counted exactly as a rank's, so "met" means the same in both.
    need = roadmap.MILESTONES["sql-tuning"]["needs"][0]
    shipped = {m["id"] for m in live_modules()}
    check = ranks._check(need, ranks.facts(progress.empty()), shipped, {"exercises": 0, "labs": 0})
    assert check["text"] == "Finish LM-18 and LAB-02" and check["need"] == 2
