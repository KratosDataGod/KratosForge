"""Lineage, change impact and the OpenLineage export on big and random projects: they finish in time, never
recurse past Python's limit, give the same bytes every time, and keep the graph's invariants.

Projects are written as entity files directly (the way a person or Git could leave them), seeded, so a
failure repeats."""

import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

import pytest

from fabric_desktop.meta import change_impact, lineage, openlineage
from fabric_desktop.meta.project import Project, impact as entity_impact
from tests.test_openlineage import _all_facets, spec  # noqa: F401 - the fixture is used by name

SIDECAR = Path(__file__).resolve().parent.parent


# ---- writing projects -------------------------------------------------------------------------------------

def _q(v):
    return json.dumps(v, ensure_ascii=False)  # a JSON string is a TOML basic string


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_q(k)} = {_value(x)}" for k, x in v.items()) + " }"
    return _q(v)


def toml(spec):
    """Enough TOML for an entity file: scalars, [tables], [contract.columns] and [[arrays of tables]]."""
    lines = [f"{_q(k)} = {_value(v)}" for k, v in spec.items() if not isinstance(v, (dict, list))]
    for k, v in spec.items():
        if isinstance(v, dict):
            lines.append(f"\n[{_q(k)}]")
            lines += [f"{_q(a)} = {_value(b)}" for a, b in v.items() if not isinstance(b, dict)]
            for a, b in v.items():
                if isinstance(b, dict):
                    lines.append(f"\n[{_q(k)}.{_q(a)}]")
                    lines += [f"{_q(c)} = {_value(d)}" for c, d in b.items()]
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for item in v:
                lines.append(f"\n[[{_q(k)}]]")
                lines += [f"{_q(a)} = {_value(b)}" for a, b in item.items()]
        elif isinstance(v, list):
            lines.insert(0, f"{_q(k)} = {_value(v)}")
    return "\n".join(lines) + "\n"


def write_project(root, entities, layers=("bronze", "silver", "gold")):
    """A project of ``entities`` ({name: spec}), every layer allowed to read its own (as a DAG)."""
    ents = Path(root) / "metadata" / "entities"
    ents.mkdir(parents=True, exist_ok=True)
    (Path(root) / "layers.toml").write_text(
        '[rules]\nsame_layer_reads = "dag"\n' + "".join(f'\n[[layer]]\nname = "{n}"\n' for n in layers), encoding="utf-8")
    for name, s in entities.items():
        (ents / f"{name}.toml").write_text(toml({"name": name, "owner": "me", **s}), encoding="utf-8")
    return Project(root)


def big_project(root, rng, n=100, k=30):
    """~10,000 lineage nodes: ``n`` bronze tables of ``k`` columns from files; ``n`` silver tables with a
    rename, a clean and a chain of derives, some reading two bronze tables; ``n // 4`` Type 2 dimensions
    and the rest facts with lookups into them, a quarter of the facts reading the one before (a chain)."""
    ents = {}
    base = [f"c{j}" for j in range(k)]
    for i in range(n):
        ents[f"bronze.b{i}"] = {"source": {"files": f"landing/b{i}/*.csv"}, "load": {"mode": "full"},
                                "contract": {"keys": ["c0"], "columns": {c: "int64" for c in base}}}
    for i in range(n):
        srcs = [f"bronze.b{i}"] + ([f"bronze.b{(i + 1) % n}"] if i % 7 == 0 else [])
        cols = {c: "int64" for c in base if c != "c1"} | {"r1": "int64"}
        derives = [{"column": "d0", "expr": "c0 + r1"}]
        derives += [{"column": f"d{j}", "expr": f"d{j - 1} * 2 + c{j + 1}"} for j in range(1, 6)]
        cols |= {d["column"]: "int64" for d in derives}
        ents[f"silver.s{i}"] = {"source": {"entity": srcs if len(srcs) > 1 else srcs[0], "rename": {"c1": "r1"}},
                                "load": {"mode": "full"}, "contract": {"keys": ["c0"], "columns": cols},
                                "clean": [{"column": "c2", "trim": True}], "derive": derives}
    dims = n // 4
    base = [c for c in base if c != "c1"] + ["r1"]  # as silver has them
    for i in range(dims):
        ents[f"gold.dim{i}"] = {"source": {"entity": f"silver.s{i}"}, "load": {"mode": "scd2"},
                                "scd": {"surrogate_key": "sk", "tracked": ["c3"]},
                                "contract": {"keys": ["c0"], "columns": {"sk": "int64"} | {c: "int64" for c in base[:k // 2]}}}
    for i in range(dims, n):
        chained = i % 4 == 0 and i > dims
        src = f"gold.f{i - 4}" if chained and f"gold.f{i - 4}" in ents else f"silver.s{rng.randrange(n)}"
        d = rng.randrange(dims)
        cols = {c: "int64" for c in base[: k - 3]} | {"d5": "int64", f"dim{d}_sk": "int64"}
        ents[f"gold.f{i}"] = {"source": {"entity": src}, "load": {"mode": "full"},
                              "contract": {"keys": ["c0"], "columns": cols},
                              "lookup": [{"dim": f"gold.dim{d}", "on": "c0", "as": f"dim{d}_sk"}]}
    return write_project(root, ents)


def node_count(project):
    return sum(len(lineage.entity_columns(project, n)) for n in project.entities)


def runs_for(project, shuffle=None):
    """Two runs a day apart for every entity, and a third that started at the same moment as the later one
    (written with another offset), so which run is last is a tie the order of the rows mustn't decide."""
    starts = ["2026-11-10T10:00:00+00:00", "2026-11-11T10:00:00+00:00", "2026-11-11T11:00:00+01:00"]
    runs = [{"run_id": f"r-{e}-{j}", "entity": e, "status": ("ok", "failed")[j % 2], "started_at": starts[j],
             "seconds": j} for e in sorted(project.entities) for j in range(3)]
    if shuffle is not None:
        shuffle.shuffle(runs)
    return runs


def jsonl(out):
    return "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in out["events"])


def validate_all(spec, events):
    for ev in events:
        spec(ev, ev["schemaURL"])
        for facet in _all_facets(ev):
            spec(facet, facet["_schemaURL"])


# ---- 1. scale --------------------------------------------------------------------------------------------

def test_ten_thousand_nodes_export_in_time_and_every_event_validates(tmp_path, spec):
    project = big_project(tmp_path / "big", random.Random(1))
    assert 9_000 <= node_count(project) <= 11_000
    t0 = time.perf_counter()
    out = openlineage.run_events(project, runs_for(project), "dev")
    took = time.perf_counter() - t0
    # measured about 0.5 s on a dev container; the bound only catches a blow-up (quadratic or worse)
    assert took < 15, f"run_events took {took:.1f} s for {len(out['events'])} events"
    assert len(out["events"]) == len(project.entities) and out["left_out"] == []
    validate_all(spec, out["events"])
    for ws in ("5b9c1d2e-3f4a-4b5c-8d6e-7f8091a2b3c4",):
        validate_all(spec, openlineage.run_events(project, runs_for(project), "dev", ws)["events"][:20])
    # the longest chain: a fact read by a fact read by a fact ..., back to files
    last = max((n for n in project.entities if n.startswith("gold.f")), key=lambda n: int(n[6:]))
    t0 = time.perf_counter()
    up = lineage.upstream(project, last, "d5")
    down = lineage.downstream(project, "bronze.b0", "c0")
    impact = change_impact.column_impact(project, "bronze.b0", "c0", "drop")
    assert time.perf_counter() - t0 < 10
    assert up[-1]["inputs"][0]["origin"] == "files" and len({(s["entity"], s["column"]) for s in up}) == len(up)
    assert ("silver.s0", "d5") in {(d["entity"], d["column"]) for d in down}
    assert impact["counts"]["breaks"] >= 1


def chain_project(root, depth):
    """A chain of ``depth`` tables, each reading the one before, and a table with ``depth`` derives in a row."""
    ents = {"bronze.e0": {"source": {"files": "landing/e/*.csv"}, "contract": {"columns": {"a": "int64"}}}}
    for i in range(1, depth):
        ents[f"bronze.e{i}"] = {"source": {"entity": f"bronze.e{i - 1}"}, "contract": {"columns": {"a": "int64"}}}
    ents["silver.long"] = {
        "source": {"entity": "bronze.e0"},
        "contract": {"columns": {"a": "int64", **{f"x{j}": "int64" for j in range(depth)}}},
        "derive": [{"column": "x0", "expr": "a + 1"}] + [{"column": f"x{j}", "expr": f"x{j - 1} + 1"} for j in range(1, depth)]}
    return write_project(root, ents)


def test_a_chain_longer_than_the_recursion_limit(tmp_path, spec):
    """Found here: upstream recursed once per step and the export once per own column, so a chain past
    ~1,000 steps raised RecursionError, and upstream worked out a table's columns again at every step
    (12 s for 1,500 derives in a row). Both walk with a stack now, upstream once per table."""
    depth = sys.getrecursionlimit() + 500
    project = chain_project(tmp_path / "deep", depth)
    t0 = time.perf_counter()
    up = lineage.upstream(project, f"bronze.e{depth - 1}", "a")
    assert [s["entity"] for s in up] == [f"bronze.e{i}" for i in reversed(range(depth))]
    up = lineage.upstream(project, "silver.long", f"x{depth - 1}")
    assert [s["column"] for s in up][:3] == [f"x{depth - 1}", f"x{depth - 2}", f"x{depth - 3}"]
    assert (up[-1]["entity"], up[-1]["column"]) == ("bronze.e0", "a") and len(up) == depth + 2  # x..., then silver.long.a
    down = lineage.downstream(project, "bronze.e0", "a")
    assert len(down) == (depth - 1) + 1 + depth  # every table in the chain, silver.long.a and every derive
    impact = change_impact.column_impact(project, "bronze.e0", "a", "drop")
    assert {i["name"] for i in impact["items"]} == {"bronze.e1", "silver.long", *(f"bronze.e{i}" for i in range(2, depth))}
    out = openlineage.run_events(project, runs_for(project), "dev")
    long = next(e for e in out["events"] if e["job"]["name"] == "silver.long")
    last = long["outputs"][0]["facets"]["columnLineage"]["fields"][f"x{depth - 1}"]["inputFields"]
    assert [(f["name"], f["field"]) for f in last] == [("LH_Bronze.dbo.e0", "a")]
    validate_all(spec, [long])
    took = time.perf_counter() - t0
    assert took < 15, f"{took:.1f} s"  # measured about 1 s


# ---- 5. determinism --------------------------------------------------------------------------------------

_EXPORT = """
import hashlib, json, random, sys
from pathlib import Path
from fabric_desktop.meta import openlineage
from fabric_desktop.meta.project import Project
from tests.test_lineage_scale import jsonl, runs_for
project = Project(Path(sys.argv[1]))
runs = runs_for(project, random.Random(int(sys.argv[2])))
print(hashlib.sha256(jsonl(openlineage.run_events(project, runs, "dev")).encode()).hexdigest())
"""


def test_the_export_is_the_same_bytes_every_time(tmp_path):
    project = big_project(tmp_path / "det", random.Random(5), n=24, k=12)
    first = jsonl(openlineage.run_events(project, runs_for(project), "dev"))
    assert first == jsonl(openlineage.run_events(project, runs_for(project), "dev"))
    for seed in range(3):  # the runs in any order
        assert jsonl(openlineage.run_events(project, runs_for(project, random.Random(seed)), "dev")) == first
    assert jsonl(openlineage.run_events(Project(project.root), runs_for(project)[::-1], "dev")) == first
    # other string hashes (set and dict order of str keys), in fresh processes
    want = __import__("hashlib").sha256(first.encode()).hexdigest()
    procs = [subprocess.Popen([sys.executable, "-c", _EXPORT, str(project.root), str(seed)], cwd=SIDECAR,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env={**os.environ, "PYTHONHASHSEED": str(seed)})
             for seed in (0, 1, 12345)]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        assert out.strip() == want


def test_a_tie_on_the_start_time_is_not_decided_by_the_rows_order(tmp_path):
    """Found here: two runs of one entity that started at the same moment were exported as whichever row came
    last, so the same run log in another order gave another file."""
    project = big_project(tmp_path / "tie", random.Random(5), n=4, k=3)
    runs = [{"run_id": "a", "entity": "bronze.b0", "status": "ok", "started_at": "2026-11-11T10:00:00+00:00"},
            {"run_id": "b", "entity": "bronze.b0", "status": "failed", "started_at": "2026-11-11T11:00:00+01:00"}]
    one, other = (openlineage.run_events(project, r, "dev")["events"] for r in (runs, runs[::-1]))
    assert one == other and one[0]["eventType"] == "FAIL"  # run b: the same moment, and b sorts after a


def test_the_service_export_writes_the_same_bytes_for_the_same_runs(tmp_path, monkeypatch):
    import polars as pl

    from fabric_desktop import known_folders
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    rows = runs_for(svc._project())

    class Store:  # the run log as the store holds it, rows in the order given
        def __init__(self, rows):
            self.rows = rows

        def meta(self, table):
            assert table == "run_log"
            return pl.DataFrame(self.rows)

    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads")
    files = []
    for order in (rows, rows[::-1], random.Random(3).sample(rows, len(rows))):
        monkeypatch.setattr(svc, "_store", lambda project, env, order=order: Store(order))
        files.append(Path(svc.meta_openlineage_export("dev")["path"]).read_bytes())
    assert len(set(files)) == 1 and files[0].count(b"\n") == 6


# ---- 6. properties of the graph, on seeded random projects ----------------------------------------------

POOL = ["id", "a", "b", "c", "amount", "when", "k", "valid_from", "_run_id"]


def random_project(root, rng):
    """Up to 12 entities over three layers, written by hand: sources (files, one or two entities, now and
    then a later one, itself, or one that isn't there), renames, derives (some unreadable), lookups, keys."""
    layers = ("bronze", "silver", "gold")
    names = [f"{layers[min(i // 4, 2)]}.t{i}" for i in range(rng.randint(3, 12))]
    ents = {}
    for i, name in enumerate(names):
        cols = rng.sample(POOL, rng.randint(1, 5))
        spec = {"contract": {"keys": cols[:1], "columns": {c: "int64" for c in cols}}}
        if i == 0 or rng.random() < 0.2:
            spec["source"] = {"files": f"landing/{name}/*.csv"}
        else:
            pick = rng.random()
            if pick < 0.1:
                srcs = [name]  # itself
            elif pick < 0.2:
                srcs = [rng.choice(names)]  # perhaps a later one: a cycle
            elif pick < 0.25:
                srcs = ["silver.gone"]
            else:
                srcs = rng.sample(names[:i], min(i, rng.randint(1, 2)))
            spec["source"] = {"entity": srcs if len(srcs) > 1 else srcs[0]}
            if rng.random() < 0.3:
                spec["source"]["rename"] = {rng.choice(POOL): cols[-1]}
        derives = []
        for _ in range(rng.randint(0, 3)):
            col = rng.choice(POOL)
            expr = rng.choice([f"{rng.choice(POOL)} + {rng.choice(POOL)}", f"coalesce({rng.choice(POOL)}, 0)",
                               "1", "((", f"COLUMNS('^{rng.choice(POOL)}.*$')", f"{col} * 2"])
            derives.append({"column": col, "expr": expr})
            spec["contract"]["columns"].setdefault(col, "int64")
        if derives:
            spec["derive"] = derives
        if rng.random() < 0.3:
            spec["load"] = {"mode": rng.choice(["scd2", "scd1", "full"])}  # full: a surrogate key that isn't made
            spec["scd"] = {"surrogate_key": "sk", "tracked": cols[:1]}
        dims = [n for n in names if n != name]
        if dims and rng.random() < 0.3:
            spec["lookup"] = [{"dim": rng.choice(dims + ["gold.nodim"]), "on": rng.choice(POOL), "as": "dim_sk"}]
        if rng.random() < 0.2:
            spec["keys"] = [{"table": "kt", "business_key": rng.choice(POOL)}]
        ents[name] = spec
    return write_project(root, ents)


def _nodes(project):
    return {(n, c): rec for n in project.entities for c, rec in lineage.entity_columns(project, n).items()}


@pytest.mark.parametrize("seed", range(int(os.environ.get("LINEAGE_SEEDS", 40))))
def test_graph_invariants_on_random_projects(tmp_path, seed):
    rng = random.Random(seed)
    project = random_project(tmp_path / "p", rng)
    nodes = _nodes(project)
    for (name, col), rec in nodes.items():
        # unknown columns have no inputs; every edge points at a column that is there, or says it is missing,
        # or at an origin that isn't an entity
        if rec["label"] == "unknown":
            assert rec["inputs"] == [], (name, col, rec)
        for i in rec["inputs"]:
            if "entity" in i:
                assert i.get("missing") or (i["entity"], i["column"]) in nodes, (name, col, i)
            else:
                assert i["origin"] in ("files", "landing", "table", "link"), (name, col, i)
        if rec["how"] == "derived" and rec["label"] == "declared":  # no edge the expression doesn't name
            named = lineage._expr_columns(rec["expr"])
            back = {v: k for k, v in (project.entities[name].spec["source"].get("rename") or {}).items()}
            assert {i["column"] for i in rec["inputs"]} <= {*named, *(back.get(c, c) for c in named)}, (name, col, rec)
    ups = {x: {(s["entity"], s["column"]) for s in lineage.upstream(project, *x)} for x in nodes}
    for x in rng.sample(sorted(nodes), min(25, len(nodes))):
        down = {(d["entity"], d["column"]) for d in lineage.downstream(project, *x)}
        for y in nodes:
            if y != x:
                assert (y in down) == (x in ups[y]), (x, y)
    for x in rng.sample(sorted(nodes), min(8, len(nodes))):
        name, col = x
        for change in ("drop", "rename", "retype"):
            res = change_impact.column_impact(project, name, col, change, "new_name", "string")
            down = {d["entity"] for d in lineage.downstream(project, name, col)}
            looks_up = {n for n, e in project.entities.items()
                        if any(isinstance(c, dict) and c.get("exists_in") == f"{name}.{col}" for c in e.spec.get("check", []))}
            tables = {i["name"] for i in res["items"] if i["kind"] == "table"}
            # never the changed column's upstream (unless a cycle brings it back downstream too). Beyond its
            # column lineage, a table that reads it may be listed: a lookup matches on the dimension's column,
            # and an expression lineage can't read may use any column.
            reads_it = set(entity_impact(project, name))
            assert tables <= {name} | down | looks_up | reads_it, (x, change, tables - {name} - down - reads_it)
