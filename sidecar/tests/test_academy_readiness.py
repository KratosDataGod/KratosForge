"""ACD-006: Academy evidence rows and readiness by objective. Rows keep their study guide version, measures
stay separate, Fabric-only objectives need FABRIC VERIFIED evidence, and the estimate is labelled as ours."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import progress
from fabric_desktop.academy import exercises, readiness, registry

PACK = "dp-700"


@pytest.fixture
def cert():
    return registry.certification(PACK)


def _passed(eid):
    ex = exercises.get(PACK, eid)
    return {"passed": True, "results": [{"kind": c["kind"], "passed": True} for c in ex["validation"]]}


def _row(eid, cert, at=None, **kw):
    row = progress.from_academy(_passed(eid), exercises.get(PACK, eid), cert, **kw)
    if at:
        row = {**row, "at": at, "id": row["id"] + at}
    return row


def _state(profile, oid):
    return next(x for x in readiness.readiness(profile, PACK)["objectives"] if x["id"] == oid)


def test_a_pass_records_its_objective_class_and_guide_version(cert):
    row = _row("E37", cert, hints_used=1)
    assert row["type"] == "academy.exercise" and row["rules_version"] == progress.RULES_VERSION >= 4
    d = row["detail"]
    assert d == {"certification": "dp-700", "certification_version": "2026-10-30", "objectives": ["2.2.9"],
                 "class": "local_verified", "mode": "guided", "hints_used": 1, "explanation_seen": False,
                 "checks": ["result_matches", "table_shape"]}
    assert "answer" not in str(row) and "silver_orders" not in str(row)


def test_no_row_for_a_fail_or_a_draft(cert):
    assert progress.from_academy({"passed": False, "results": [{}]}, exercises.get(PACK, "E37"), cert) is None
    draft = {**exercises.get(PACK, "E37"), "status": "draft"}
    assert progress.from_academy(_passed("E37"), draft, cert) is None


def test_passing_again_the_same_day_adds_nothing(cert):
    profile = progress.empty()
    assert progress.add(profile, _row("E37", cert))
    assert not progress.add(profile, _row("E37", cert))


def test_academy_rows_give_no_xp_yet(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))
    assert progress.progression(profile)["xp"] == 0


def test_a_local_pass_shows_a_portable_objective(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))
    x = _state(profile, "2.2.9")
    assert x["state"] == "shown" and x["best_class"] == "local_verified" and x["measures"]["guided"] == ["dp-700/E37"]
    assert not x["needs_platform"]
    assert _state(profile, "2.2.8")["state"] == "not_started"


def test_an_objective_that_needs_fabric_stays_incomplete_with_local_work(cert):
    ex = {**exercises.get(PACK, "E21"), "objectives": ["2.3.3"]}     # pretend a local pass for a Fabric objective
    profile = progress.empty()
    progress.add(profile, progress.from_academy(_passed("E21"), ex, cert))
    x = _state(profile, "2.3.3")
    assert x["state"] == "practised" and x["needs_platform"] and x["gaps"]


def test_measures_stay_separate(cert):
    profile = progress.empty()
    challenge = {**exercises.get(PACK, "E36"), "mode": "challenge"}
    progress.add(profile, progress.from_academy(_passed("E36"), challenge, cert, hints_used=0))
    trouble = {**exercises.get(PACK, "E37"), "mode": "troubleshoot"}
    progress.add(profile, progress.from_academy(_passed("E37"), trouble, cert, hints_used=3))
    assert _state(profile, "2.2.8")["measures"]["independent"] == ["dp-700/E36"]
    m = _state(profile, "2.2.9")["measures"]
    assert m["troubleshooting"] == ["dp-700/E37"] and m["independent"] == [] and m["guided"] == []


def test_hints_the_explanation_or_changed_answers_make_it_not_independent(cert):
    challenge = {**exercises.get(PACK, "E36"), "mode": "challenge"}
    for kw in ({"hints_used": 2}, {"explanation_seen": True}, {"answers_changed": True}):
        profile = progress.empty()
        progress.add(profile, progress.from_academy(_passed("E36"), challenge, cert, **kw))
        assert _state(profile, "2.2.8")["measures"]["independent"] == []


def test_retention_needs_a_pass_a_week_later(cert):
    profile = progress.empty()
    first = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    progress.add(profile, _row("E37", cert, at=first.isoformat()))
    progress.add(profile, _row("E37", cert, at=(first + dt.timedelta(days=3)).isoformat()))
    assert _state(profile, "2.2.9")["measures"]["retention"] == []
    progress.add(profile, _row("E37", cert, at=(first + dt.timedelta(days=8)).isoformat()))
    assert _state(profile, "2.2.9")["measures"]["retention"] == ["dp-700/E37"]


def test_excluding_a_row_takes_it_out(cert):
    profile = progress.empty()
    row = _row("E37", cert)
    progress.add(profile, row)
    profile["excluded"].append(row["id"])
    assert _state(profile, "2.2.9")["state"] == "not_started"


def test_rows_from_another_guide_version_are_kept_but_not_rewritten(cert, monkeypatch):
    profile = progress.empty()
    row = _row("E37", cert)
    row["detail"]["certification_version"] = "2025-01-01"
    progress.add(profile, row)
    out = readiness.readiness(profile, PACK)
    assert out["earlier_version_rows"] == 1 and _state(profile, "2.2.9")["state"] == "not_started"
    assert row["detail"]["certification_version"] == "2025-01-01"
    walked = {**cert, "crosswalk": {"2025-01-01": {"2.2.9": ["2.2.9"]}}}
    monkeypatch.setattr(registry, "certification", lambda pack_id, root=registry.ROOT: walked)
    assert _state(profile, "2.2.9")["state"] == "shown"


def test_knowledge_comes_from_assessments(cert):
    profile = progress.empty()
    profile["evidence"].append({"id": "a1", "type": "academy.assessment", "status": "verified", "at": "2026-11-03T10:00:00+00:00",
                                "artifact": {"name": "dp-700/diag-2"}, "ref": {"id": "dp-700/diag-2"},
                                "detail": {"certification": PACK, "certification_version": cert["version"],
                                           "objectives": ["2.2.8"], "per_objective": {"2.2.8": {"correct": 3, "answered": 4}}}})
    x = _state(profile, "2.2.8")
    assert x["measures"]["knowledge"] == {"correct": 3, "answered": 4}
    assert x["state"] == "practised"      # knowledge alone never shows a practical objective


def test_knowledge_adds_partial_credit_from_multi_part_questions(cert):
    # A matching question with 3 of 4 parts right earns 0.75; two exams' points add without losing the fraction.
    profile = progress.empty()
    for n, correct in ((1, 1.75), (2, 0.5)):
        profile["evidence"].append({"id": f"a{n}", "type": "academy.assessment", "status": "verified",
                                    "at": f"2026-11-0{n}T10:00:00+00:00", "artifact": {"name": f"dp-700/x{n}"}, "ref": {"id": f"dp-700/x{n}"},
                                    "detail": {"certification": PACK, "certification_version": cert["version"], "objectives": ["2.2.1"],
                                               "per_objective": {"2.2.1": {"correct": correct, "answered": 2}}}})
    assert _state(profile, "2.2.1")["measures"]["knowledge"] == {"correct": 2.25, "answered": 4}


def test_the_estimate_is_labelled_and_suggests_what_is_next(cert):
    out = readiness.readiness(progress.empty(), PACK)
    assert out["label"] == "KratosForge readiness estimate" and "isn't Microsoft's score" in out["note"]
    assert [d["objectives"] for d in out["domains"]] == [16, 17, 16]
    assert [d["need_platform"] for d in out["domains"]] == [14, 9, 13]
    assert {n["exercise"] for n in out["next"]} == {"E21", "E36", "E37"}
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))
    assert "E37" not in {n["exercise"] for n in readiness.readiness(profile, PACK)["next"]}


# ---------- ACD-015: the Readiness dashboard's summary and its exported report ----------

def _assessed(profile, cert, oid, correct, answered):
    row = {"id": f"a-{oid}-{correct}-{answered}", "type": "academy.assessment", "at": "2026-11-20T10:00:00Z", "status": "verified",
           "detail": {"certification": PACK, "certification_version": cert["version"], "objectives": [oid],
                      "per_objective": {oid: {"correct": correct, "answered": answered}}}}
    profile["evidence"].append(row)


def test_the_dashboard_counts_each_measure_by_domain_with_fixed_rules(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))          # 2.2.9, guided, local verified
    _assessed(profile, cert, "2.2.9", 3, 4)           # 75% of 4: Knowledge met
    _assessed(profile, cert, "2.1.1", 1, 4)           # under half: a weakness
    _assessed(profile, cert, "1.1.1", 1, 1)           # one answered: not enough for Knowledge
    r = readiness.readiness(profile, PACK)
    dash = r["dashboard"]
    two = next(d for d in dash["domains"] if d["id"] == "2")["measures"]
    assert two["knowledge"] == {"met": 1, "of": sum(o["domain"] == "2" for o in r["objectives"])}
    assert two["guided"]["met"] == 1 and two["independent"]["met"] == 0
    assert two["platform_verified"]["of"] == sum(o["domain"] == "2" and o["platform_required"] for o in r["objectives"])
    one = next(d for d in dash["domains"] if d["id"] == "1")["measures"]
    assert one["knowledge"]["met"] == 0
    assert dash["strengths"][0]["id"] == "2.2.9"
    assert dash["weaknesses"][0] == {"id": "2.1.1", "text": _state(profile, "2.1.1")["text"], "domain": "2",
                                     "why": "1 of 4 question points right"}
    assert dash["todo"]["objectives"] == sum(o["state"] == "not_started" for o in r["objectives"])
    assert sum(dash["todo"]["by_domain"].values()) == dash["todo"]["objectives"]


def test_the_report_is_labelled_an_estimate_and_page_text_cant_break_its_tables(cert):
    profile = progress.empty()
    progress.add(profile, _row("E37", cert))
    r = readiness.readiness(profile, PACK)
    r["objectives"][0]["text"] = "a | b\nc"
    text = readiness.report(r, "Implementing data engineering", "2026-11-26")
    assert text.startswith("# Implementing data engineering: KratosForge readiness estimate")
    assert r["note"] in text and "Exported 2026-11-26. Study guide of 2026-10-30." in text
    assert "| 2.2.9 " in text and "E37" in text and "a / b c" in text
    assert "## Strengths" in text and "## Weaknesses" in text and "not needed" in text


def test_export_writes_a_new_file_in_downloads_and_never_replaces_one(tmp_path, monkeypatch):
    from fabric_desktop import known_folders
    from fabric_desktop.academy_service import AcademyMixin

    class Svc(AcademyMixin):
        root = tmp_path

        def _profile_path(self):
            return tmp_path / "profile.json"

    monkeypatch.setattr(known_folders, "export_folder", lambda: tmp_path / "Downloads")
    first, second = Svc().academy_readiness_export(PACK), Svc().academy_readiness_export(PACK)
    assert first["path"] != second["path"] and second["path"].endswith(").md")
    assert "KratosForge readiness estimate" in open(first["path"], encoding="utf-8").read()
