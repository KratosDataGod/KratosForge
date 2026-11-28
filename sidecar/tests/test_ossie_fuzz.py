"""Fuzz the Ossie readers with hostile files: a valid Ossie document with parts swapped for the wrong JSON type,
dropped or nested, and byte-level damage (tests/settings_fuzz.py: torn, not UTF-8, a BOM, NUL bytes, deep nesting,
duplicate keys, very long strings). test_tmdl_fuzz.py covers TMDL written from random models; this covers what a
person or another tool hands the app as Ossie YAML or JSON, and a Semantic View's folder of files.

The property: parse, to_tmsl, ossie_to_tmdl, ossie_validate.validate and ossie_profiles.validate_files read each
file or refuse it with a ValueError (TypeError where parse documents it) that says what is wrong in words, never
another exception. Every loop uses fixed seeds, so a failure names its seed and reproduces exactly."""

from __future__ import annotations

import copy
import json
import random
import warnings

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_profiles, ossie_validate
from tests.settings_fuzz import mutations
from tests.test_model_ossie import _ossie

pytestmark = pytest.mark.skipif(bool(model_ossie.unavailable()), reason="the Ossie converter can't run here")

ODD = [None, True, 0, -1, 1.5, "", "x" * 5000, "\x00", "é", [], [1, "a"], {}, {"a": {"b": [None]}}, [None], ["x"],
       [{}], "{not json", "[]"]


def _base() -> dict:
    doc = yaml.safe_load(_ossie())
    doc["datasets"][0]["metrics"][0]["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": json.dumps({"_v": 2})}]
    return doc


def _paths(value, at=()):
    yield at
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _paths(v, (*at, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _paths(v, (*at, i))


def _mutated(seed: int) -> dict:
    """The base document with one to three parts swapped for an odd value, or dropped."""
    r = random.Random(seed)
    doc = _base()
    for _ in range(r.randint(1, 3)):
        at = r.choice([p for p in _paths(doc) if p])
        parent = doc
        for k in at[:-1]:
            parent = parent[k]
        if r.random() < .15 and isinstance(parent, dict):
            parent.pop(at[-1], None)
        else:
            parent[at[-1]] = r.choice(ODD)
    return doc


def _plain(exc: Exception, case: str) -> None:
    assert type(exc) in (ValueError, TypeError, ossie_profiles.UnknownProfile), f"{case}: {type(exc).__name__}: {exc}"
    assert str(exc).strip(), case


def test_random_wrong_types_in_an_ossie_file_are_converted_or_refused_by_name():
    converted = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for seed in range(300):
            doc = _mutated(seed)
            text = yaml.safe_dump(doc, sort_keys=False) if seed % 2 else json.dumps(doc)
            case = f"seed {seed}"
            assert isinstance(ossie_validate.validate(text), dict), case
            result = ossie_profiles.validate_files({"a.yaml": text, "b.yaml": text.encode()})
            assert set(result["files"]) == {"a.yaml", "b.yaml"}, case
            try:
                parsed = model_ossie.parse(text)
                model_ossie.to_tmsl(parsed)
                model_ossie.ossie_to_tmdl(text)
            except (ValueError, TypeError) as exc:
                _plain(exc, case)
                continue
            converted += 1
    # Damage that only empties a part still converts: the loop isn't only refusals. A part of the wrong type (a
    # non-object item of a list, which the converter used to skip) is refused by path since the shape check.
    assert converted > 50


@pytest.mark.parametrize("seed", [1, 2])
def test_damaged_ossie_bytes_are_read_or_refused_by_name(seed):
    for label, raw in mutations(_base(), seed, 80):
        case = f"seed {seed} {label}"
        result = ossie_validate.validate(raw)
        assert result["valid"] in (True, False), case
        files = ossie_profiles.validate_files({"view.yaml": raw})
        assert "view.yaml" in files["files"], case
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            assert files["files"]["view.yaml"]["diagnostics"][0]["rule"] == "parse", case
            continue
        try:
            model_ossie.parse(text)
        except (ValueError, TypeError) as exc:
            _plain(exc, case)


# ---- the bugs the loops found, one by one

@pytest.mark.parametrize("part, value", [
    (("datasets",), "append 5"),
    (("datasets", 0, "metrics", 0, "expression"), "SUM(amount)"),
    (("datasets", 0, "metrics", 0, "custom_extensions"), 1.5),
    (("relationships", 0, "from_columns"), 5),
    (("datasets", 1, "fields"), 7),
])
def test_a_part_of_the_wrong_type_is_refused_by_where_it_is_not_a_crash(part, value):
    doc = _base()
    parent = doc
    for k in part[:-1]:
        parent = parent[k]
    if value == "append 5":
        parent[part[-1]].append(5)
    else:
        parent[part[-1]] = value
    with pytest.raises(ValueError, match=r"^The Ossie file couldn't be converted, because .*\$\."):
        model_ossie.to_tmsl(doc)


@pytest.mark.parametrize("data, why", [("{not json", r"isn't valid JSON \(Expecting"), ("[1]", "isn't a JSON object"),
                                       (5, "isn't valid JSON")])
def test_a_dataset_metrics_power_bi_data_that_isnt_a_json_object_is_refused_by_name(data, why):
    doc = _base()
    doc["datasets"][0]["metrics"][0]["custom_extensions"][0]["data"] = data
    with pytest.raises(ValueError, match=f"POWER_BI custom_extensions data in dataset 'fact_sales' metric 'net revenue' {why}"):
        model_ossie.to_tmsl(doc)


@pytest.mark.parametrize("text, key", [
    ('{"a": 1, "a": 2}', "a"),
    ("version: x\nname: a\nname: b\n", "name"),
    (_ossie() + "datasets: []\n", "datasets"),
])
def test_a_key_given_twice_is_refused_not_silently_replaced(text, key):
    with pytest.raises(ValueError, match=f"gives '{key}' twice in one object"):
        model_ossie.load_text(text)
    assert ossie_validate.validate(text)["diagnostics"][0]["rule"] == "parse"


def test_a_key_given_once_in_each_of_two_objects_is_read():
    assert model_ossie.load_text("a: {x: 1}\nb: {x: 2}\n") == {"a": {"x": 1}, "b": {"x": 2}}


@pytest.mark.parametrize("key, value", [("metrics", 5), ("relationships", "x"), ("metrics", {"a": 1})])
def test_a_view_file_whose_list_isnt_a_list_is_shown_in_that_file_not_a_crash(key, value):
    doc = copy.deepcopy(_base())
    first = {"version": doc["version"], "name": "v", "datasets": doc["datasets"]}
    second = {"version": doc["version"], "name": "v", key: value}
    result = ossie_profiles.validate_files({"a.yaml": yaml.safe_dump(first), "b.yaml": yaml.safe_dump(second)})
    assert not result["valid"]
    assert ("$." + key, "type") in {(d["path"], d["rule"]) for d in result["files"]["b.yaml"]["diagnostics"]}
