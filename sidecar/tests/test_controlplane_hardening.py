"""Hardening for the KratosForge Environment control plane (CP-001 to CP-011): fuzz-style checks that
bad input is refused with the error each layer promises (ManifestError, ValueError, PermissionError),
never another exception, and never with a side effect. Deterministic: seeded ``random.Random`` loops,
no network, nothing reaches Fabric."""

from __future__ import annotations

import copy
import json
import random

import pytest

from fabric_desktop import app_settings
from fabric_desktop import measurement as ms
from fabric_desktop.controlplane import cost
from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import store as st
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service

WS = "11111111-1111-4111-8111-111111111111"
LH = "22222222-2222-4222-8222-222222222222"
FN = "33333333-3333-4333-8333-333333333333"
CENTRAL = "44444444-4444-4444-8444-444444444444"
OID = "55555555-5555-4555-8555-555555555555"
AT = "2026-11-19T20:00:00Z"
SECRET_KEYS = ("password", "client_secret", "clientSecret", "token", "access_token", "connection_string",
               "ConnectionString", "api_key", "accountKey", "sas", "credentials", "pwd")


def full(**extra):
    """A valid manifest using every optional part, so every field can be mutated."""
    kwargs = dict(name="Contoso data platform", provider="fabric",
                  namespace={"workspace_id": WS, "display_name": "KratosForge"},
                  analytical_store={"workspace_id": WS, "lakehouse_id": LH, "display_name": "KratosForgeLakehouse"},
                  operational_store={"workspace_id": WS, "functions_id": FN, "display_name": "Meta"},
                  deployments={"prod": {"platform_workspace_id": WS}}, operational_version=3,
                  families={"fdopt_": 2, "fdacc_": 1}, owner_object_id=OID, services=["optimize", "savings"], at=AT)
    m = mf.new_manifest(**(kwargs | extra))
    m["config_refs"] = {"policy": "Files/_kratosforge/policy.json"}
    return mf.validate(m)


def _raises_manifest_error(fn, *args):
    """Calls fn and returns the exception, failing the test unless it is a ManifestError."""
    try:
        fn(*args)
    except mf.ManifestError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - the point: any other type is a bug
        pytest.fail(f"{fn.__name__} raised {type(exc).__name__}: {exc!r:.200}, not ManifestError")
    pytest.fail(f"{fn.__name__} accepted what it must refuse")


# ======================================================================================= 1. manifest fuzz

# Protects the byte-stable file: the same manifest always writes the same bytes, and reading them back
# gives the same manifest (so a conditional replace never rewrites a file that didn't change).
def test_a_valid_manifest_round_trips_byte_identically():
    rng = random.Random(1)
    for _ in range(25):
        name = "".join(rng.choice("abcXYZ éü漢字-_.") for _ in range(rng.randint(1, mf.MAX_NAME))).strip() or "x"
        m = full(name=name) | {"future": {"z": [1, 2.5, None, True], "a": "ok"}}
        data = mf.to_json(m)
        assert mf.from_json(data) == m
        assert mf.to_json(mf.from_json(data)) == data
        assert mf.from_json(data.decode("utf-8")) == m  # str in, same manifest


REQUIRED = [("manifest_version",), ("environment_id",), ("name",), ("provider",), ("namespace",),
            ("analytical_store",), ("created_at",), ("updated_at",), ("namespace", "workspace_id"),
            ("analytical_store", "workspace_id"), ("analytical_store", "lakehouse_id"), ("analytical_store", "kind"),
            ("operational_store", "workspace_id"), ("operational_store", "functions_id"),
            ("operational_store", "kind"), ("owner", "object_id"), ("migration", "state")]


# Protects every required field: a manifest missing one is refused as a ManifestError, never a KeyError.
@pytest.mark.parametrize("path", REQUIRED, ids=".".join)
def test_dropping_any_required_key_is_a_manifest_error(path):
    m = full()
    *parents, last = path
    target = m
    for p in parents:
        target = target[p]
    del target[last]
    _raises_manifest_error(mf.validate, m)
    _raises_manifest_error(mf.from_json, json.dumps(m))


WRONG = [123, -1, 1.5, True, None, [], ["x"], {}, {"a": 1}, "text", ""]
# Values a field may legitimately hold, which are not "wrong types" for it (keyed by repr of the value).
VALID_ANYWAY = {("name", "'text'"), ("manifest_version", "123"), ("analytical_store.schemas", "True"),
                ("schema.operational", "123"), ("schema.operational", "None"), ("services", "[]"),
                ("services", "['x']"), ("capabilities", "[]"), ("capabilities", "['x']"),
                ("deployments", "{}"), ("schema", "{}"), ("schema", "{'a': 1}"), ("compatibility", "{}"),
                ("compatibility", "{'a': 1}"), ("config_refs", "{}"), ("schema.families", "{}"),
                ("operational_store", "None"), ("owner", "None"), ("operational_store.database_id", "None"),
                ("operational_store.database_id", "''")} | {
    (f"{part}.display_name", v) for part in ("namespace", "analytical_store", "operational_store")
    for v in ("None", "''", "'text'")}
FIELDS = ["manifest_version", "environment_id", "name", "provider", "namespace", "analytical_store",
          "operational_store", "deployments", "schema", "compatibility", "owner", "created_at", "updated_at",
          "services", "capabilities", "migration", "config_refs", "namespace.workspace_id",
          "namespace.display_name", "analytical_store.workspace_id", "analytical_store.lakehouse_id",
          "analytical_store.kind", "analytical_store.schemas", "analytical_store.display_name",
          "operational_store.functions_id", "operational_store.database_id", "operational_store.display_name",
          "schema.families", "schema.operational", "compatibility.min_app_version", "owner.object_id",
          "migration.state", "config_refs.policy", "deployments.prod"]


def _set(m, path, value):
    *parents, last = path.split(".")
    target = m
    for p in parents:
        target = target[p]
    target[last] = value


# Protects each field's type rule: every wrong-typed value (number, bool, null, list, object, text) in
# every field is a ManifestError (an optional object set to null is refused too: only absent means default).
@pytest.mark.parametrize("field", FIELDS)
def test_a_wrong_type_in_any_field_is_a_manifest_error(field):
    for value in WRONG:
        if (field, repr(value)) in VALID_ANYWAY:
            continue
        m = full()
        _set(m, field, value)
        _raises_manifest_error(mf.validate, m)
        _raises_manifest_error(mf.from_json, json.dumps(m))


# Protects the length limits: a name, label or migration field one character too long is refused.
@pytest.mark.parametrize("field,limit", [("name", mf.MAX_NAME), ("namespace.display_name", mf.MAX_NAME),
                                         ("analytical_store.display_name", mf.MAX_NAME),
                                         ("operational_store.display_name", mf.MAX_NAME)])
def test_oversize_strings_are_refused(field, limit):
    m = full()
    _set(m, field, "a" * limit)
    mf.validate(m)  # at the limit: fine
    _set(m, field, "a" * (limit + 1))
    _raises_manifest_error(mf.validate, m)
    _set(m, field, "a" * 100_000)
    _raises_manifest_error(mf.validate, m)


# Protects migration's own limits (holder id <= 64, failed step <= MAX_TEXT) and control characters anywhere.
def test_migration_fields_and_control_characters_are_bounded():
    for migration in ({"state": "running", "by": "x" * 65, "expires_at": AT},
                      {"state": "failed", "step": "x" * (mf.MAX_TEXT + 1)},
                      {"state": "failed", "step": "a\tb"}):
        _raises_manifest_error(mf.validate, full() | {"migration": migration})
    for ch in ("\x00", "\x1b", "\r", "\n"):
        _raises_manifest_error(mf.validate, full() | {"name": f"a{ch}b"})


def _dicts(value):
    """Every dict inside value, the top level included."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _dicts(v)


# Protects "no secrets, ever": a credential-shaped key anywhere (top level, in a store, in an unknown
# key, inside a list), in a current or a newer manifest, is refused, and the file can't be written.
def test_secret_shaped_keys_at_any_depth_are_refused():
    rng = random.Random(2)
    for i in range(300):
        m = full() | {"future": {"nested": [{"deeper": {"x": 1}}, {"y": []}]}}
        if i % 3 == 0:
            m["manifest_version"] = mf.MANIFEST_VERSION + 1  # a newer manifest is read-only, but still never secret
        target = rng.choice(list(_dicts(m)))
        key = rng.choice(SECRET_KEYS)
        target[key] = rng.choice(["x", "", 1, None, {"v": "x"}, ["x"]])
        exc = _raises_manifest_error(mf.validate, m)
        assert "credential" in str(exc), (key, str(exc))
        _raises_manifest_error(mf.from_json, json.dumps(m))
        _raises_manifest_error(mf.to_json, m)


# Protects the recursion guard: deep nesting in the JSON or in a dict built in memory is a ManifestError,
# not a RecursionError that would take the discovery loop down.
def test_deep_nesting_is_a_manifest_error():
    for opener, closer in (("[", "]"), ('{"a":', "}")):
        depth = (mf.MAX_BYTES - 10) // (len(opener) + len(closer))
        _raises_manifest_error(mf.from_json, opener * depth + "1" + closer * depth)
    deep: dict = {}
    node = deep
    for _ in range(50_000):
        node["a"] = {}
        node = node["a"]
    _raises_manifest_error(mf.validate, full() | {"future": deep})


# Protects reading a file anyone in the workspace can write: random bytes, bytes that aren't UTF-8,
# and a valid manifest with invalid UTF-8 spliced into a string are all ManifestError.
def test_non_utf8_and_random_bytes_are_manifest_errors():
    rng = random.Random(3)
    good = mf.to_json(full())
    for _ in range(300):
        _raises_manifest_error(mf.from_json, bytes(rng.randrange(256) for _ in range(rng.randint(0, 64))) or b"\xff")
    for bad in (b"\xff", b"\xc3\x28", b"\xed\xa0\x80", b"\xf8\x88\x80\x80\x80"):
        _raises_manifest_error(mf.from_json, good.replace(b"Contoso", b"Cont" + bad))


# Protects the size cap: one byte over MAX_BYTES is refused before parsing, at the cap is parsed.
def test_the_size_cap_is_exact():
    good = mf.to_json(full())
    padded = good + b" " * (mf.MAX_BYTES - len(good))
    assert len(padded) == mf.MAX_BYTES and mf.to_json(mf.from_json(padded)) == good
    exc = _raises_manifest_error(mf.from_json, padded + b" ")
    assert "larger" in str(exc)
    _raises_manifest_error(mf.from_json, "x" * (mf.MAX_BYTES + 1))


# Protects against JSON that parses into something Python can't use: an integer literal too long to
# convert raises plain ValueError inside json.loads, which must still surface as ManifestError.
def test_an_integer_too_long_to_convert_is_a_manifest_error():
    _raises_manifest_error(mf.from_json, '{"manifest_version": ' + "1" * 5000 + "}")
    good = mf.to_json(full()).decode()
    _raises_manifest_error(mf.from_json, good.replace('"fdopt_": 2', '"fdopt_": ' + "9" * 5000))


# Protects writing: a lone surrogate (\ud800, legal in JSON text but not encodable as UTF-8) must be
# refused when read, or a manifest that validates would fail later in to_json with UnicodeEncodeError.
@pytest.mark.parametrize("where", ["name", "namespace.display_name", "future_key"])
def test_a_lone_surrogate_is_refused_not_a_crash_later(where):
    m = full()
    if where == "future_key":
        m["\ud800"] = "x"
    else:
        _set(m, where, "a\ud800b")
    _raises_manifest_error(mf.validate, m)
    _raises_manifest_error(mf.to_json, m)
    text = json.dumps(full()).replace("Contoso", "Cont\\ud800")
    _raises_manifest_error(mf.from_json, text)


# Protects the "ManifestError or a sound manifest" contract under random edits of a real file: delete,
# insert or replace characters; whatever parses and validates must also write back to the same bytes.
def test_random_edits_of_a_valid_file_never_raise_anything_else():
    rng = random.Random(4)
    good = mf.to_json(full()).decode()
    alphabet = '{}[]":,0123456789-.eE ntrufalse\\"\nkfenv_abé\ud800'
    accepted = 0
    for _ in range(1500):
        text = list(good)
        for _ in range(rng.randint(1, 4)):
            pos = rng.randrange(len(text))
            op = rng.randrange(3)
            if op == 0:
                del text[pos]
            elif op == 1:
                text.insert(pos, rng.choice(alphabet))
            else:
                text[pos] = rng.choice(alphabet)
        data = "".join(text)
        try:
            m = mf.from_json(data)
        except mf.ManifestError:
            continue
        except Exception as exc:  # noqa: BLE001 - any other exception is the crash this fuzz test looks for
            pytest.fail(f"from_json raised {type(exc).__name__}: {exc!r:.200}")
        accepted += 1
        assert mf.from_json(mf.to_json(m)) == m
    assert accepted  # some edits (in labels, whitespace) are still sound manifests


# ======================================================================================= 2. updated()

# Protects identity: an update can never change the id, provider, creation time or version, even to a
# valid-looking value, and the refused update leaves the original untouched.
@pytest.mark.parametrize("key,value", [("environment_id", "kfenv_" + "b" * 32), ("provider", "local"),
                                       ("created_at", "2020-01-01T00:00:00Z"), ("manifest_version", 1),
                                       ("manifest_version", mf.MANIFEST_VERSION + 1)])
def test_updated_never_changes_identity(key, value):
    m = full()
    before = copy.deepcopy(m)
    exc = _raises_manifest_error(mf.updated, m, {key: value, "name": "Renamed"})
    assert "can't change" in str(exc) and m == before


# Protects forward compatibility: keys this version doesn't know survive an update (deep copies, so
# the update doesn't share or change the caller's objects).
def test_updated_keeps_unknown_keys_and_copies():
    m = full() | {"future": {"a": [1, {"b": 2}]}, "zz_later": "keep"}
    out = mf.updated(m, {"name": "Renamed"}, at="2026-11-20T00:00:00Z")
    assert out["future"] == {"a": [1, {"b": 2}]} and out["zz_later"] == "keep" and out["name"] == "Renamed"
    assert out["environment_id"] == m["environment_id"] and out["created_at"] == AT
    out["future"]["a"][1]["b"] = 99
    assert m["future"]["a"][1]["b"] == 2 and m["name"] == "Contoso data platform"


# Protects read-only newer manifests: an older app refuses to update one a newer app wrote.
def test_updated_refuses_a_newer_manifest_version():
    newer = full() | {"manifest_version": mf.MANIFEST_VERSION + 1, "namespace": "a later shape"}
    mf.validate(newer)
    exc = _raises_manifest_error(mf.updated, newer, {"name": "x"})
    assert "read-only" in str(exc)


# Protects validation on update: a change that breaks a rule (a secret, a bad type) is refused.
def test_updated_validates_its_result():
    for changes in ({"name": ""}, {"services": "optimize"}, {"namespace": {"workspace_id": WS, "token": "x"}},
                    {"owner": {"object_id": OID, "email": "a@b.c"}}):
        _raises_manifest_error(mf.updated, full(), changes)


# ======================================================================================= 3. ManifestStore

# Protects create-only: a second create in the same store is ManifestExists, and the first manifest stays.
def test_store_create_twice_is_manifest_exists(tmp_path):
    store = st.local_store(tmp_path)
    first = store.create(full())
    with pytest.raises(st.ManifestExists):
        store.create(full(name="Other"))
    with pytest.raises(st.ManifestExists):
        store.create(first.manifest)  # even the very same manifest
    assert store.read() == first


# Protects the etag condition: a replace from a stale read is ManifestChanged and writes nothing.
def test_store_replace_with_a_stale_etag_is_manifest_changed(tmp_path):
    store = st.local_store(tmp_path)
    first = store.create(full())
    second = store.replace(first, mf.updated(first.manifest, {"name": "Second"}))
    with pytest.raises(st.ManifestChanged):
        store.replace(first, mf.updated(first.manifest, {"name": "Stale"}))
    stale = st.Read(first.manifest, "not-an-etag")
    with pytest.raises(st.ManifestChanged):
        store.replace(stale, mf.updated(first.manifest, {"name": "Forged"}))
    assert store.read().manifest["name"] == "Second" and store.read() == second


# Protects one environment per store: replacing with another environment's manifest (or one with no
# id) is a ManifestError, checked before anything is written.
def test_store_replace_with_another_environment_is_refused(tmp_path):
    store = st.local_store(tmp_path)
    first = store.create(full())
    for other in (full(name="Other"), {k: v for k, v in first.manifest.items() if k != "environment_id"}):
        with pytest.raises(mf.ManifestError, match="another environment"):
            store.replace(first, other)
    assert store.read() == first


# ======================================================================================= 4. Cache

CORRUPT = ["", "{", '{"environments": {"x"', "[1, 2]", "null", "5", '"text"', "true",
           '{"environments": [1, 2]}', '{"environments": "x", "active": "x"}',
           '{"environments": {"x": 5, "y": [1], "z": null}, "active": "x"}',
           '{"active": ["a"], "environments": {}}', '{"active": {"a": 1}, "environments": {}}',
           '{"active": 7, "environments": {"7": 1}}']


# Protects offline start-up: a corrupt, half-written or wrongly shaped environments.json is treated as
# empty (no active environment, nothing listed) and never raises; remembering one afterwards works.
@pytest.mark.parametrize("content", CORRUPT + [b"\xff\xfe\x00\x81", b"\x00" * 10])
def test_a_corrupt_cache_is_empty_never_raises(tmp_path, content):
    folder = tmp_path / "controlplane"
    folder.mkdir()
    path = folder / "environments.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    cache = st.Cache(folder)
    assert cache.all() == [] and cache.active() is None
    assert cache.forget("x") is False
    with pytest.raises(KeyError):
        cache.activate("x")
    m = full()
    cache.remember(m)
    assert cache.active()["environment_id"] == m["environment_id"]
    assert [e["environment_id"] for e in cache.all()] == [m["environment_id"]]


# Protects the active pointer: forgetting the active environment clears it; forgetting another keeps it.
def test_forgetting_the_active_environment_clears_active(tmp_path):
    cache = st.Cache(tmp_path)
    a, b = full(name="A"), full(name="B")
    cache.remember(a)
    cache.remember(b, activate=False)
    assert cache.forget(b["environment_id"]) and cache.active()["environment_id"] == a["environment_id"]
    assert cache.forget(a["environment_id"]) and cache.active() is None and cache.all() == []
    assert json.loads((tmp_path / "environments.json").read_text())["active"] == ""


# Protects the cache's lookups from malformed ids (they arrive from the app as JSON): no TypeError.
@pytest.mark.parametrize("bad", [None, 5, ["x"], {"a": 1}, "", "x" * 10_000, "../../etc/passwd"])
def test_cache_lookups_with_malformed_ids_change_nothing(tmp_path, bad):
    cache = st.Cache(tmp_path)
    m = full()
    cache.remember(m)
    before = (tmp_path / "environments.json").read_bytes()
    assert cache.forget(bad) is False
    with pytest.raises(KeyError):
        cache.activate(bad)
    assert (tmp_path / "environments.json").read_bytes() == before


# Protects "ids and labels only": the cached entry is exactly the summary keys, whatever extra or
# unknown keys the manifest held, and nothing secret-shaped reaches the file.
def test_remember_stores_summary_keys_only(tmp_path):
    cache = st.Cache(tmp_path)
    m = full() | {"future": {"note": "not for the cache"}, "zz": "also not"}
    entry = cache.remember(m)
    assert set(entry) == set(mf.summary(m))
    data = json.loads((tmp_path / "environments.json").read_text())
    assert set(data) == {"active", "environments"}
    assert set(data["environments"][m["environment_id"]]) == set(mf.summary(m))
    text = (tmp_path / "environments.json").read_text()
    for word in ("future", "not for the cache", "owner", OID, FN, "config_refs", "deployments"):
        assert word not in text
    with pytest.raises(mf.ManifestError):
        cache.remember(full() | {"x": {"password": "p"}})  # a manifest with a secret is never cached
    assert "password" not in (tmp_path / "environments.json").read_text()


# ======================================================================================= 5. service input fuzz

class FakeFabric:
    def __init__(self):
        self.workspaces = [{"id": WS, "displayName": "Platform"}]
        self.lakehouses: dict[str, list[dict]] = {WS: []}
        self.created: list[tuple] = []

    def list_workspaces(self):
        return list(self.workspaces)

    def list_lakehouses(self, ws):
        return list(self.lakehouses.get(ws, []))

    def find_lakehouse(self, ws, name):
        return next((lh for lh in self.list_lakehouses(ws) if lh["displayName"] == name), None)

    def create_workspace(self, name, capacity_id="", description=""):
        ws = {"id": f"{len(self.created) + 2:08d}-2222-4222-8222-222222222222", "displayName": name}
        self.workspaces.append(ws)
        self.lakehouses[ws["id"]] = []
        self.created.append(("workspace", name))
        return ws

    def create_lakehouse(self, ws, name, description=""):
        lh = {"id": f"{len(self.created) + 2:08d}-3333-4333-8333-333333333333", "displayName": name}
        self.lakehouses.setdefault(ws, []).append(lh)
        self.created.append(("lakehouse", name))
        return lh


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    s = Service(root=tmp_path / "app")
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None, token=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: st.local_store(lakes / ws / lh))
    prod: set[str] = set()
    monkeypatch.setattr(Service, "_prod_marked", lambda self, ws: ws in prod)
    s.fake, s.lakes, s.prod = fabric, lakes, prod
    return s


def _central(svc):
    svc.fake.lakehouses[WS].append({"id": CENTRAL, "displayName": "FabricDesktop_Central"})
    svc.optimize_connect(WS, CENTRAL, "Platform", "FabricDesktop_Central", initialize=True)


def _adopted(svc):
    """An adopted central lakehouse holding only Optimize's tables, so an upgrade has work to do."""
    _central(svc)
    return svc.cp_adopt(svc.cp_adopt_preview("Ours")["token"], confirmed=True)


def _cache_bytes(svc):
    path = svc.root / "controlplane" / "environments.json"
    return path.read_bytes() if path.exists() else None


# Protects cp_plan's request shape: anything but an object is a ValueError, with no preview kept.
@pytest.mark.parametrize("request_", [None, "name", ["name"], 5, 1.5, True, ("name", "x"), b"{}"])
def test_cp_plan_refuses_a_non_object(svc, request_):
    with pytest.raises(ValueError):
        svc.cp_plan(request_)
    assert svc._cp_previews() == {} and svc.fake.created == []


# Protects cp_plan's fields under fuzz: unknown keys (any type), non-text values and over-long values
# are ValueError (or PermissionError), never another exception, and refused requests keep no preview.
def test_cp_plan_input_fuzz_raises_only_value_or_permission_errors(svc):
    rng = random.Random(5)
    from fabric_desktop import controlplane_service as cpsvc
    keys = list(cpsvc.REQUEST_KEYS) + ["extra", "", "NAME", "__class__", 1, None, ("t",)]
    values = [None, "", "x", "Ours", " ", "x" * (cpsvc.MAX_FIELD + 1), "x" * cpsvc.MAX_FIELD, 1, 0.5, True,
              [], {}, ["x"], {"a": "b"}, b"x", WS, "kfenv_" + "0" * 32, "../../x", "\x00", "a\nb"]
    for _ in range(400):
        request = {rng.choice(keys): rng.choice(values) for _ in range(rng.randint(0, 5))}
        try:
            out = svc.cp_plan(request)
        except (ValueError, PermissionError):
            continue
        except Exception as exc:  # noqa: BLE001 - any other exception is the crash this fuzz test looks for
            pytest.fail(f"cp_plan({request!r:.200}) raised {type(exc).__name__}: {exc!r:.200}")
        assert isinstance(out, dict) and isinstance(out["token"], str)
        assert all(isinstance(k, str) and k in cpsvc.REQUEST_KEYS for k in request)
    assert svc.fake.created == []


# Protects create from a name, label or owner the manifest would refuse: such a plan is blocked (no token) at preview
# time, so confirming it never creates a workspace and lakehouse that then can't get a manifest.
@pytest.mark.parametrize("request_", [
    {"name": "two\nlines", "workspace_id": WS},
    {"name": "x" * (mf.MAX_NAME + 1), "workspace_id": WS},
    {"name": "Server=tcp:x;Password=hunter2", "workspace_id": WS},
    {"name": "Ours", "workspace_id": WS, "owner_object_id": "someone@example.com"},
    {"name": "Ours", "capacity_id": "cap", "owner_object_id": "not-a-guid"},
    {"name": "Ours", "capacity_id": "cap", "workspace_name": "w" * (mf.MAX_NAME + 1)},
    {"name": "Ours", "workspace_id": WS, "lakehouse_name": "l" * (mf.MAX_NAME + 1)},
])
def test_cp_plan_blocks_what_the_manifest_would_refuse(svc, request_):
    plan = svc.cp_plan(request_)
    assert plan["blocked"] and plan["token"] == ""
    assert svc.fake.created == [] and svc._cp_previews() == {}


GARBAGE_TOKENS = [None, 0, 1, -1, 10**40, 1.5, True, "", " ", "x" * 1_000_000, "made-up", "\x00", ["t"], {"t": 1}]


# Protects the token gate: a garbage token (any type, any size) for create, adopt or upgrade is a
# PermissionError and nothing is created, adopted or upgraded.
@pytest.mark.parametrize("method", ["cp_create", "cp_adopt", "cp_upgrade"])
def test_garbage_tokens_are_permission_errors_and_create_nothing(svc, method):
    real = svc.cp_plan({"name": "Ours", "capacity_id": "cap"})["token"]
    for token in GARBAGE_TOKENS:
        with pytest.raises(PermissionError):
            getattr(svc, method)(token, confirmed=True)
    assert svc.fake.created == [] and _cache_bytes(svc) is None
    assert real in svc._cp_previews()  # the real preview is still there, unused


# Protects kind binding: each kind's token is refused by the other kinds' calls, and the refusal doesn't
# use the token up (peek before take), so the right call still runs it.
def test_a_token_works_only_for_its_own_kind(svc):
    plan = svc.cp_plan({"name": "Ours", "workspace_id": WS})
    for method in ("cp_adopt", "cp_upgrade"):
        with pytest.raises(PermissionError, match="expired"):
            getattr(svc, method)(plan["token"], confirmed=True)
    assert svc.fake.created == []
    assert svc.cp_create(plan["token"], confirmed=True)["ok"]


# Protects cp_cost's days parameter: bool, float, text, out of range, huge or null days is a short
# ValueError (under 80 characters, so a huge value isn't echoed back).
@pytest.mark.parametrize("days", [True, False, 1.5, 30.0, "30", "9" * 500, -1, 0, 367, 10**100, None, [30], {"d": 1}])
def test_cp_cost_days_are_checked_with_a_short_message(svc, days):
    with pytest.raises(ValueError) as caught:
        svc.cp_cost(days=days)
    assert len(str(caught.value)) < 80 and "between 1 and 366" in str(caught.value)


# Protects switch and disconnect: unknown or malformed ids are a ValueError (switch) or "not forgotten"
# (disconnect), and neither changes the active environment or the cache file.
@pytest.mark.parametrize("bad", ["", None, 5, "kfenv_" + "0" * 32, ["x"], {"a": 1}, "x" * 100_000, "../../etc"])
def test_switch_and_disconnect_with_bad_ids_change_nothing(svc, bad):
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    before, status = _cache_bytes(svc), svc.cp_status()
    with pytest.raises(ValueError, match="isn't connected"):
        svc.cp_switch(bad)
    assert svc.cp_disconnect(bad)["forgotten"] is False
    assert _cache_bytes(svc) == before and svc.cp_status() == status
    assert status["active"]["environment_id"] == out["environment"]["environment_id"]


# ======================================================================================= 6. upgrade edges

# Protects kind binding for upgrade tokens: create and adopt refuse one, and it still upgrades afterwards.
def test_an_upgrade_token_cant_create_or_adopt(svc):
    _adopted(svc)
    plan = svc.cp_upgrade_plan()
    assert plan["token"]
    for method in ("cp_create", "cp_adopt"):
        with pytest.raises(PermissionError, match="expired"):
            getattr(svc, method)(plan["token"], confirmed=True)
    assert svc.fake.created == []
    assert svc.cp_upgrade(plan["token"], confirmed=True)["ok"]


# Protects the person's choice of environment: an upgrade previewed for one environment doesn't run
# once this PC disconnected from it, and nothing in the lakehouse changes.
def test_upgrade_after_disconnect_refuses(svc):
    out = _adopted(svc)
    lake = LocalLakehouse(svc.lakes / WS / CENTRAL)
    plan = svc.cp_upgrade_plan()
    before = sorted(lake.table_names())
    svc.cp_disconnect(out["environment"]["environment_id"])
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_upgrade(plan["token"], confirmed=True)
    assert sorted(lake.table_names()) == before
    assert st.local_store(svc.lakes / WS / CENTRAL).read().manifest == out["manifest"]


# Protects the same after a switch: the previewed environment is no longer the active one, so the
# upgrade is refused rather than run against an environment the person moved away from.
def test_upgrade_after_switching_away_refuses(svc):
    out = _adopted(svc)
    plan = svc.cp_upgrade_plan()
    other = svc.cp_create(svc.cp_plan({"name": "B", "workspace_id": WS, "lakehouse_name": "B"})["token"], confirmed=True)
    assert svc.cp_status()["active"]["environment_id"] == other["environment"]["environment_id"]
    with pytest.raises(PermissionError, match="changed since"):
        svc.cp_upgrade(plan["token"], confirmed=True)
    assert st.local_store(svc.lakes / WS / CENTRAL).read().manifest == out["manifest"]
    svc.cp_switch(out["environment"]["environment_id"])  # back: a fresh plan runs
    assert svc.cp_upgrade(svc.cp_upgrade_plan()["token"], confirmed=True)["ok"]


# Protects the Prod typed-name gate with "peek before take": a near-miss name (trailing or leading
# space, case, invisible character) is refused, the token survives, and the exact name then works.
@pytest.mark.parametrize("method", ["cp_upgrade", "cp_adopt"])
def test_a_near_miss_prod_name_is_refused_and_the_token_survives(svc, method):
    svc.prod.add(WS)
    if method == "cp_upgrade":
        _central(svc)  # adopting a Prod workspace needs its name typed too
        svc.cp_adopt(svc.cp_adopt_preview("Ours")["token"], confirmed=True, confirm_name="Platform")
        token = svc.cp_upgrade_plan()["token"]
    else:
        _central(svc)
        preview = svc.cp_adopt_preview("Ours")
        assert preview["typed_name"] == "Platform"
        token = preview["token"]
    for typed in ("Platform ", " Platform", "platform", "PLATFORM", "Platform\n", "Platform\u200b", "", None):
        with pytest.raises(PermissionError, match="type its name exactly"):
            getattr(svc, method)(token, confirmed=True, confirm_name=typed)
        assert token in svc._cp_previews()
    assert getattr(svc, method)(token, confirmed=True, confirm_name="Platform")["ok"]
    assert token not in svc._cp_previews()


# ======================================================================================= 7. cost.net()

CLASSES = (ms.MEASURED, ms.ESTIMATED, ms.SCENARIO, ms.UNKNOWN)
ORDER = {c: i for i, c in enumerate(CLASSES)}  # weakest last


def _side(cls, value, unit=ms.CURRENCY, currency="USD"):
    return {"classification": cls, "value": value, "unit": unit, "currency": currency}


# Protects net savings' honesty for every pair of classifications: a mismatched unit or currency, or a
# scenario against a non-scenario, is None with a reason; otherwise the label is the weaker side's.
@pytest.mark.parametrize("a", CLASSES)
@pytest.mark.parametrize("b", CLASSES)
def test_net_for_every_pair_of_classifications(a, b):
    for savings, spend in [(_side(a, 10.0, currency="EUR"), _side(b, 4.0)),
                           (_side(a, 10.0, unit=ms.CU_SECONDS), _side(b, 4.0)),
                           (_side(a, 10.0, currency=None), _side(b, 4.0))]:
        got = cost.net(savings, spend)
        assert got["value"] is None and "same unit" in got["reason"]
    got = cost.net(_side(a, 10.0), _side(b, 4.0))
    if (a == ms.SCENARIO) != (b == ms.SCENARIO):
        assert got["value"] is None and "scenario" in got["reason"].lower()
    else:
        weakest = max(a, b, key=ORDER.__getitem__)
        assert got["value"] == 6.0 and got["classification"] == weakest and got["label"] == ms.LABELS[weakest]
    for v_a, v_b in ((None, 4.0), (10.0, None), (None, None)):
        assert cost.net(_side(a, v_a), _side(b, v_b)) == {"value": None, "reason": cost.NO_NET}


# Protects net() from rows with missing keys: any subset of keys missing on either side gives a result
# (None with a reason, or a labelled value), never a KeyError.
def test_net_never_raises_on_missing_keys():
    full_a, full_b = _side(ms.MEASURED, 10.0), _side(ms.ESTIMATED, 4.0)
    for mask_a in range(16):
        for mask_b in range(16):
            sa = {k: v for i, (k, v) in enumerate(full_a.items()) if not mask_a >> i & 1}
            sb = {k: v for i, (k, v) in enumerate(full_b.items()) if not mask_b >> i & 1}
            got = cost.net(sa, sb)
            assert set(got) >= {"value", "reason"}
            if got["value"] is not None:
                assert got["label"] and got["classification"] in CLASSES
    assert cost.net({}, {}) == {"value": None, "reason": cost.NO_NET}
    assert cost.net(None, {})["value"] is None
