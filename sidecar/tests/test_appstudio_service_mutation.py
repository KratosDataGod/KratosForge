"""App Studio's service (appstudio_service.py), held to its exact behaviour: the project-name refusal, the component
types Design offers, the home list's newest-first order, the description limit at exactly its size, the plan
store's size and its hour (a plan an hour old to the second is still kept, a second more and it's gone), consent
checked before the semantic model is read, the model's name passed when publishing reads it, and a failed
publish's message. The model and Fabric are the stand-ins of the main tests; the clock is a fake."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import appstudio_service
from fabric_desktop.appstudio import project
from fabric_desktop.appstudio import providers as prov
from fabric_desktop.appstudio import spec as specs
from test_appstudio_edit import svc  # noqa: F401 - svc is a fixture
from test_appstudio_generate import ITEM, WS
from test_appstudio_publish import REPORT, _Svc as _PublishSvc
from test_appstudio_publish import svc as pub  # noqa: F401 - pub is a fixture

SOURCE = {"workspace_id": WS, "item_id": ITEM, "name": "Sales model"}
RENAME = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
HOUR = 60 * 60


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(appstudio_service, "time", c)
    return c


# ---------------------------------------------------------------- projects


def test_a_name_that_isnt_a_project_folder_is_refused_as_such(svc):  # noqa: F811
    for bad in ("Sales overview", "Sales/../Other.KFApp"):
        with pytest.raises(ValueError, match=r"^Choose a project from the list\.$"):
            svc.appstudio_open(bad)


def test_an_app_without_a_name_is_refused(svc):  # noqa: F811
    with pytest.raises(ValueError, match=r"^Give the app a name\.$"):
        svc.appstudio_create("  \n ")


def test_design_offers_the_components_of_the_apps_target(svc):  # noqa: F811
    opened = svc.appstudio_open(svc.project)
    expected = sorted(specs.TARGETS[opened["spec"]["target"]]["components"])
    assert expected and opened["components"] == expected


def test_home_lists_the_newest_change_first(svc):  # noqa: F811
    other = svc.appstudio_create("Second app")["project"]
    root = svc._appstudio_root()
    for name, when in ((svc.project, "2026-01-01T00:00:00Z"), (other, "2026-06-01T00:00:00Z")):
        meta = json.loads((root / name / project.META).read_text(encoding="utf-8"))
        meta["modified"] = when
        (root / name / project.META).write_text(json.dumps(meta), encoding="utf-8")
    assert [r["project"] for r in svc.appstudio_home()["projects"]] == [other, svc.project]


def test_a_preview_of_an_app_that_no_longer_validates_says_why(svc):  # noqa: F811
    (svc._appstudio_root() / svc.project / project.SPEC).write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(specs.SpecError):
        svc.appstudio_preview(svc.project)


def test_a_preview_of_an_app_holding_a_secret_is_refused(svc):  # noqa: F811
    path = svc._appstudio_root() / svc.project / project.SPEC
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["pages"][0]["components"][0]["title"] = "Bearer abc.def.ghi"  # pragma: allowlist secret (test value)
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert specs.validate(spec) == [], "only the secret check stops it"
    with pytest.raises(specs.SpecError, match="Projects never keep secrets"):
        svc.appstudio_preview(svc.project)


def test_a_semantic_model_name_is_one_line_of_at_most_120_characters():
    assert appstudio_service.redact_name("a" * 200) == "a" * 119 + "…"
    assert appstudio_service.redact_name("b" * 120) == "b" * 120
    assert appstudio_service.redact_name(None) == ""


# ---------------------------------------------------------------- plans and changes


def test_a_description_of_exactly_the_limit_is_accepted(svc):  # noqa: F811
    text = "x" * appstudio_service.MAX_DESCRIPTION
    assert svc.appstudio_plan(text, source=SOURCE)["sent"]["description_chars"] == appstudio_service.MAX_DESCRIPTION
    svc.reply = RENAME
    assert svc.appstudio_change_plan(svc.project, text)["sent"]["request_chars"] == appstudio_service.MAX_DESCRIPTION


def test_twenty_plans_are_kept_and_the_oldest_makes_room(svc, clock):  # noqa: F811
    plans = svc._appstudio_plan_store()
    plans.update({f"p{i}": {"at": clock.now - 100 + i} for i in range(20)})
    got = svc.appstudio_plan("Sales", source=SOURCE)
    assert len(plans) == 20 and "p0" not in plans and "p1" in plans and got["plan_id"] in plans


def test_a_plan_an_hour_old_is_kept_and_a_second_older_is_dropped(svc, clock):  # noqa: F811
    plans = svc._appstudio_plan_store()
    plans.update({"edge": {"at": clock.now - HOUR}, "old": {"at": clock.now - HOUR - 1}})
    svc.appstudio_plan("Sales", source=SOURCE)
    assert "edge" in plans and "old" not in plans


def test_a_change_an_hour_old_is_kept_and_a_second_older_is_dropped(svc, clock):  # noqa: F811
    plans = svc._appstudio_plan_store()
    plans.update({"edge": {"at": clock.now - HOUR}, "old": {"at": clock.now - HOUR - 1}})
    svc.reply = RENAME
    svc.appstudio_change_plan(svc.project, "rename")
    assert "edge" in plans and "old" not in plans


def test_a_plan_can_be_approved_within_the_hour_and_not_after(svc, clock):  # noqa: F811
    start = clock.now
    first = svc.appstudio_plan("Sales", source=SOURCE)
    clock.now = start + HOUR - 1
    assert svc.appstudio_create_from_plan(first["plan_id"], first["plan"], name="Within the hour")["project"]
    second = svc.appstudio_plan("Sales", source=SOURCE)
    clock.now += HOUR + 1
    with pytest.raises(ValueError, match="^That plan has expired"):
        svc.appstudio_create_from_plan(second["plan_id"], second["plan"], name="Too late")


def test_a_change_can_be_applied_within_the_hour_and_not_after(svc, clock):  # noqa: F811
    svc.reply = RENAME
    start = clock.now
    first = svc.appstudio_change_plan(svc.project, "rename")
    clock.now = start + HOUR - 1
    assert svc.appstudio_apply_change(svc.project, first["change_id"])["spec"]["pages"][0]["components"][0]["title"] == "Total"
    second = svc.appstudio_change_plan(svc.project, "rename")
    clock.now += HOUR + 1
    with pytest.raises(ValueError, match="^That change has expired"):
        svc.appstudio_apply_change(svc.project, second["change_id"])


def test_a_change_without_consent_is_refused_before_the_model_is_read(svc):  # noqa: F811
    conn = svc._appstudio_model("")
    svc.appstudio_connection_save({"id": conn["id"], "name": "Contoso", "provider": "openai-compatible",
                                   "model": "m1", "endpoint": "https://llm.contoso.com/v1",
                                   "key_ref": "credman:KratosForge/c", "consent": {"context": False, "sample_rows": False}})
    svc.bim_reads.clear()
    with pytest.raises(prov.ConsentError, match="^Agree to send application context"):
        svc.appstudio_change_plan(svc.project, "rename")
    assert svc.bim_reads == [] and svc.sent == []


# ---------------------------------------------------------------- publish


def publish(pub, check, **kw):  # noqa: F811
    args = {"fingerprint": check["fingerprint"], "confirm_name": "Sales dev", "workspace_id": check["workspace_id"]}
    return pub.appstudio_publish(pub.project, **{**args, **kw})


def test_publishing_reads_the_model_under_its_name(pub, monkeypatch):  # noqa: F811
    seen = []
    real = _PublishSvc._model_bim
    monkeypatch.setattr(_PublishSvc, "_model_bim", lambda self, ws, model: seen.append((ws, model)) or real(self, ws, model))
    pub.appstudio_publish_check(pub.project)
    assert seen == [(WS, {"id": ITEM, "displayName": "Sales model"})]


def test_a_stale_publish_says_nothing_was_done(pub):  # noqa: F811
    got = publish(pub, pub.appstudio_publish_check(pub.project), fingerprint="old")
    assert got["stale"] is True and got["done"] is False
    assert pub.fabric.writes == []


def test_a_failed_create_that_left_nothing_says_only_why(pub):  # noqa: F811
    pub.fabric.fail = RuntimeError("quota")
    with pytest.raises(RuntimeError) as err:
        publish(pub, pub.appstudio_publish_check(pub.project))
    assert str(err.value) == "Publishing didn't finish (RuntimeError: quota)."


def test_a_failed_replace_says_the_report_may_be_half_updated(pub):  # noqa: F811
    pub.fabric.items.append({"id": REPORT, "type": "Report", "displayName": "Sales overview"})
    pub.fabric.fail = RuntimeError("quota")
    with pytest.raises(RuntimeError) as err:
        publish(pub, pub.appstudio_publish_check(pub.project), replace=True)
    assert str(err.value) == ("Publishing didn't finish (RuntimeError: quota). "
                              "The report in Fabric may be half updated; publishing again replaces it.")


def test_a_failure_is_cut_to_300_characters(pub):  # noqa: F811
    pub.fabric.fail = RuntimeError("y" * 500)
    with pytest.raises(RuntimeError) as err:
        publish(pub, pub.appstudio_publish_check(pub.project))
    why = str(err.value).removeprefix("Publishing didn't finish (").removesuffix(").")
    assert len(why) == 300 and why.endswith("y…")
    assert project.publish_history(pub._appstudio_project(pub.project))[-1]["note"] == why
