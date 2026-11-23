"""Ossie YAML on libyaml (perf follow-up of OSSIE-012b): every read and write gives exactly what PyYAML's
pure-Python code gives, refusals and their words included, and without libyaml the pure code runs alone.

Each check runs twice, once with ``model_ossie._C`` (libyaml's classes) and once with it None (the pure
code only, as on an engine whose PyYAML has no libyaml), and compares the two."""

from __future__ import annotations

import json
import random
import types
from collections.abc import Callable
from typing import Any

import pytest
import yaml

import ossie_corpus as oc
import ossie_large as ol
from fabric_desktop import model_ossie as mo
from fabric_desktop import semantic_parity as sp
from fabric_desktop import semantic_view as sv

mo.require()
FAST = mo._C
ENTRIES = oc.manifest()
TEXT_ENTRIES = [e for e in ENTRIES if e["kind"] in ("ossie", "view")]
V02 = "version: 0.2.0.dev0\nname: m\n"


def _norm(value: Any) -> Any:
    """A value with every type spelled out, so 1, 1.0 and True don't compare equal."""
    if isinstance(value, dict):
        return ("dict", [(_norm(k), _norm(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", [_norm(v) for v in value])
    return (type(value).__name__, repr(value))


def _outcome(fn: Callable[[], Any]) -> tuple[Any, ...]:
    try:
        return ("ok", _norm(fn()))
    except Exception as exc:  # noqa: BLE001  # narrow: the outcome is compared, whatever it is
        return ("raised", type(exc).__name__, str(exc))


def _both(monkeypatch: pytest.MonkeyPatch, fn: Callable[[], Any]) -> tuple[Any, Any]:
    """``fn``'s outcome with libyaml and with the pure code only."""
    monkeypatch.setattr(mo, "_C", FAST)
    fast = _outcome(fn)
    monkeypatch.setattr(mo, "_C", None)
    pure = _outcome(fn)
    monkeypatch.setattr(mo, "_C", FAST)
    return fast, pure


def test_libyaml_is_used_exactly_when_pyyaml_has_it():
    assert (FAST is not None) == bool(getattr(yaml, "__with_libyaml__", False))
    assert FAST is not None, "this engine's PyYAML has libyaml, so the fast path runs in these tests"


# ---- the corpus, both ways

@pytest.mark.parametrize("e", ENTRIES, ids=[e["path"].rsplit("/", 1)[-1] for e in ENTRIES])
def test_every_corpus_file_has_the_same_outcomes_with_and_without_libyaml(e, monkeypatch):
    fast, pure = _both(monkeypatch, lambda: oc.outcomes(e))
    assert fast == pure


@pytest.mark.parametrize("e", TEXT_ENTRIES, ids=[e["path"].rsplit("/", 1)[-1] for e in TEXT_ENTRIES])
def test_every_corpus_text_reads_the_same_and_a_plain_one_reads_on_the_fast_path(e, monkeypatch):
    text = oc.load(e)
    fast, pure = _both(monkeypatch, lambda: mo.load_text(text))
    assert fast == pure
    if not e["path"].startswith("hostile/") and "\t" not in text:
        assert _norm(yaml.load(text, Loader=FAST.no_alias)) == pure[1]  # noqa: S506 - read by libyaml itself


@pytest.mark.parametrize("e", [e for e in ENTRIES if e["kind"] in ("tmsl", "tmdl")],
                         ids=[e["path"].rsplit("/", 1)[-1] for e in ENTRIES if e["kind"] in ("tmsl", "tmdl")])
def test_every_corpus_export_and_view_is_byte_for_byte_the_same(e, monkeypatch):
    bim = oc._bim(e, oc.load(e))
    fast, pure = _both(monkeypatch, lambda: (mo.export(bim, translate_dax=True, validate=True),
                                             sv.semantic_view(bim, view_name="v")))
    assert fast == pure
    assert fast[0] == "ok" or "hostile/" in e["path"]


# ---- hostile text, both ways

DEEP = 3 * mo._FAST_DEPTH
HOSTILE = {
    "alias": V02 + "datasets:\n- &d {name: a}\n- *d\n",
    "alias_first": "a: &x 1\nb: *x\n",
    "billion_laughs": "a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\n",
    "anchor_only": V02 + "datasets: &d\n- name: a\n",
    "alias_undefined": "a: *nope\n",
    "repeated_key": V02 + "datasets: []\ndatasets: [{name: a}]\n",
    "repeated_key_nested": V02 + "datasets:\n- name: a\n  name: b\n",
    "repeated_key_flow": '{"version": "0.2.0.dev0", "name": "m", "name": "n"}',
    "repeated_number_key": "1: a\n1.0: b\n",
    "repeated_bool_key": "true: a\nyes: b\n",
    "merge_key": "a: {x: 1}\nb:\n  <<: {x: 2}\n  x: 3\n",
    "deep_block": "".join("  " * i + f"k{i}:\n" for i in range(DEEP)) + "  " * DEEP + "x: 1\n",
    "deep_flow": "[" * DEEP + "]" * DEEP,
    "deep_flow_map": '{"a": ' * DEEP + "1" + "}" * DEEP,
    "deep_past_recursion": "[" * 5000 + "]" * 5000,
    "just_under_fast_depth": "[" * (mo._FAST_DEPTH - 1) + "1" + "]" * (mo._FAST_DEPTH - 1),
    "at_fast_depth": "[" * mo._FAST_DEPTH + "1" + "]" * mo._FAST_DEPTH,
    "huge_name": V02.replace("name: m", "name: " + "n" * 3_000_000),
    "huge_key": "k" * 2000 + ": 1\n",
    "key_1024": "k" * 1024 + ": 1\n",
    "huge_flow_key": "{" + "k" * 2000 + ": 1}",
    "too_big": "a: " + "x" * mo.MAX_CHARS,
    "empty": "",
    "only_comment": "# nothing\n",
    "two_documents": "a: 1\n---\nb: 2\n",
    "document_end": "a: 1\n...\n",
    "not_yaml": "a: [1, 2\nb: }\n",
    "tab_indent": "a:\n\tb: 1\n",
    "tab_after_colon": "a:\t1\n",
    "tab_in_plain": "a: x\ty\n",
    "tab_in_block": "a: |\n  x\ty\n  \tz\n",
    "tab_in_quotes": 'a: "x\ty"\n',
    "tab_trailing": "a: 1\t\n",
    "crlf": V02.replace("\n", "\r\n") + "datasets: [{name: a}]\r\n",
    "cr_only": "a: 1\rb: 2\r",
    "bom_start": "\ufeff" + V02,
    "bom_middle": "a: 1\n\ufeffb: 2\n",
    "bom_twice": "\ufeff\ufeffa: 1\n",
    "nel": "a: x\x85y\nb: |\n  1\x85  2\n",
    "line_separator": "a: x\u2028y\nb: \"p\u2029q\"\n",
    "escaped_breaks": 'a: "x\\Ny\\Lz\\Pw\\r"\n',
    "escaped_surrogate": 'a: "\\ud800"\n',
    "raw_surrogate": "a: \ud800\n",
    "astral": "a: \U0001F600\n\U0001F600: b\n",
    "rtl_and_combining": "\u05e9\u05dc\u05d5\u05dd: e\u0301\u200b\u200f\n",
    "nul": "a: \x00\n",
    "control": "a: \x07\x1b\n",
    "c1_control": "a: \x9f\n",
    "noncharacter": "a: \ufffe\uffff\n",
    "header_comment": "a: |#c\n  x\nb: >-#c\n  y\n",
    "header_comment_spaced": "a: | #c\n  x\n",
    "question_in_flow": "a: [x?y]\n",
    "question_in_flow_map": '{"a": b?c}',
    "question_spaced_in_flow": "[a ? b]",
    "question_in_block": "a: x?y\n",
    "question_quoted_in_flow": '["x?y", \'a?\']',
    "colon_in_flow": "[a:b, c:]",
    "colon_after_quote": '{"a":b}',
    "tags": "a: !!str 1\nb: !!int '2'\nc: !!binary aGk=\nd: !!set {x}\ne: !!timestamp 2024-01-02\n",
    "unknown_tag": "a: !python/object:os.system x\n",
    "python_tag": "a: !!python/tuple [1, 2]\n",
    "yaml_directive": "%YAML 1.1\n---\na: 1\n",
    "yaml_directive_12": "%YAML 1.2\n---\na: 1\n",
    "timestamps": "a: 2024-01-02\nb: 2024-01-02 10:00:00+02:00\nc: 2001-12-14t21:59:43.10-05:00\n",
    "numbers": "a: 0x1F\nb: 0o17\nc: 017\nd: 1_000\ne: 1e3\nf: .nan\ng: -.inf\nh: 190:20:30\ni: +1\n",
    "bools": "a: yes\nb: off\nc: On\nd: y\ne: NO\n",
    "json": json.dumps({"version": "0.2.0.dev0", "name": "m\u00e9\n\t\"", "datasets": [{"name": "a", "x": [1, 2.5, None,
                                                                                                         True]}]}),
    "json_escapes": '{"a": "\\u00e9\\t\\n\\"\\\\\\/"}',
    "literal_keep": "a: |+\n  x\n\n\nb: 1\n",
    "folded": "a: >\n  x\n  y\n\n  z\n",
    "indent_indicator": "a: |2\n   x\n  y\n",
    "complex_key": "? [a, b]\n: 1\n",
    "complex_string_key": "? |\n  k\n: v\n",
    "null_key": "~: a\nnull: b\n",
    "plain_multiline": "a: x\n  y\n\n  z\n",
}


@pytest.mark.parametrize("name", list(HOSTILE))
def test_hostile_text_reads_or_is_refused_exactly_as_by_the_pure_reader(name, monkeypatch):
    text = HOSTILE[name]
    fast, pure = _both(monkeypatch, lambda: mo.load_text(text))
    assert fast == pure


@pytest.mark.parametrize("name", ["alias", "billion_laughs", "repeated_key", "repeated_key_flow",
                                  "repeated_number_key", "deep_past_recursion", "too_big"])
def test_the_safety_refusals_still_refuse_with_libyaml(name):
    with pytest.raises(ValueError, match="aliases|twice in one object|nested too deeply|MB"):
        mo.load_text(HOSTILE[name])


def test_a_deep_file_goes_to_the_pure_reader_and_a_flagged_one_never_reaches_libyaml(monkeypatch):
    calls = []
    real = FAST.no_alias

    class Spy(real):  # type: ignore[misc,valid-type]
        def __init__(self, stream: str) -> None:
            calls.append(stream)
            super().__init__(stream)

    monkeypatch.setattr(mo, "_C", types.SimpleNamespace(**{**vars(FAST), "no_alias": Spy}))
    for name in ("tab_indent", "bom_middle", "header_comment"):
        _outcome(lambda: mo.load_text(HOSTILE[name]))  # noqa: B023 - called at once
    assert calls == []
    with pytest.raises(mo._NotSure):
        yaml.load(HOSTILE["deep_flow"], Loader=real)  # noqa: S506 - the fast reader alone gives up
    with pytest.raises(mo._NotSure):
        yaml.load(HOSTILE["question_in_flow"], Loader=real)  # noqa: S506
    assert mo.load_text(V02) == {"version": "0.2.0.dev0", "name": "m"} and calls == [V02]


def test_random_text_reads_the_same_both_ways(monkeypatch):
    rng = random.Random(7)
    pieces = ["a", "1", "0x1F", ".5", "true", "null", "~", "2024-01-02", ":", ": ", "- ", "\n", "\n  ", "\n- ", "  ",
              "\t", "#", " #c", "[", "]", "{", "}", ",", "'", '"', "\\", "|", "|+", ">-", "&x ", "*x", "!!str ", "? ",
              "?", "---", "...", "\r\n", "\x85", "\u2028", "\ufeff", "é", "\U0001F600", "<<: ", '{"a": ', "k: v\n"]
    for _ in range(3000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 20)))
        fast, pure = _both(monkeypatch, lambda text=text: mo.load_text(text))
        assert fast == pure, text


# ---- writing, both ways

def _docs() -> dict[str, Any]:
    tricky = ["plain", "", " lead", "trail ", "a: b", "- x", "#c", "multi\nline", "keep\n\n", "strip\n", "\n",
              "tab\there", "two\n\ttabs\n", "nel\x85x", "ls\u2028x", "ps\u2029x", "cr\rx", "crlf\r\nx", "bom\ufeff",
              "nul\x00", "bell\x07", "c1\x9f", "nonchar\ufffe", "emoji\U0001F600", "surrogate\ud800", "é中",
              "true", "null", "1.5", "0x10", "2024-01-02", "'quote'", '"dq"', "back\\slash", "...", "---", "| x",
              "x" * 5000, "word " * 1000]
    keys = ["k", "a" * 99, "a" * 100, "a" * 101, "a" * 130, "é" * 50, "é" * 51, "é" * 70, "", "nl\nkey", "cr\rkey",
            "emoji\U0001F600"]
    return {
        "strings": {"values": tricky, "nested": [{"name": s, "description": s} for s in tricky]},
        "keys": {k: i for i, k in enumerate(keys)},
        "non_text_keys": {1: "a", None: "b", False: "c", 2.5: "d"},
        "scalars": {"i": 10 ** 30, "f": 1.5, "neg": -0.0, "nan": float("nan"), "inf": float("-inf"), "b": False,
                    "n": None},
        "keep_last": {"a": "x", "b": "y\n\n"},
        "keep_not_last": {"a": "y\n\n", "b": "x"},
        "keep_in_list": {"a": ["y\n\n", "z"], "b": 1},
        "shared": (lambda shared: {"a": shared, "b": shared})(["same", "list"]),
        "other_types": {"tuple": (1, 2)},
        "dates": {"d": __import__("datetime").date(2024, 1, 2)},
        "set": {"s": {"x"}},
        "bytes": {"b": b"hi"},
    }


@pytest.mark.parametrize("name", list(_docs()))
def test_dumping_is_byte_for_byte_the_same_both_ways(name, monkeypatch):
    doc = _docs()[name]
    view = {"version": "0.2.0.dev0", "name": "v", "datasets": [{"name": "d", "description": doc}]} \
        if name not in ("other_types", "set", "bytes", "non_text_keys") else None
    fast, pure = _both(monkeypatch, lambda: (
        mo.dump_yaml(doc),
        mo.dump_yaml(doc, (yaml.SafeDumper, FAST.safe_dumper), sort_keys=False),
        sv._dump(view) if view is not None else None))
    assert fast == pure


def test_random_documents_dump_and_read_back_the_same_both_ways(monkeypatch):
    rng = random.Random(11)
    alphabet = list("ab :#-?[]{},&*!|>'\"%@`\t\n\\ 01.e+~") + ["\n\n", "  ", "\x85", "\u2028", "\u2029", "\ufeff",
                                                                 "\x07", "\xa0", "é", "中", "\ufffe", "\u200b"]

    def text() -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.choice((rng.randint(0, 8), rng.randint(0, 120)))))

    def value(depth: int) -> Any:
        r = rng.random()
        if depth > 3 or r < 0.5:
            return rng.choice([text(), text(), 1, -2.5, True, None])
        if r < 0.75:
            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
        return {rng.choice([text(), "name", "a" * rng.randint(90, 130)]): value(depth + 1) for _ in range(3)}

    for _ in range(600):
        doc = {"k": value(0), text() or "z": value(0)}
        fast, pure = _both(monkeypatch, lambda doc=doc: (mo.dump_yaml(doc), mo.load_own(mo.dump_yaml(doc))))
        assert fast == pure, doc


def test_text_with_tabs_reads_back_the_same_both_ways(monkeypatch):
    """The dumper writes a tab as an escape in double quotes; a literal block holding tabs (as another tool writes
    one) reads the same both ways too."""
    doc = {"expression": "SUMX(\n\tSales,\n\t\tSales[a] *\t2\n)\n", "name": "tab\tname", "list": ["\t", "x\n\t"]}
    text = mo.dump_yaml(doc)
    assert "\t" not in text
    fast, pure = _both(monkeypatch, lambda: (mo.load_own(text), mo.load_text(text)))
    assert fast == pure == ("ok", _norm((doc, doc)))
    block = "expression: |\n  SUMX(\n  \tSales,\n  \t\tSales[a] *\t2\n  )\n"
    fast, pure = _both(monkeypatch, lambda: mo.load_own(block))
    assert fast == pure == ("ok", _norm({"expression": doc["expression"]}))


# ---- large models and parity, both ways

def test_a_large_model_converts_exports_and_compares_the_same_both_ways(monkeypatch):
    bim, text = ol.bim(3, 6, 8), ol.ossie(3, 6, 8)
    fast, pure = _both(monkeypatch, lambda: (
        mo.to_tmsl(text), mo.export(bim, validate=True), sp.parity_l1(bim, text), sp.parity_l2(bim, text)))
    assert fast == pure and fast[0] == "ok"


# ---- without libyaml

def test_without_libyaml_there_are_no_fast_classes_and_views_use_the_pure_dumper(monkeypatch):
    no_c = types.SimpleNamespace(__with_libyaml__=False)
    assert mo._fast_classes(no_c, object, yaml.SafeDumper) is None
    monkeypatch.setattr(yaml, "__with_libyaml__", False)
    assert sv._dumper.__wrapped__()[1] is None


@pytest.mark.parametrize("e", ENTRIES, ids=[e["path"].rsplit("/", 1)[-1] for e in ENTRIES])
def test_the_corpus_meets_its_manifest_with_the_pure_code_only(e, monkeypatch):
    monkeypatch.setattr(mo, "_C", None)
    for point, (word, detail) in oc.outcomes(e).items():
        assert word == e["expect"][point], (point, detail)
        if word == oc.REFUSED:
            assert e["says"] in detail["message"]
