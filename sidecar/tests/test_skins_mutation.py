"""Mutation batch 24: the skin limits and colour maths that skins.py's other tests left untested."""

import json

import pytest

from fabric_desktop import skins


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _skin(**changes):
    m = json.loads(json.dumps(skins.KRATOSFORGE))
    m.update(id="my-skin", name="My skin")
    m.update(changes)
    return m


def _problems(manifest) -> list[str]:
    with pytest.raises(skins.SkinError) as err:
        skins.validate(manifest, skins.BASE)
    return err.value.problems


def _pages(n: int) -> list[str]:
    return [f"page{i}" for i in range(n)]


def test_a_file_of_exactly_64_kb_is_read_and_one_byte_more_is_refused():
    text = json.dumps(_skin())
    exact = text + " " * (skins.MAX_BYTES - len(text.encode("utf-8")))
    assert len(exact.encode("utf-8")) == 64 * 1024
    assert skins.parse(exact, skins.BASE)["id"] == "my-skin"
    with pytest.raises(skins.SkinError) as err:
        skins.parse(exact + " ", skins.BASE)
    assert err.value.problems == ["the file is larger than 64 KB; a skin is a few KB"]


def test_the_rail_holds_twelve_pages_and_not_thirteen():
    assert skins.validate(_skin(workflow={"rail": _pages(12)}), skins.BASE)
    assert _problems(_skin(workflow={"rail": _pages(13)})) == [
        "workflow rail must be a list of at most 12 page ids"]


def test_rail_labels_hold_twelve_pages_and_not_thirteen():
    twelve = {p: "Label" for p in _pages(12)}
    assert skins.validate(_skin(workflow={"labels": twelve}), skins.BASE)
    thirteen = {p: "Label" for p in _pages(13)}
    assert _problems(_skin(workflow={"labels": thirteen})) == [
        "workflow labels must be an object of at most 12 page id: label"]


def test_rail_labels_that_are_not_an_object_are_refused():
    assert _problems(_skin(workflow={"labels": ["home"]})) == [
        "workflow labels must be an object of at most 12 page id: label"]


def test_a_description_holds_200_characters():
    assert skins.validate(_skin(description="d" * 200), skins.BASE)
    assert _problems(_skin(description="d" * 201)) == ["description is longer than 200 characters"]


def test_targets_hold_four_and_not_five():
    one = {"platform": "fabric", "reviewed": "2026-11-03"}
    assert skins.validate(_skin(targets=[one] * 4), skins.BASE)
    assert _problems(_skin(targets=[one] * 5)) == ["targets must be a list of at most 4"]


def test_targets_that_are_not_a_list_are_refused():
    assert _problems(_skin(targets="fabric")) == ["targets must be a list of at most 4"]


def test_a_target_basis_holds_200_characters():
    ok = {"platform": "fabric", "reviewed": "2026-11-03", "basis": "b" * 200}
    assert skins.validate(_skin(targets=[ok]), skins.BASE)
    long = dict(ok, basis="b" * 201)
    assert _problems(_skin(targets=[long])) == ["targets[0] basis is longer than 200 characters"]


def test_a_label_for_an_unknown_command_is_one_problem_not_a_crash():
    m = _skin(workflow={"commands": {"notebook": {"primary": "notebook.run_all",
                                                  "labels": {"notebook.nope": "Nope"}}}})
    assert _problems(m) == ["commands.notebook.labels names notebook.nope, which isn't a notebook command"]


def test_a_token_outside_every_pair_needs_no_partner_without_built_in_colors():
    problems: list[str] = []
    skins._tokens({"--border": "#cccccc"}, "tokens light", problems, {})
    assert problems == []


def test_setting_only_the_background_names_the_missing_text_token():
    problems: list[str] = []
    skins._tokens({"--accent-dark": "#000000"}, "tokens dark", problems, {})
    assert problems == ["tokens dark sets --accent-dark, so it must also set --on-accent"]


def test_short_hex_colors_mean_the_same_as_long_ones():
    assert skins.contrast("#000", "#fff") == pytest.approx(21.0)
    assert skins.contrast("#000", "#fff") == skins.contrast("#000000", "#ffffff")
    assert skins.contrast("#abc", "#000") == skins.contrast("#aabbcc", "#000000")


@pytest.mark.parametrize("color, weight", [("#ff0000", 0.2126), ("#00ff00", 0.7152), ("#0000ff", 0.0722)])
def test_each_channel_carries_its_wcag_weight(color, weight):
    assert skins.contrast(color, "#000000") == pytest.approx((weight + 0.05) / 0.05)


def test_dump_writes_two_space_json_in_the_manifests_own_order():
    m = _skin()
    text = skins.dump(m)
    assert text == json.dumps(m, indent=2) + "\n"
    assert list(json.loads(text)) == list(m)
    assert text.startswith('{\n  "schema": ')
