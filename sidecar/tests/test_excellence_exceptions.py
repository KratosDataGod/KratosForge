"""Accepted risk and exceptions (FEA-404): strictly checked, shown on what they match, and never a
change to any outcome, score, assured score, coverage, status or Critical Risk flag."""

from __future__ import annotations

import copy
import datetime as dt
import json
import random

import pytest

from fabric_desktop import server
from fabric_desktop.excellence import assess, catalog, exceptions as exc, profiles as pf
from fabric_desktop.service import Service

TODAY = dt.date(2026, 11, 20)
WS = "f1f1f1f1-0000-4000-8000-000000000001"   # the demo workspace
CAP = "f1f1f1f1-0000-4000-8000-0000000000ca"  # the demo capacity
NB1 = "f1f1f1f1-0000-4000-8000-000000000101"  # two demo notebooks that fail FEA-DE-001
NB2 = "f1f1f1f1-0000-4000-8000-000000000102"
REG = catalog.registry()


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return Service(root=tmp_path_factory.mktemp("fea404") / "app").excellence_assess(demo=True)


def ex(**kw) -> dict:
    base = {"control_id": "FEA-SEC-003", "subject": WS, "owner": "Data platform lead",
            "reason": "The partner contract ends in March.", "expires": "2027-03-31", "recorded_at": "2026-11-15"}
    return {k: v for k, v in {**base, **kw}.items() if v is not ...}


def ok(*items: dict) -> list[dict]:
    return exc.validate(list(items), REG, today=TODAY)


def strip(result: dict) -> dict:
    """The result with every exception annotation taken off."""
    r = copy.deepcopy(result)
    r.pop("exceptions", None)
    for x in (*r["outcomes"], *r["findings"]):
        x.pop("accepted_risk", None)
    return r


def same(a: dict, b: dict) -> bool:
    return json.dumps(strip(a), sort_keys=True) == json.dumps(strip(b), sort_keys=True)


def failed(r: dict) -> list[dict]:
    return [o for o in r["outcomes"] if o["outcome"] == "fail" or (o.get("advisory") or {}).get("outcome") == "fail"]


# ---- validation


def test_a_good_exception_is_normalized():
    got = ok(ex(owner="  Data platform lead  ", recorded_at=...))
    assert got == [{"control_id": "FEA-SEC-003", "subject": WS, "owner": "Data platform lead",
                    "reason": "The partner contract ends in March.", "expires": "2027-03-31",
                    "recorded_at": "2026-11-20"}]
    assert ok(ex(subject="*"))[0]["subject"] == "*"
    assert exc.validate(None, REG, today=TODAY) == [] and exc.validate([], REG, today=TODAY) == []


@pytest.mark.parametrize("raw, message", [
    ("nope", "exceptions must be a list"),
    ({"control_id": "FEA-SEC-003"}, "exceptions must be a list"),
    (["nope"], "exception 1: must be an object"),
    ([ex(color="red")], "exception 1: unknown field color"),
    ([ex(owner=...)], "exception 1: missing owner"),
    ([ex(reason=None)], "exception 1: missing reason"),
    ([ex(expires=...)], "exception 1: missing expires"),
    ([ex(control_id=...)], "exception 1: missing control_id"),
    ([ex(subject=...)], "exception 1: missing subject"),
    ([ex(owner="")], "exception 1: owner is empty"),
    ([ex(owner="   ")], "exception 1: owner is empty"),
    ([ex(reason="")], "exception 1: reason is empty"),
    ([ex(subject="")], "exception 1: subject is empty"),
    ([ex(owner=7)], "exception 1: owner must be text"),
    ([ex(owner="x" * 257)], "exception 1: owner is longer than 256"),
    ([ex(reason="x" * 257)], "exception 1: reason is longer than 256"),
    ([ex(subject="x" * 257)], "exception 1: subject is longer than 256"),
    ([ex(reason="ok\x00then")], "exception 1: reason has a control character"),
    ([ex(owner="Ann\nBob")], "exception 1: owner has a control character"),
    ([ex(reason="tab\there")], "exception 1: reason has a control character"),
    ([ex(subject="ws\x1b[31m")], "exception 1: subject has a control character"),
    ([ex(control_id="FEA-XXX-999")], "exception 1: unknown control FEA-XXX-999"),
    ([ex(expires="2027/03/31")], "exception 1: expires must be a date"),
    ([ex(expires="20270331")], "exception 1: expires must be a date"),
    ([ex(expires="2027-03-31T00:00:00")], "exception 1: expires must be a date"),
    ([ex(expires="2027-02-30")], "exception 1: expires isn't a real date"),
    ([ex(expires=20270331)], "exception 1: expires must be a date"),
    ([ex(recorded_at="yesterday")], "exception 1: recorded_at must be a date"),
    ([ex(recorded_at=..., expires="2026-11-19")], "exception 1: expires must be after"),
    ([ex(recorded_at=..., expires="2026-11-20")], "exception 1: expires must be after"),
    ([ex(expires="2026-11-15")], "exception 1: expires must be after"),
    ([ex(recorded_at=..., expires="2027-11-21")], "exception 1: expires more than 365 days"),
    ([ex(recorded_at="2026-01-01", expires="2027-01-02")], "exception 1: expires more than 365 days"),
    ([ex(recorded_at="2026-11-21")], "exception 1: recorded_at is in the future"),
    ([ex(), ex(subject=WS.upper(), owner="Someone else")], "exception 2: another exception already covers"),
    ([ex()] * (exc.MAX_EXCEPTIONS + 1), "at most 1000 exceptions"),
])
def test_each_bad_exception_is_refused_with_its_problem(raw, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(")):
        exc.validate(raw, REG, today=TODAY)


def test_the_limits_themselves_are_allowed():
    assert ok(ex(owner="x" * 256, reason="y" * 256))
    assert ok(ex(recorded_at=..., expires="2027-11-20"))  # exactly 365 days
    assert ok(ex(recorded_at=..., expires="2026-11-21"))  # tomorrow
    assert ok(ex(reason="Café, naïve, 東京"))  # text, not just ASCII
    assert ok(ex(), ex(subject="*"))  # a resource and every resource of the control are different exceptions


# ---- matching, expiry, unmatched


def test_a_named_resource_is_annotated_and_nothing_else(demo):
    r = exc.apply(demo, ok(ex()))
    note = {"owner": "Data platform lead", "reason": "The partner contract ends in March.",
            "expires": "2027-03-31", "expired": False}
    hit = [o for o in r["outcomes"] if "accepted_risk" in o]
    assert [(o["control_id"], o["subject"]["id"]) for o in hit] == [("FEA-SEC-003", WS)]
    assert hit[0]["accepted_risk"] == note and hit[0]["outcome"] == "fail"
    f = [f for f in r["findings"] if "accepted_risk" in f]
    assert len(f) == 1 and f[0]["rule"].startswith("FEA-SEC-003@") and f[0]["accepted_risk"] == note
    assert f[0]["status"] == "open"
    assert r["exceptions"]["active"] == 1 and r["exceptions"]["expired"] == 0 and r["exceptions"]["unmatched"] == []
    assert "accepted_risk" not in json.dumps(demo)  # apply copies; the input is untouched


def test_star_covers_every_failing_resource_of_that_control(demo):
    r = exc.apply(demo, ok(ex(control_id="FEA-DE-001", subject="*")))
    hit = sorted(o["subject"]["id"] for o in r["outcomes"] if "accepted_risk" in o)
    want = sorted(o["subject"]["id"] for o in failed(demo) if o["control_id"] == "FEA-DE-001")
    assert hit == want and len(want) == 3 and NB1 in hit
    assert sum("accepted_risk" in f for f in r["findings"]) == 3
    assert r["exceptions"]["active"] == 1


def test_a_named_resource_wins_over_star(demo):
    r = exc.apply(demo, ok(ex(control_id="FEA-DE-001", subject="*", owner="Everyone"),
                           ex(control_id="FEA-DE-001", subject=NB1, owner="Notebook owner")))
    owners = {o["subject"]["id"]: o["accepted_risk"]["owner"] for o in r["outcomes"] if "accepted_risk" in o}
    assert owners[NB1] == "Notebook owner" and owners[NB2] == "Everyone"
    assert r["exceptions"]["active"] == 2


def test_subjects_match_without_regard_to_case(demo):
    r = exc.apply(demo, ok(ex(subject=CAP.upper(), control_id="FEA-CAP-003")))
    assert [o["subject"]["id"] for o in r["outcomes"] if "accepted_risk" in o] == [CAP]


def test_passing_and_unevaluated_outcomes_are_never_annotated(demo):
    others = [o for o in demo["outcomes"] if o not in failed(demo)]
    every = ok(*[ex(control_id=c, subject="*") for c in sorted({o["control_id"] for o in demo["outcomes"]})])
    r = exc.apply(demo, every)
    assert all("accepted_risk" in o for o in failed(r))
    assert not any("accepted_risk" in o for o in r["outcomes"] if o["outcome"] != "fail"
                   and (o.get("advisory") or {}).get("outcome") != "fail")
    assert others  # the demo has passes and gaps to prove it on
    assert {c for c in (e["control_id"] for e in every)} - {o["control_id"] for o in failed(demo)} == {
        u["control_id"] for u in r["exceptions"]["unmatched"]}


def test_expired_is_shown_as_expired_and_not_active(demo):
    # Recorded in the spring, expired on 2026-10-01; the demo was assessed on 2026-11-01.
    old = ok(ex(recorded_at="2026-04-01", expires="2026-10-01"))
    r = exc.apply(demo, old)
    o = next(o for o in r["outcomes"] if "accepted_risk" in o)
    assert o["accepted_risk"]["expired"] is True and o["outcome"] == "fail"
    assert next(f for f in r["findings"] if "accepted_risk" in f)["accepted_risk"]["expired"] is True
    assert r["exceptions"]["active"] == 0 and r["exceptions"]["expired"] == 1 and r["exceptions"]["unmatched"] == []


def test_an_exception_expires_on_its_expiry_date(demo):
    on_the_day = exc.apply(demo, ok(ex(recorded_at="2026-06-01", expires="2026-11-01")))
    day_before = exc.apply(demo, ok(ex(recorded_at="2026-06-01", expires="2026-11-02")))
    assert on_the_day["exceptions"]["expired"] == 1 and day_before["exceptions"]["active"] == 1


def test_an_active_exception_wins_over_an_expired_one(demo):
    r = exc.apply(demo, ok(ex(subject="*", recorded_at="2026-04-01", expires="2026-10-01", owner="Old"),
                           ex(owner="New")))
    o = next(o for o in r["outcomes"] if "accepted_risk" in o)
    assert o["accepted_risk"]["owner"] == "New" and o["accepted_risk"]["expired"] is False
    assert r["exceptions"]["active"] == 1 and r["exceptions"]["expired"] == 1


def test_unmatched_lists_what_matched_no_failure(demo):
    r = exc.apply(demo, ok(ex(subject="no-such-resource"), ex(control_id="FEA-CAP-001", subject="*"),
                           ex(control_id="FEA-DE-001", subject=NB1)))
    assert r["exceptions"]["unmatched"] == [{"control_id": "FEA-SEC-003", "subject": "no-such-resource"},
                                            {"control_id": "FEA-CAP-001", "subject": "*"}]
    assert r["exceptions"]["active"] == 1
    assert len(r["exceptions"]["records"]) == 3


def test_applying_twice_reads_the_same_as_once(demo):
    once = exc.apply(demo, ok(ex()))
    assert exc.apply(once, ok(ex())) == once
    assert strip(exc.apply(once, [])) == strip(demo) and exc.apply(once, [])["exceptions"]["active"] == 0


def test_a_result_without_exceptions_says_none(demo):
    assert demo["exceptions"] == {"active": 0, "expired": 0, "unmatched": [], "records": []}
    assert not any("accepted_risk" in x for x in (*demo["outcomes"], *demo["findings"]))


# ---- never a change to the outcome or the score


def _scored(r: dict) -> str:
    keep = ("scores", "workspace_scores", "capacity_scores", "counts", "next_actions", "item_coverage",
            "policy_version", "fingerprint")
    return json.dumps({k: r[k] for k in keep}, sort_keys=True)


def test_scores_are_byte_identical_with_and_without_exceptions(demo):
    every = ok(*[ex(control_id=c, subject="*") for c in sorted({o["control_id"] for o in demo["outcomes"]})])
    r = exc.apply(demo, every)
    assert _scored(r) == _scored(demo)
    assert [(o["outcome"], o["basis"]) for o in r["outcomes"]] == [(o["outcome"], o["basis"]) for o in demo["outcomes"]]
    assert [(f["id"], f["status"], f["severity"]) for f in r["findings"]] == [
        (f["id"], f["status"], f["severity"]) for f in demo["findings"]]
    assert same(r, demo)


def test_a_critical_risk_stays_a_critical_risk(demo):
    critical = pf.Profile("crit", "1.0.0", "Critical security", dict(pf.get().weights),
                          severity_overrides={"FEA-SEC-003": "critical"})
    base = assess.rescore(demo, REG, critical, today=TODAY)
    assert base["scores"]["critical_risk"] is True and base["scores"]["critical_controls"] == ["FEA-SEC-003"]
    r = assess.rescore({**demo, "exceptions": {"records": ok(ex())}}, REG, critical, today=TODAY)
    assert r["scores"]["critical_risk"] is True and _scored(r) == _scored(base)
    assert next(o for o in r["outcomes"] if o["control_id"] == "FEA-SEC-003")["accepted_risk"]["expired"] is False


def _random_profile(rnd: random.Random, ids: list[str]) -> pf.Profile:
    dims = list(pf.get().weights)
    cuts = sorted(rnd.sample(range(1, 100), len(dims) - 1))
    weights = {d: float(b - a) for d, a, b in zip(dims, [0, *cuts], [*cuts, 100], strict=True)}
    sev = {c: rnd.choice(("critical", "high", "medium", "low", "informational")) for c in rnd.sample(ids, 5)}
    off = {c: "Not used here." for c in rnd.sample(ids, rnd.randint(0, 3))}
    return pf.Profile("gen", "1.0.0", "Generated", weights, severity_overrides=sev, disabled=off,
                      rated_coverage=0.8, provisional_coverage=0.5)


def _random_exceptions(rnd: random.Random, r: dict, ids: list[str]) -> list[dict]:
    subjects = sorted({o["subject"]["id"] for o in r["outcomes"]}) + ["*", "nothing-here"]
    pairs = sorted({(rnd.choice(ids), rnd.choice(subjects)) for _ in range(rnd.randint(0, 25))})
    pairs += sorted({(o["control_id"], o["subject"]["id"]) for o in failed(r) if rnd.random() < 0.5} - set(pairs))
    out = []
    for i, (cid, subj) in enumerate(pairs):
        recorded = TODAY - dt.timedelta(days=rnd.randint(0, 300))
        expires = recorded + dt.timedelta(days=rnd.randint(1, 365))
        out.append({"control_id": cid, "subject": subj, "owner": f"owner {i}", "reason": f"reason {i}",
                    "expires": expires.isoformat(), "recorded_at": recorded.isoformat()})
    return ok(*out)


@pytest.mark.parametrize("seed", range(40))
def test_property_exceptions_never_change_what_was_assessed(demo, seed):
    """Many results from the demo snapshot: a random share of its evidence, a random profile, random
    exceptions. With or without them, everything but the annotations is byte for byte the same."""
    rnd = random.Random(seed)
    ids = sorted(REG.versions())
    snap = copy.deepcopy(demo)
    snap["evidence"] = [e for e in snap["evidence"] if rnd.random() < 0.85]
    prof = _random_profile(rnd, ids) if seed % 4 else pf.get(sorted(pf.BUILT_IN)[seed // 4 % len(pf.BUILT_IN)])
    plain = assess.rescore(snap, REG, prof, today=TODAY)
    records = _random_exceptions(rnd, plain, ids)
    with_ = assess.rescore({**snap, "exceptions": {"records": records}}, REG, prof, today=TODAY)
    assert _scored(with_) == _scored(plain)
    assert same(with_, plain) and same(exc.apply(plain, records), plain)
    assert exc.apply(plain, records) == with_
    noted = [o for o in with_["outcomes"] if "accepted_risk" in o]
    assert all(o in failed(with_) for o in noted)
    assert noted or not failed(plain)  # the seeds do exercise annotations
    n = with_["exceptions"]
    assert n["expired"] + n["active"] + len(n["unmatched"]) <= len(records)
    assert n["records"] == records


# ---- rescoring


def test_rescoring_keeps_the_annotations_and_the_scores(demo):
    records = ok(ex(), ex(control_id="FEA-DE-001", subject="*"), ex(subject="gone"),
                 ex(control_id="FEA-CAP-003", subject=CAP, recorded_at="2026-04-01", expires="2026-10-01"))
    first = assess.rescore({**demo, "exceptions": {"records": records}}, REG, today=TODAY)
    again = assess.rescore(json.loads(json.dumps(first)), REG, today=TODAY)
    assert json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True)
    assert _scored(first) == _scored(demo)
    assert first["exceptions"]["active"] == 2 and first["exceptions"]["expired"] == 1
    assert first["exceptions"]["unmatched"] == [{"control_id": "FEA-SEC-003", "subject": "gone"}]


def test_rescoring_a_snapshot_from_before_exceptions_still_works(demo):
    old = {k: v for k, v in demo.items() if k != "exceptions"}
    assert assess.rescore(old, REG, today=TODAY)["exceptions"]["records"] == []


def test_rescoring_refuses_a_tampered_exception(demo):
    bad = {**demo, "exceptions": {"records": [ex(owner="")]}}
    with pytest.raises(ValueError, match="owner is empty"):
        assess.rescore(bad, REG, today=TODAY)


# ---- the engine call


def test_the_call_takes_exceptions(tmp_path):
    s = Service(root=tmp_path / "app")
    plain = s.excellence_assess(demo=True)
    today = dt.datetime.now(dt.timezone.utc).date()
    raw = [{"control_id": "FEA-SEC-003", "subject": WS, "owner": "Lead", "reason": "Partner access until March.",
            "expires": (today + dt.timedelta(days=90)).isoformat()}]
    r = s.excellence_assess(demo=True, exceptions=raw)
    assert r["exceptions"]["active"] == 1 and r["exceptions"]["records"][0]["recorded_at"] == today.isoformat()
    assert _scored(r) == _scored(plain) and same(r, plain)


def test_a_bad_exception_stops_the_call_before_anything_is_read(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    called = []
    monkeypatch.setattr(Service, "_optimization_assess", lambda self, **kw: called.append(kw))
    with pytest.raises(ValueError, match="exception 1: owner is empty"):
        s.excellence_assess(demo=True, exceptions=[ex(owner="")])
    with pytest.raises(ValueError, match="exception 1: expires must be after"):
        s.excellence_assess(demo=True, exceptions=[ex(recorded_at=..., expires="2020-01-01")])
    assert called == []


def test_the_param_comes_over_rpc_and_is_type_checked(tmp_path):
    s = Service(root=tmp_path / "app")
    today = dt.datetime.now(dt.timezone.utc).date()
    good = [{"control_id": "FEA-SEC-003", "subject": "*", "owner": "Lead", "reason": "Accepted for now.",
             "expires": (today + dt.timedelta(days=30)).isoformat()}]
    reply = server.handle(s, json.dumps({"id": 1, "method": "excellence_assess",
                                         "params": {"demo": True, "exceptions": good}}))
    assert reply["result"]["exceptions"]["active"] == 1
    reply = server.handle(s, json.dumps({"id": 2, "method": "excellence_assess",
                                         "params": {"demo": True, "exceptions": {"a": 1}}}))
    assert reply["error"].startswith("TypeError: excellence_assess: exceptions")
    reply = server.handle(s, json.dumps({"id": 3, "method": "excellence_assess",
                                         "params": {"demo": True, "exceptions": [{"control_id": "nope"}]}}))
    assert "exception 1" in reply["error"] and "result" not in reply


# ---- invisible and malformed characters


@pytest.mark.parametrize("field, value", [
    ("subject", WS + "\u200b"),          # zero-width space: reads as the workspace, matches nothing
    ("subject", "\u2060" + WS),          # word joiner
    ("subject", "\ufeff*"),              # byte order mark before the wildcard
    ("owner", "\u202eLead"),             # right-to-left override: the owner reads reversed
    ("reason", "ok\u2066hidden\u2069"),  # bidi isolates
    ("subject", "\ud800"),               # lone surrogate: not text, and can't be written as UTF-8
    ("owner", "x\udfff"),
    ("reason", "\ue000private"),         # private use
    ("reason", "a\u2028b"),              # line separator
])
def test_invisible_or_malformed_characters_are_refused(field, value):
    with pytest.raises(ValueError, match=f"exception 1: {field} has a control character"):
        ok(ex(**{field: value}))


def test_rescoring_a_snapshot_with_malformed_exceptions_is_a_value_error(demo):
    for bad in ("records", ["x"], 7):
        with pytest.raises(ValueError, match="exceptions"):
            assess.rescore({**demo, "exceptions": bad}, REG, today=TODAY)
