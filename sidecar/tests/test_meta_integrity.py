"""Two integrity holes in metadata projects:

1. An entity with [[keys]] and a list source (``[source] entity = [...]``) crashed merge_sql with
   ``TypeError: unhashable type: 'list'``, after meta_save_entity had already written the file, so the entity
   could no longer be opened. The engine reads business keys from one source entity only (a list crashes its
   read too), so the save gate now names the problem, and a save that would leave an entity unopenable is
   rolled back.
2. find_cycle and run_order recursed once per entity in a chain and hit RecursionError past ~1,000. They are
   iterative now, with the same output order as the recursive versions (kept here as the reference)."""

from __future__ import annotations

import random
import sys

import pytest

from fabric_desktop.meta import keys as K
from fabric_desktop.meta import project as P
from fabric_desktop.meta.project import GateError, validate
from fabric_desktop.service import Service
from tests.test_lineage_scale import write_project

LIST_SOURCE = 'entity = ["bronze.orders", "bronze.customers"]'


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _orders(svc):
    return svc._project().entities_dir / "silver.orders.toml"


# ---- 1. [[keys]] with a list source ---------------------------------------------------------------------

def test_saving_keys_with_a_list_source_keeps_the_entity_openable(svc):
    path = _orders(svc)
    text = path.read_text(encoding="utf-8").replace('entity = "bronze.orders"', LIST_SOURCE)
    out = svc.meta_save_entity("silver.orders", text)  # was TypeError: unhashable type: 'list'
    assert out["keys_sql"] is None
    assert any("one source entity" in x for x in out["errors"]), out["errors"]
    again = svc.meta_entity("silver.orders")
    assert again["text"] == text and again["keys_sql"] is None


def test_the_save_gate_and_the_key_notebook_refuse_a_list_source_with_keys(svc):
    path = _orders(svc)
    path.write_text(path.read_text(encoding="utf-8").replace('entity = "bronze.orders"', LIST_SOURCE), encoding="utf-8")
    project = svc._project()
    errors = [x for x in validate(project) if x.startswith("silver.orders:")]
    assert errors == [("silver.orders: [[keys]] reads business keys from one source entity, not a list "
                       "(bronze.orders, bronze.customers)")]
    with pytest.raises(GateError):
        svc.meta_key_notebook("silver.orders")
    with pytest.raises(ValueError, match="one source entity"):
        K.merge_sql(project, project.entities["silver.orders"])


def test_a_one_item_list_is_refused_too_as_the_engine_cannot_read_it(svc):
    path = _orders(svc)
    path.write_text(path.read_text(encoding="utf-8").replace('entity = "bronze.orders"', 'entity = ["bronze.orders"]'),
                    encoding="utf-8")
    assert any("one source entity" in x for x in validate(svc._project()))


def test_a_list_source_without_keys_is_not_a_keys_problem(svc):
    path = svc._project().entities_dir / "gold.fact_sales.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('entity = "silver.orders"',
                                                             'entity = ["silver.orders", "silver.customers"]'),
                    encoding="utf-8")
    assert not [x for x in K.validate_keys(svc._project()) if "one source entity" in x]


def test_a_save_that_cannot_be_opened_is_rolled_back(svc, monkeypatch):
    path = _orders(svc)
    before = path.read_text(encoding="utf-8")

    def broken(*a, **k):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr(K, "merge_sql", broken)
    with pytest.raises(ValueError, match="Not saved"):
        svc.meta_save_entity("silver.orders", before.replace('owner = "me"', 'owner = "ana"'))
    assert path.read_text(encoding="utf-8") == before
    monkeypatch.undo()
    assert svc.meta_entity("silver.orders")["name"] == "silver.orders"


def test_a_rename_that_cannot_be_opened_puts_the_old_file_back(svc, monkeypatch):
    ents = svc._project().entities_dir
    old = ents / "gold.fact_sales.toml"
    before = old.read_text(encoding="utf-8")
    real = type(svc).meta_entity

    def broken(self, name, env="dev"):
        if name == "gold.fact_sales2":
            raise TypeError("boom")
        return real(self, name, env)
    monkeypatch.setattr(type(svc), "meta_entity", broken)
    with pytest.raises(ValueError, match="Not saved"):
        svc.meta_save_entity("gold.fact_sales", before.replace('"gold.fact_sales"', '"gold.fact_sales2"'))
    assert old.read_text(encoding="utf-8") == before
    assert not (ents / "gold.fact_sales2.toml").exists()


# ---- 2. find_cycle and run_order on long chains ----------------------------------------------------------

def _old_find_cycle(project):
    """The recursive version this replaced: the reference for the order."""
    graph = P._graph(project)
    state, stack = {}, []

    def visit(n):
        state[n] = 1
        stack.append(n)
        for m in graph[n]:
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if m not in state and (c := visit(m)):
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(graph):
        if n not in state and (c := visit(n)):
            return c
    return None


def _old_run_order(project):
    graph = P._graph(project)
    out, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for m in graph[n]:
            visit(m)
        out.append(n)

    for n in sorted(graph, key=lambda n: (project._layer_pos(project.entities[n]), n)):
        visit(n)
    return out


def _random_project(root, rng, n, dag):
    names = [f"{rng.choice(['bronze', 'silver', 'gold', 'nolayer'])}.t{i:03d}" for i in range(n)]
    ents = {}
    for i, name in enumerate(names):
        pool = names[i + 1:] if dag else names
        reads = rng.sample(pool, min(len(pool), rng.randrange(4)))
        spec = {"source": {"entity": reads}} if reads else {"source": {"files": "landing/x/*.csv"}}
        if reads and rng.random() < 0.3:  # the same read twice, through a step too
            spec["step"] = {"reads": [rng.choice(reads)]}
        ents[name] = spec
    return write_project(root, ents)


@pytest.mark.parametrize("seed", range(40))
def test_the_order_is_the_same_as_the_recursive_version_on_random_graphs(tmp_path, seed):
    rng = random.Random(seed)
    project = _random_project(tmp_path / "p", rng, rng.randrange(1, 60), dag=seed % 2 == 0)
    assert P.find_cycle(project) == _old_find_cycle(project)
    if seed % 2 == 0:
        assert P.find_cycle(project) is None
        assert P.run_order(project) == _old_run_order(project)


def test_the_order_of_the_sample_project_is_unchanged(svc):
    project = svc._project()
    assert P.run_order(project) == _old_run_order(project)
    assert P.find_cycle(project) is None and _old_find_cycle(project) is None


def _chain(root, n, cycle_back=None):
    """silver.e0000 reads e0001, which reads e0002, ... (each one deeper); the last may read an earlier one."""
    names = [f"silver.e{i:04d}" for i in range(n)]
    ents = {name: {"source": {"entity": names[i + 1]}} for i, name in enumerate(names[:-1])}
    ents[names[-1]] = ({"source": {"entity": names[cycle_back]}} if cycle_back is not None
                       else {"source": {"files": "landing/x/*.csv"}})
    return write_project(root, ents), names


def test_a_3000_entity_chain_orders_without_recursing(tmp_path):
    project, names = _chain(tmp_path / "c", 3000)
    assert sys.getrecursionlimit() < 3000
    assert P.find_cycle(project) is None
    assert P.run_order(project) == names[::-1]
    assert project.summary()["run_order"] == names[::-1]


def test_a_cycle_at_the_end_of_a_long_chain_is_found(tmp_path):
    project, names = _chain(tmp_path / "c", 3000, cycle_back=2997)
    assert P.find_cycle(project) == names[2997:] + [names[2997]]
    assert any("cycle" in x for x in validate(project))
    assert project.summary()["run_order"] == []

