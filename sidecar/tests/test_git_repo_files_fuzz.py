"""Fuzz the files read from a person's repository folders: a platform repository's TOML files
(git/platform_files.py), capacity.toml (git/capacity_files.py), the repository marker and
locations.toml (git/scopes.py), and a Databricks Asset Bundle's databricks.yml and its includes
(engines/databricks_bundles.py).

Each case is a seeded mutation of a valid file: a field of the wrong type, odd values, deep nesting,
a BOM, bytes that aren't UTF-8, NUL bytes, keys given twice, very long text, or random byte damage.
Every case must be read or refused by name with a plain message, never raise anything else."""

from __future__ import annotations

import copy
import random
import time

import pytest
import yaml

from fabric_desktop.engines import databricks_bundles as db
from fabric_desktop.git import capacity_files as cf
from fabric_desktop.git import platform_files as pf
from fabric_desktop.git import scopes

from tests.settings_fuzz import ODD_VALUES

CASES = 120
DEEP = "x = " + "[" * 5000 + "]" * 5000 + "\n"


def _damage(rnd: random.Random, raw: bytes) -> bytes:
    out = bytearray(raw)
    for _ in range(rnd.randint(1, 4)):
        out[rnd.randrange(len(out))] = rnd.randrange(256)
    return bytes(out)


def _fixed(good: bytes) -> list[bytes]:
    return [b"", b"\xef\xbb\xbf" + good, good[:5] + b"\xff\xfe" + good[5:], good.replace(b"\n", b"\x00\n", 1),
            good[: len(good) // 2], good + good, DEEP.encode(), b"x = " + b"9" * 5000 + b"\n",
            b'x = "' + b"y" * 200_000 + b'"\n', good.replace(b"\n", b"\r\n")]


def _toml_cases(valid: dict, seed: int, n: int):
    """Byte cases for one TOML file: fixed damage first, then fields swapped for odd values."""
    good = pf.dumps(valid).encode("utf-8")
    yield from _fixed(good)
    rnd = random.Random(seed)
    odd = [v for v in ODD_VALUES if v is not None] + [[{"a": 1}], {"x": [1, {"y": "z"}]}, 1e400, -0.0]
    for _ in range(n):
        data = {k: (dict(v) if isinstance(v, dict) else v) for k, v in valid.items()}
        for _ in range(rnd.randint(1, 3)):
            k = rnd.choice(list(data))
            if isinstance(data[k], dict) and data[k] and rnd.random() < 0.5:
                data[k][rnd.choice(list(data[k]))] = copy.deepcopy(rnd.choice(odd))
            else:
                data[k] = copy.deepcopy(rnd.choice(odd))
        try:
            raw = pf.dumps(data).encode("utf-8", "surrogatepass")
        except ValueError:
            continue
        yield _damage(rnd, raw) if rnd.random() < 0.25 else raw


PLATFORM = {
    "domains/sales.toml": {"id": "sales", "name": "Sales", "parent": "", "description": "d", "owners": ["a"]},
    "sources/crm.toml": {"source_id": 3, "code": "crm", "title": "CRM", "active": True, "zone": "hr_secure"},
    "landing_zones/hr_secure.toml": {"id": "hr_secure", "name": "HR", "secure": True, "readers": ["g"]},
    "key_tables/customer.toml": {"name": "customer", "business_key": "id", "domain": "sales"},
    "policy_templates/mine.toml": {"id": "mine", "name": "Mine", "category": "quality", "description": "",
                                   "definition": {"severity": "low"}},
    "policies/p1.toml": {"id": "p1", "status": "published", "definition": {"name": "P", "category": "quality"},
                         "assign": [{"scope": "enterprise", "settings": {"a": 1}}]},
    "admins.toml": {"admins": ["a@x", "b@x"]},
    "glossary.toml": {"term": [{"term": "t", "definition": "d"}]},
    "shortcut_rules.toml": {"allow": {"x": True}},
}


def _check_platform(out: dict) -> None:
    assert isinstance(out["problems"], list) and all(isinstance(p, str) for p in out["problems"])
    for rec in out["sources"].values():
        sid = rec["source_id"]
        assert isinstance(sid, str) or (isinstance(sid, int) and not isinstance(sid, bool))
    if out["admins"] is not None:
        assert all(isinstance(a, str) for a in out["admins"])


@pytest.mark.parametrize("rel", sorted(PLATFORM))
def test_any_platform_file_is_read_or_reported_never_raises(tmp_path, rel):
    for i, raw in enumerate(_toml_cases(PLATFORM[rel], seed=len(rel), n=CASES // 4)):
        root = tmp_path / str(i)
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_bytes(raw)
        _check_platform(pf.read_repo(root))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        _check_platform(pf.read_repo(root, ({rel: text}, [])))  # the committed copy reads the same way


@pytest.mark.parametrize(("text", "found"), [
    ('code = "crm"\nsource_id = inf\n', "inf"),
    ('code = "crm"\nsource_id = nan\n', "nan"),
    ('code = "crm"\nsource_id = true\n', "True"),
    ('code = "crm"\nsource_id = 1.5\n', "1.5"),
    ('code = "crm"\nsource_id = "1_0"\n', "1_0"),
    ('code = "crm"\nsource_id = 4.0\n', 4),
    ('code = "crm"\nsource_id = " 12 "\n', 12),
])
def test_a_source_id_that_isnt_a_whole_number_is_kept_as_written(tmp_path, text, found):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "crm.toml").write_text(text, encoding="utf-8")
    assert pf.read_repo(tmp_path)["sources"]["crm"]["source_id"] == found


@pytest.mark.parametrize(("rel", "text"), [
    ("policy_templates/mine.toml", 'id = "mine"\ndefinition = [1, 2]\n'),
    ("policy_templates/mine.toml", 'id = "mine"\ndefinition = "ab"\n'),
    ("policies/mine.toml", 'id = "mine"\ncurrent = "x"\n'),
    ("policies/mine.toml", 'id = "mine"\ndefinition = {}\nassign = "x"\n'),
    ("policies/mine.toml", 'id = "mine"\ndefinition = {}\nassign = [1]\n'),
    ("policies/mine.toml", 'id = "mine"\ndefinition = {}\nassign = [{ scope = "enterprise", settings = [1] }]\n'),
])
def test_a_field_of_the_wrong_kind_is_reported_by_file(tmp_path, rel, text):
    (tmp_path / rel).parent.mkdir()
    (tmp_path / rel).write_text(text, encoding="utf-8")
    out = pf.read_repo(tmp_path)
    assert out["problems"] == [(f"{rel} wasn't read, because a field in it holds the wrong kind of value. "
                               "Check it against docs/source-control.md, then compare again.")]


@pytest.mark.parametrize(("name", "text", "why"), [
    ("glossary.toml", "term = 5\n", "a term in it isn't a [[term]] table"),
    ("glossary.toml", 'term = "abc"\n', "a term in it isn't a [[term]] table"),
    ("admins.toml", "admins = 5\n", "its admins value isn't a list of names"),
    ("admins.toml", "admins = [{ a = 1 }]\n", "its admins value isn't a list of names"),
])
def test_admins_and_glossary_of_the_wrong_shape_are_reported_not_used(tmp_path, name, text, why):
    (tmp_path / name).write_text(text, encoding="utf-8")
    out = pf.read_repo(tmp_path)
    kind = name.removesuffix(".toml")
    assert out[kind] is None
    assert out["problems"] == [f"{name} wasn't read, because {why}. Fix the file, then compare again."]


def test_a_platform_file_nested_too_deeply_is_reported(tmp_path):
    (tmp_path / "shortcut_rules.toml").write_text(DEEP, encoding="utf-8")
    msg = "shortcut_rules.toml wasn't read, because it's nested too deeply. Move its deepest settings up a level, then compare again."
    assert pf.read_repo(tmp_path)["problems"] == [msg]
    assert pf.read_repo(tmp_path, ({"shortcut_rules.toml": DEEP}, []))["problems"] == [msg]


@pytest.mark.parametrize("ident", ["nul", "con", "aux", "prn", "com1", "lpt9", "nul.x", "clock$"])
def test_export_refuses_a_windows_device_name_as_a_file_name(ident):
    defs = {k: {} for k in pf.FOLDERS} | {"admins": [], "glossary": {}, "shortcut_rules": {}}
    defs["domains"] = {ident: pf.domain_record({"id": ident, "name": "x"}), "sales": pf.domain_record({"id": "sales"})}
    files, refused = pf.to_files(defs)
    assert "domains/sales.toml" in files and not any(f.startswith(f"domains/{ident}") for f in files)
    assert refused == [{"kind": "domains", "id": ident, "why": "its name can't be a file name"}]


# --------------------------------------------------------------------------- capacity.toml and scopes

CAPACITY = {"capacities": {"F64 Prod": {"capacity_id": "c1", "sku": "F64", "region": "West Europe", "state": "Active",
                                        "workspaces": [{"id": "w1", "name": "Sales"}, "w2"]}}}


def test_any_capacity_file_is_read_or_reported_never_raises(tmp_path):
    rnd = random.Random(7)
    cases = list(_toml_cases(CAPACITY, seed=7, n=CASES))
    for _i in range(CASES):  # one level down too: the fields of the capacity's own section
        sec = dict(CAPACITY["capacities"]["F64 Prod"])
        sec[rnd.choice(list(sec))] = copy.deepcopy(rnd.choice(ODD_VALUES[1:] + [[1, None], [{"id": 5}], {"id": "w"}]))
        cases.append(pf.dumps({"capacities": {rnd.choice(["F64 Prod", "", "\x7f", "x" * 300]): sec}}).encode())
    for raw in cases:
        (tmp_path / cf.FILE).write_bytes(raw)
        out = cf.read(tmp_path)
        assert all(isinstance(p, str) for p in out["problems"])
        for cap in out["capacities"]:
            assert isinstance(cap["capacity_id"], str) and cap["capacity_id"]
            for w in cap["workspaces"]:
                assert isinstance(w["id"], str) and w["id"] and len(w["id"]) <= 256 and isinstance(w["name"], str)


@pytest.mark.parametrize("value", ["5", '"w1w2"', '{ id = "w1" }'])
def test_capacity_workspaces_that_arent_a_list_are_reported(tmp_path, value):
    (tmp_path / cf.FILE).write_text(f'[capacities.a]\ncapacity_id = "c1"\nworkspaces = {value}\n', encoding="utf-8")
    out = cf.read(tmp_path)
    assert out["capacities"][0]["workspaces"] == []
    assert out["problems"] == [("The workspaces under capacities.a in capacity.toml weren't read, because they aren't "
                               "a list. Fix the file, then compare again.")]


def test_toml_files_nested_too_deeply_are_reported(tmp_path):
    for name in (cf.FILE, scopes.MARKER, scopes.LOCATIONS):
        (tmp_path / name).write_text(DEEP, encoding="utf-8")
    assert cf.read(tmp_path)["problems"] == [("capacity.toml wasn't read, because it's nested too deeply. "
                                             "Move its deepest settings up a level, then compare again.")]
    assert scopes.read_kind(tmp_path) is None
    assert scopes.read_locations(tmp_path)["problem"] == ("locations.toml wasn't read, because it's nested too "
                                                          "deeply. Move its deepest settings up a level, then try again.")


def test_any_marker_or_locations_file_is_read_or_reported_never_raises(tmp_path):
    marker = {"kind": "notebooks", "version": 1}
    locations = {"dev": {"runs_on": "this_pc"}, "test": {"workspace_id": "w", "lakehouse_id": "l"}}
    for raw in _toml_cases(marker, seed=3, n=CASES // 2):
        (tmp_path / scopes.MARKER).write_bytes(raw)
        assert scopes.read_kind(tmp_path) in (None, *scopes.KINDS)
    for raw in _toml_cases(locations, seed=4, n=CASES):
        (tmp_path / scopes.LOCATIONS).write_bytes(raw)
        out = scopes.read_locations(tmp_path)
        assert out["problem"] is None or isinstance(out["problem"], str)


# --------------------------------------------------------------------------- Databricks bundles

BUNDLE = {"bundle": {"name": "b"}, "include": ["resources/*.yml"],
          "experimental": {"scripts": {"preinit": "echo hi"}},
          "artifacts": {"lib": {"type": "whl", "build": "python -m build"}},
          "targets": {"dev": {"mode": "development", "default": True}, "prod": {"mode": "production"}}}


def _bundle_cases(seed: int, n: int):
    good = yaml.safe_dump(BUNDLE).encode("utf-8")
    yield from _fixed(good)
    yield b"a: " + b"[" * 5000 + b"]" * 5000
    yield b"a: &a [*a]\nartifacts: {x: {build: *a}}\n"
    yield good + b"experimental: {}\n"
    yield b"include: 5\n"
    yield b'include: ["a\\0b"]\n'
    yield b"include: [/etc/passwd]\n"
    yield b"include: ['..\\\\x.yml']\n"
    yield b"targets: [dev]\n"
    yield b"bundle: name\n"
    yield b"\tbundle: x\n"
    rnd = random.Random(seed)
    for _ in range(n):
        data = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BUNDLE.items()}
        for _ in range(rnd.randint(1, 3)):
            k = rnd.choice(list(data))
            if isinstance(data[k], dict) and data[k] and rnd.random() < 0.5:
                data[k][rnd.choice(list(data[k]))] = copy.deepcopy(rnd.choice(ODD_VALUES))
            else:
                data[k] = copy.deepcopy(rnd.choice(ODD_VALUES))
        raw = yaml.safe_dump(data, allow_unicode=True).encode("utf-8", "surrogatepass")
        yield _damage(rnd, raw) if rnd.random() < 0.25 else raw


def test_any_bundle_file_is_read_or_refused_by_name(tmp_path):
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "jobs.yml").write_text("resources: {}\n", encoding="utf-8")
    for raw in _bundle_cases(seed=11, n=CASES):
        (tmp_path / "databricks.yml").write_bytes(raw)
        for call in (db.targets, db.local_commands):
            try:
                out = call(str(tmp_path))
            except db.BundleError as exc:
                assert str(exc) and "\n" not in str(exc)
            else:
                assert isinstance(out, dict)
        (tmp_path / "resources" / "jobs.yml").write_bytes(raw)  # the same damage in an included file
        (tmp_path / "databricks.yml").write_text("include: [resources/*.yml]\n", encoding="utf-8")
        try:
            db.local_commands(str(tmp_path))
        except db.BundleError as exc:
            assert str(exc)


@pytest.mark.parametrize(("text", "message"), [
    ("targets: [dev]\n", ("databricks.yml wasn't read, because its targets section isn't a set of named settings. "
                         "Fix the file, then try again.")),
    ("bundle: name\n", ("databricks.yml wasn't read, because its bundle section isn't a set of named settings. "
                       "Fix the file, then try again.")),
    ("include: 5\n", ("databricks.yml wasn't read, because its include isn't a list of file patterns. "
                     "Fix the file, then try again.")),
    ("include: resources/*.yml\n", ("databricks.yml wasn't read, because its include isn't a list of file patterns. "
                                   "Fix the file, then try again.")),
    ('include: ["a\\0b"]\n', "The include 'a b' matches no file, so the bundle can't be checked."),
    ("a: " + "[" * 5000 + "]" * 5000 + "\n",
     "databricks.yml wasn't read, because it's nested too deeply. Move its deepest settings up a level, then try again."),
    ("a: &a [*a]\n", ("databricks.yml wasn't read, because one of its YAML aliases refers to itself. "
                     "Write that part out in full, then try again.")),
])
def test_a_bundle_of_the_wrong_shape_is_refused_by_name(tmp_path, text, message):
    (tmp_path / "databricks.yml").write_text(text, encoding="utf-8")
    got = []
    for call in (db.targets, db.local_commands):
        try:
            call(str(tmp_path))
        except db.BundleError as exc:
            got.append(str(exc))
    assert got and set(got) == {message}


def test_a_repeated_key_is_refused_with_its_line(tmp_path):
    (tmp_path / "databricks.yml").write_text("bundle:\n  name: a\n  cluster: b\nbundle: {}\n", encoding="utf-8")
    msg = ("databricks.yml wasn't read, because it gives 'bundle' twice in one section (line 4). "
           "Keep one, then try again.")
    with pytest.raises(db.BundleError) as got:
        db.local_commands(str(tmp_path))
    assert str(got.value) == msg
    (tmp_path / "databricks.yml").write_text("targets:\n  prod:\n    mode: production\n    mode: development\n",
                                             encoding="utf-8")
    with pytest.raises(db.BundleError, match="gives 'mode' twice in one section"):
        db.targets(str(tmp_path))


def test_yaml_aliases_and_merge_keys_still_read(tmp_path):
    (tmp_path / "databricks.yml").write_text(
        "x: &cluster {spark_version: '15.4'}\ntargets:\n  dev: {<<: *cluster, default: true}\n  test: *cluster\n",
        encoding="utf-8")
    assert [t["name"] for t in db.targets(str(tmp_path))["targets"]] == ["dev", "test"]


@pytest.mark.parametrize("text, key, line", [
    ("a:\n  b: 1\n  c:\n    d: 1\n    d: 2\n", "d", 5),  # at any depth
    ("a: 1\n'a': 2\n", "a", 2),  # quoted or not, the same key
    ('a: 1\n"a": 2\n', "a", 2),
    ("x: &x {mode: a}\ny: &y {mode: b}\ntargets: {t: {<<: [*x, *y]}}\n", "mode", 3),  # two merged sources
    ("x: &x {k: {<<: {m: 1}, n: 1}}\ny: &y {k: 2}\nz: {<<: [*x, *y]}\n", "k", 3),
])
def test_a_repeated_key_is_refused_wherever_it_is(tmp_path, text, key, line):
    (tmp_path / "databricks.yml").write_text(text, encoding="utf-8")
    with pytest.raises(db.BundleError, match=f"gives {key!r} twice in one section \\(line {line}\\)"):
        db.targets(str(tmp_path))


def test_a_key_written_beside_a_merge_key_overrides_it_and_merged_commands_are_listed(tmp_path):
    (tmp_path / "databricks.yml").write_text(
        "x: &x {scripts: {postdeploy: echo merged}, pydabs: {enabled: false}}\n"
        "experimental: {<<: *x, pydabs: {enabled: true}}\n", encoding="utf-8")
    shown = " ".join(db.local_commands(str(tmp_path))["commands"])
    assert "experimental.scripts.postdeploy: echo merged" in shown
    assert "experimental.pydabs" in shown


def test_aliases_that_expand_past_what_can_be_checked_are_refused_quickly(tmp_path):
    lines = ["x0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"x{i + 1}: &a{i + 1} [" + ", ".join([f"*a{i}"] * 10) + "]" for i in range(9)]
    (tmp_path / "databricks.yml").write_text("\n".join(lines) + "\nartifacts: {w: {build: *a9}}\n", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(db.BundleError) as got:
        db.targets(str(tmp_path))  # never written out: 10 ** 10 values once expanded
    assert str(got.value) == ("databricks.yml wasn't read, because its YAML aliases make it too large to check once "
                              "they are written out. Use fewer aliases, then try again.")
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("text", [
    "a: &a {k: 1}\nt: {<<: [*a, *a]}\ntargets: {x: {}}\n",  # one base merged twice is one copy
    "a: &a {k: 1}\nc: &c {<<: *a, m: 2}\nt: {<<: [*c, *a]}\ntargets: {x: {}}\n",  # a layer and its base
    "a: &a {k: 1}\nb: &b {k: 2}\nt: {<<: [*a, *b], k: 3}\ntargets: {x: {}}\n",  # written out: the override
])
def test_merge_keys_that_give_one_value_or_are_overridden_still_read(tmp_path, text):
    (tmp_path / "databricks.yml").write_text(text, encoding="utf-8")
    assert [t["name"] for t in db.targets(str(tmp_path))["targets"]] == ["x"]


def test_a_key_two_merge_sources_give_differently_is_refused_even_from_a_nested_merge(tmp_path):
    (tmp_path / "databricks.yml").write_text("x: &x {<<: {mode: a}}\ny: &y {mode: b}\nt: {<<: [*x, *y]}\n",
                                             encoding="utf-8")
    with pytest.raises(db.BundleError, match="gives 'mode' twice in one section \\(line 3\\)"):
        db.targets(str(tmp_path))


@pytest.mark.parametrize("text", [
    's: &s "' + "x" * 600_000 + '"\nl: [' + ", ".join(["*s"] * 10) + "]\n",  # long text, counted by length
    "\n".join(["l0: &l0 {k: 1}"] + [f"l{i}: &l{i} {{<<: [*l{i - 1}, *l{i - 1}], k{i}: 1}}" for i in range(1, 25)])
    + "\n",  # merge keys that double at every level
])
def test_aliases_and_merges_too_large_once_written_out_are_refused_quickly(tmp_path, text):
    (tmp_path / "databricks.yml").write_text(text, encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(db.BundleError, match="its YAML aliases make it too large to check"):
        db.targets(str(tmp_path))
    assert time.monotonic() - start < 5
