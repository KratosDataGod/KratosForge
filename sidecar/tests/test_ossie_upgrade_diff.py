"""tools/ossie_upgrade_diff.py (OSSIE-012c): the corpus through the vendored converter and a candidate, the exit
code, and sys.modules and model_ossie always put back."""

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

import ossie_corpus as oc
from fabric_desktop import model_ossie as mo

TOOL = Path(__file__).resolve().parents[1] / "tools" / "ossie_upgrade_diff.py"
VENDORED = Path(mo.__file__).resolve().parent / "_vendor" / "ossie_microsoft"
SAME = """from fabric_desktop._vendor.ossie_microsoft import (OSSIE_VERSION, ConversionError,
    convert_ossie_to_semantic_model, convert_semantic_model_to_ossie)
"""
LOSES_ROLES = SAME.replace("convert_ossie_to_semantic_model,", "convert_ossie_to_semantic_model as _to_model,") + """
def convert_ossie_to_semantic_model(text, **kwargs):
    bim = _to_model(text, **kwargs)
    bim["model"].pop("roles", None)
    bim["model"].pop("relationships", None)
    return bim
"""
TAKES_BAD_EXTENSIONS = SAME.replace("convert_ossie_to_semantic_model,", "convert_ossie_to_semantic_model as _to_model,") \
    + """
import yaml

def convert_ossie_to_semantic_model(text, **kwargs):
    doc = yaml.safe_load(text)
    doc.pop("custom_extensions", None)
    return _to_model(yaml.safe_dump(doc), **kwargs)
"""
RAISES = SAME + """
def convert_semantic_model_to_ossie(*args, **kwargs):
    raise RuntimeError("candidate broke")
"""


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("ossie_upgrade_diff_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(tmp_path, source, name="candidate.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _state(tool):
    mo.require()
    return (sys.modules[tool.VENDOR], {n: getattr(mo, n) for n in (*tool.REQUIRED, *tool.OPTIONAL)},
            sorted(k for k in sys.modules if k.startswith(tool.CANDIDATE)))


# ---- exit codes

def test_the_vendored_package_as_candidate_changes_nothing_and_exits_0(tool, capsys):
    assert tool.main([str(VENDORED)]) == tool.NO_REGRESSION
    assert capsys.readouterr().out.strip() == "0 differences: 0 worse, 0 better, 0 changed."


def test_a_module_with_the_same_functions_exits_0(tool, tmp_path):
    assert tool.diff(_candidate(tmp_path, SAME)) == []
    assert tool.main([str(_candidate(tmp_path, SAME))]) == 0


def test_a_candidate_that_loses_roles_and_relationships_exits_1_naming_them(tool, tmp_path, capsys):
    assert tool.main([str(_candidate(tmp_path, LOSES_ROLES))]) == tool.REGRESSION
    out = capsys.readouterr().out
    assert "worse   ossie/v02_04_powerbi.yaml  to_tmsl fidelity role 'EU readers': fully_preserved -> unsupported" in out
    assert "to_tmsl fidelity relationship 'orders_customers': fully_preserved -> unsupported" in out


def test_a_candidate_that_raises_is_worse_where_the_corpus_converted(tool, tmp_path):
    rows = tool.diff(_candidate(tmp_path, RAISES), only=["tmsl/01_minimal.bim"])
    export = next(r for r in rows if r["what"] == "export")
    assert (export["before"], export["after"], export["change"]) == ("ok", "error:RuntimeError", "worse")


def test_taking_a_file_the_manifest_expects_refused_is_worse(tool, tmp_path):
    rows = tool.diff(_candidate(tmp_path, TAKES_BAD_EXTENSIONS), only=["hostile/wrong_extension_json.yaml"])
    row = next(r for r in rows if r["what"] == "to_tmsl")
    assert (row["before"], row["after"], row["change"]) == ("refused", "ok", "worse")
    assert row["why"] == "the manifest expects refused"


def test_compare_marks_better_worse_and_changed():
    spec = importlib.util.spec_from_file_location("ossie_upgrade_diff_compare", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    entries = [{"path": "f", "expect": {"to_tmsl": "ok", "validate": "Valid"}}]
    base = {"f": {"to_tmsl": ("refused", {"fidelity": {}}),
                  "validate": ("Valid", {})}}
    cand = {"f": {"to_tmsl": ("ok", {"fidelity": {"table 'a'": "fully_preserved"}}),
                  "validate": ("Valid", {})}}
    assert [(r["what"], r["change"]) for r in tool.compare(entries, base, cand)] == [
        ("to_tmsl", "better"), ("to_tmsl fidelity table 'a'", "changed")]
    base["f"]["to_tmsl"] = ("ok", {"fidelity": {"table 'a'": "unsupported", "table 'b'": "fully_preserved"}})
    cand["f"]["to_tmsl"] = ("ok", {"fidelity": {"table 'a'": "fully_preserved"}})
    assert [(r["what"], r["after"], r["change"]) for r in tool.compare(entries, base, cand)] == [
        ("to_tmsl fidelity table 'a'", "fully_preserved", "better"), ("to_tmsl fidelity table 'b'", "(gone)", "worse")]



def _compare_tool():
    spec = importlib.util.spec_from_file_location("ossie_upgrade_diff_compare_rules", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


@pytest.mark.parametrize("expect, before, after, change, why", [
    ("ok", "refused", "ok", "better", "the manifest expects ok"),
    ("ok", "ok", "refused", "worse", "the manifest expects ok"),
    (None, "ok", "refused", "worse", ""),
    (None, "refused", "ok", "better", ""),
    (None, "Unverified", "Invalid", "changed", ""),
], ids=["exp-better", "exp-worse", "rank-worse", "rank-better", "rank-same"])
def test_compare_judges_an_outcome_by_the_manifest_first_then_by_rank(expect, before, after, change, why):
    tool = _compare_tool()
    entries = [{"path": "f", "expect": {"validate": expect} if expect else {}}]
    rows = tool.compare(entries, {"f": {"validate": (before, {})}}, {"f": {"validate": (after, {})}})
    assert [(r["change"], r["why"]) for r in rows] == [(change, why)]


def test_compare_counts_an_object_the_candidate_drops_as_worse_whatever_its_class_was():
    tool = _compare_tool()
    entries = [{"path": "f", "expect": {}}]
    for cls in ("fully_preserved", "unsupported", "something_new"):
        base = {"f": {"to_tmsl": ("ok", {"fidelity": {"table 'a'": cls}})}}
        cand = {"f": {"to_tmsl": ("ok", {"fidelity": {}})}}
        assert [(r["after"], r["change"]) for r in tool.compare(entries, base, cand)] == [("(gone)", "worse")], cls

@pytest.mark.parametrize("source, says", [
    ("def broken(:\n", "doesn't import: SyntaxError"),
    ("raise ImportError('no such converter')\n", "doesn't import: ImportError: no such converter"),
    ("def convert_ossie_to_semantic_model(text):\n    return {}\n", "lacks convert_semantic_model_to_ossie"),
    ("convert_ossie_to_semantic_model = 1\nconvert_semantic_model_to_ossie = 2\n",
     "lacks convert_ossie_to_semantic_model, convert_semantic_model_to_ossie"),
], ids=["syntax", "raises", "missing", "not-callable"])
def test_a_candidate_that_cant_be_used_exits_2_with_the_reason(tool, tmp_path, capsys, source, says):
    before = _state(tool)
    assert tool.main([str(_candidate(tmp_path, source))]) == tool.BAD_CANDIDATE
    assert says in capsys.readouterr().err
    assert _state(tool) == before


def test_a_path_with_no_module_exits_2(tool, tmp_path, capsys):
    assert tool.main([str(tmp_path / "nothing.py")]) == tool.BAD_CANDIDATE
    (tmp_path / "pkg").mkdir()
    assert tool.main([str(tmp_path / "pkg")]) == tool.BAD_CANDIDATE
    assert "a package folder needs __init__.py" in capsys.readouterr().err


def test_only_limits_the_files_and_one_that_matches_nothing_is_a_usage_error(tool, tmp_path, monkeypatch):
    seen = []
    real = oc.outcomes
    monkeypatch.setattr(oc, "outcomes", lambda e: seen.append(e["path"]) or real(e))
    assert tool.diff(_candidate(tmp_path, SAME), only=["tmsl/"]) == []
    assert sorted(set(seen)) == ["tmsl/01_minimal.bim", "tmsl/02_rich.bim"] and len(seen) == 4
    with pytest.raises(SystemExit) as caught:
        tool.main([str(_candidate(tmp_path, SAME)), "--only", "nowhere/"])
    assert caught.value.code == 2


def test_json_output_is_the_rows(tool, tmp_path, capsys):
    assert tool.main([str(_candidate(tmp_path, LOSES_ROLES)), "--only", "ossie/v02_04", "--json"]) == 1
    rows = json.loads(capsys.readouterr().out)
    assert {r["change"] for r in rows} == {"worse"} and all(set(r) == {"path", "what", "before", "after", "change",
                                                                         "why"} for r in rows)


def test_the_tool_never_goes_online(tool, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the upgrade diff opened a connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert tool.main([str(_candidate(tmp_path, LOSES_ROLES)), "--only", "ossie/"]) == 1


# ---- sys.modules and model_ossie always put back

def test_the_candidate_stands_in_during_the_run_and_is_gone_after(tool, tmp_path):
    before = _state(tool)
    with tool.candidate_swapped(_candidate(tmp_path, LOSES_ROLES)) as module:
        assert sys.modules[tool.VENDOR] is module and module.__name__ == tool.CANDIDATE
        assert mo.convert_ossie_to_semantic_model is module.convert_ossie_to_semantic_model
        assert mo.convert_semantic_model_to_ossie is before[1]["convert_semantic_model_to_ossie"]  # re-exported
    assert _state(tool) == before


def test_a_package_candidate_loads_its_own_submodules_and_they_are_removed_after(tool):
    before = _state(tool)
    with tool.candidate_swapped(VENDORED) as module:
        assert f"{tool.CANDIDATE}._common" in sys.modules
        assert mo.ConversionError is module.ConversionError is not before[1]["ConversionError"]
    assert _state(tool) == before
    assert not [k for k in sys.modules if k.startswith(tool.CANDIDATE)]


def test_everything_is_put_back_when_the_run_raises(tool, tmp_path, monkeypatch):
    before = _state(tool)

    real = tool.run_corpus

    def raises_under_the_candidate(entries):
        if sys.modules[tool.VENDOR] is before[0]:
            return real(entries)  # the vendored run
        raise RuntimeError("stopped halfway")
    monkeypatch.setattr(tool, "run_corpus", raises_under_the_candidate)
    with pytest.raises(RuntimeError, match="stopped halfway"):
        tool.diff(_candidate(tmp_path, SAME), only=["ossie/v02_01"])
    assert _state(tool) == before


def test_everything_is_put_back_when_the_candidate_fails_to_import(tool, tmp_path):
    before = _state(tool)
    with pytest.raises(tool.BadCandidate):
        with tool.candidate_swapped(_candidate(tmp_path, "import no_such_package_anywhere\n")):
            pass
    assert _state(tool) == before


def test_the_corpus_converts_as_before_after_a_candidate_ran(tool, tmp_path):
    e = oc.entry("ossie/v02_04_powerbi.yaml")
    before = oc.outcomes(e)
    tool.diff(_candidate(tmp_path, LOSES_ROLES), only=["ossie/v02_04"])
    assert oc.outcomes(e) == before
