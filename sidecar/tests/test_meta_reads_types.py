"""``reads`` in ``[hooks]`` and ``[step]`` names the entities custom Python may read, as a list.

Before, nothing checked its type. A value saved by hand that wasn't a list of names crashed the readers:
a nested list or an inline table made ``Entity.reads()`` hold an unhashable value (TypeError in validate,
summary, run order, find_cycle, impact, lineage, column impact and OpenLineage); a bare string was split
into its characters by ``reads()`` and crashed ``ctx.read_table`` (``str + list``). Now the save gate
refuses anything but a list of entity names, and the readers keep only the names, so nothing crashes.
A bare string never worked as one name anywhere, so the gate refuses it too; a file already saved with
one is read as that one name."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.meta import change_impact, lineage, openlineage
from fabric_desktop.meta import project as P
from fabric_desktop.meta.engine import HookContext
from fabric_desktop.meta.project import validate
from fabric_desktop.service import Service

SECTIONS = {
    "hooks": '[hooks]\nbefore_write = "transforms.sales:fix"\nowner = "me"\ntest = "tests/test_sales.py"\n',
    "step": '[step]\ncustom = "transforms.sales:build"\nowner = "me"\ntest = "tests/test_sales.py"\n',
}


def _message(entity, section):
    return f'{entity}: [{section}] reads takes a list of entity names, like reads = ["silver.customers"]'


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _set_reads(svc, name, section, value):
    path = svc._project().entities_dir / f"{name}.toml"
    path.write_text(path.read_text(encoding="utf-8") + f"\n{SECTIONS[section]}reads = {value}\n", encoding="utf-8")


def _ctx(project, name):
    engine = SimpleNamespace(run_id="r1", load_date=None, store=SimpleNamespace(env="dev"),
                             read_entity=lambda n: None)
    return HookContext(engine, project.entities[name])


def _reads_errors(project, name):
    return [x for x in validate(project) if x.startswith(f"{name}:") and "reads" in x]


def _touch_every_reader(svc, project):
    """Everything that follows reads: none of these may crash."""
    project.summary()
    P.run_order(project)
    P.find_cycle(project)
    for n in project.entities:
        P.impact(project, n)
    lineage.entity_columns(project, "gold.fact_sales")
    lineage.downstream(project, "silver.customers", "customer_id")
    change_impact.column_impact(project, "silver.customers", "customer_id", "drop")
    runs = [{"run_id": f"r{i}", "entity": n, "status": "ok", "started_at": "2026-11-10T10:00:00+00:00", "seconds": 1}
            for i, n in enumerate(sorted(project.entities))]
    openlineage.run_events(project, runs, "dev")
    svc.meta_entity("gold.fact_sales")


# ---- well-formed: unchanged -------------------------------------------------------------------------------

@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_a_list_of_names_passes_and_reads_as_before(svc, section):
    _set_reads(svc, "gold.fact_sales", section, '["silver.customers"]')
    project = svc._project()
    assert _reads_errors(project, "gold.fact_sales") == []
    e = project.entities["gold.fact_sales"]
    assert e.reads() == ["silver.orders", "silver.customers", "gold.dim_customer"]
    order = P.run_order(project)
    assert order.index("silver.customers") < order.index("gold.fact_sales")
    assert "gold.fact_sales" in P.impact(project, "silver.customers")
    with pytest.raises(LookupError):  # allowed: it goes on to read (nothing loaded here)
        _ctx(project, "gold.fact_sales").read_table("silver.customers")
    with pytest.raises(PermissionError):
        _ctx(project, "gold.fact_sales").read_table("bronze.orders")
    _touch_every_reader(svc, project)


def test_an_empty_list_passes(svc):
    _set_reads(svc, "gold.fact_sales", "hooks", "[]")
    assert _reads_errors(svc._project(), "gold.fact_sales") == []


def test_hooks_and_step_reads_both_count(svc):
    path = svc._project().entities_dir / "gold.fact_sales.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + SECTIONS["hooks"] + 'reads = ["silver.customers"]\n'
                    + SECTIONS["step"] + 'reads = ["bronze.orders"]\n', encoding="utf-8")
    project = svc._project()
    assert project.entities["gold.fact_sales"].reads() == ["silver.orders", "silver.customers", "bronze.orders",
                                                           "gold.dim_customer"]
    ctx = _ctx(project, "gold.fact_sales")
    for name in ("silver.customers", "bronze.orders"):
        with pytest.raises(LookupError):
            ctx.read_table(name)


# ---- malformed: a gate error, never a crash ----------------------------------------------------------------

ODD = ['[["silver.customers"]]', '[{ a = "silver.customers" }]', "[5]", '[""]', "5", '{ a = "silver.customers" }',
       '["silver.customers", 5]', '["silver.customers", ["bronze.orders"]]', "true"]


@pytest.mark.parametrize("section", sorted(SECTIONS))
@pytest.mark.parametrize("value", ODD)
def test_an_odd_reads_is_a_gate_error_not_a_crash(svc, section, value):
    _set_reads(svc, "gold.fact_sales", section, value)
    project = svc._project()  # was TypeError: unhashable type 'list' / 'dict' for nested values
    assert _message("gold.fact_sales", section) in validate(project)
    assert _message("gold.fact_sales", section) in project.summary()["errors"]
    e = project.entities["gold.fact_sales"]
    assert all(isinstance(r, str) and r for r in e.reads())
    _touch_every_reader(svc, project)
    with pytest.raises(PermissionError):  # was TypeError (str + list), or allowed by a stray substring
        _ctx(project, "gold.fact_sales").read_table("bronze.customers")


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_names_among_odd_values_are_kept(svc, section):
    _set_reads(svc, "gold.fact_sales", section, '["silver.customers", 5, ["x"], { a = 1 }, ""]')
    project = svc._project()
    assert project.entities["gold.fact_sales"].reads() == ["silver.orders", "silver.customers", "gold.dim_customer"]
    assert P.run_order(project).index("silver.customers") < P.run_order(project).index("gold.fact_sales")
    with pytest.raises(LookupError):
        _ctx(project, "gold.fact_sales").read_table("silver.customers")


# ---- a bare string ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_a_bare_string_is_refused_by_the_gate_and_read_as_one_name(svc, section):
    _set_reads(svc, "gold.fact_sales", section, '"silver.customers"')
    project = svc._project()
    # was: reads() split it into characters ("reads s, which is not a managed entity", ...)
    assert _reads_errors(project, "gold.fact_sales") == [_message("gold.fact_sales", section)]
    assert project.entities["gold.fact_sales"].reads() == ["silver.orders", "silver.customers", "gold.dim_customer"]
    order = P.run_order(project)
    assert order.index("silver.customers") < order.index("gold.fact_sales")
    with pytest.raises(LookupError):  # was TypeError: can only concatenate str (not "list") to str
        _ctx(project, "gold.fact_sales").read_table("silver.customers")
    with pytest.raises(PermissionError):  # was allowed as a substring when both sections held a string
        _ctx(project, "gold.fact_sales").read_table("silver")
    _touch_every_reader(svc, project)


def test_save_entity_reports_the_gate_message(svc):
    path = svc._project().entities_dir / "gold.fact_sales.toml"
    text = path.read_text(encoding="utf-8") + "\n" + SECTIONS["step"] + 'reads = [["silver.customers"]]\n'
    out = svc.meta_save_entity("gold.fact_sales", text)
    assert _message("gold.fact_sales", "step") in out["errors"]


# ---- a name that isn't an entity ----------------------------------------------------------------------------

@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_an_unknown_name_is_the_existing_gate_error(svc, section):
    _set_reads(svc, "gold.fact_sales", section, '["silver.nope"]')
    project = svc._project()
    assert _reads_errors(project, "gold.fact_sales") == ["gold.fact_sales: reads silver.nope, which is not a managed entity"]
    assert "silver.nope" not in P.run_order(project)
    _touch_every_reader(svc, project)
