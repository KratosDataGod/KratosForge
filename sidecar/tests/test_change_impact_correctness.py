"""Column change impact (meta/change_impact.py) checked for correctness: every way a column is read, changes
followed several tables on, odd changes (a rename to a name that's taken, a change that changes nothing),
and a brute-force reference that drops each column of seeded random projects and sees what the save gate
and lineage say breaks. Nothing runs; each test works on files in its own folder."""

from __future__ import annotations

import copy
import json
import random
import re

import pytest

from fabric_desktop.meta import change_impact, lineage
from fabric_desktop.meta.project import Project, impact as entity_impact, validate
from fabric_desktop.meta.templates import init_project


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "crm", "3", "Chris", True)


def impact(proj, name, column, change="drop", **kw):
    return change_impact.column_impact(proj, name, column, change, **kw)


def rows(res):
    return {(i["severity"], i["kind"], i["name"]) for i in res["items"]}


def item(res, name, kind="table"):
    return next(i for i in res["items"] if (i["kind"], i["name"]) == (kind, name))


def why(res, name, kind="table"):
    return " / ".join(item(res, name, kind)["why"])


def write(proj, name, text):
    (proj.entities_dir / f"{name}.toml").write_text(f'name = "{name}"\nowner = "Chris"\n{text}', encoding="utf-8")


def patch(proj, name, old, new, append=""):
    path = proj.entities_dir / f"{name}.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1) + append, encoding="utf-8")


# ---------------------------------------------------------------- [[lookup]]: the dimension's side


def test_dropping_or_renaming_the_column_a_lookup_matches_on_in_the_dimension_breaks_the_fact(proj):
    # The engine reads the dimension's column named like the lookup's `on` (customer_id) with its surrogate
    # key, so a dimension without it fails every fact that looks it up.
    for change, kw in (("drop", {}), ("rename", {"new_name": "cust_id"})):
        res = impact(proj, "gold.dim_customer", "customer_id", change, **kw)
        assert ("breaks", "table", "gold.fact_sales") in rows(res), change
        assert "its [[lookup]] of gold.dim_customer matches rows on customer_id" in why(res, "gold.fact_sales")
        # the fact's customer_sk is built by that lookup, so its relationship goes with it
        assert "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side" in why(
            res, "SM_Gold", "model")
        assert "gold.fact_sales" not in res["table_only"]


def test_a_new_type_on_the_dimension_column_a_lookup_matches_on_is_a_check_on_the_fact(proj):
    res = impact(proj, "gold.dim_customer", "customer_id", "retype", new_type="string")
    assert item(res, "gold.fact_sales")["severity"] == "check"
    assert "the two sides of the match must keep the same type" in why(res, "gold.fact_sales")


def test_the_dimensions_surrogate_key_is_taken_by_the_lookup_not_matched_on(proj):
    res = impact(proj, "gold.dim_customer", "customer_sk", "retype", new_type="int32")
    text = why(res, "gold.fact_sales")
    assert "matches customer_sk on customer_sk" not in text
    assert "takes customer_sk from gold.dim_customer.customer_sk" in text


def test_a_new_type_carried_on_by_copies_reaches_the_lookup_and_key_further_on(proj):
    # bronze.orders.customer_id -> silver.orders.customer_id (a copy) -> gold.fact_sales matches on it, and
    # silver.orders keys it in [[keys]]: both sides of a match and the key table keep one type.
    res = impact(proj, "bronze.orders", "customer_id", "retype", new_type="string")
    assert "matches customer_sk on customer_id; the two sides of the match must keep the same type" in why(
        res, "gold.fact_sales")
    assert "uses customer_id as the business key for customer_sk; key tables keep the old type" in why(
        res, "silver.orders")


def test_a_new_type_doesnt_carry_on_through_the_surrogate_key_a_lookup_makes(proj):
    write(proj, "gold.fact_copy", '[source]\nentity = "gold.fact_sales"\n[load]\nmode = "full"\n[contract]\n'
          'keys = ["order_id"]\n[contract.columns]\norder_id = "int64"\ncustomer_sk = "int64"\n')
    proj.reload()
    res = impact(proj, "silver.orders", "customer_id", "retype", new_type="string")
    assert item(res, "gold.fact_sales")["severity"] == "check"
    assert "gold.fact_copy" not in tables(res)  # customer_sk is the dimension's key, whatever customer_id's type
    res = impact(proj, "silver.orders", "customer_id", "drop")
    assert "customer_sk is built from gold.fact_sales.customer_sk, which breaks first" in why(res, "gold.fact_copy")


# ---------------------------------------------------------------- exists_in further on


def test_an_exists_in_check_on_a_copy_further_on_is_listed(proj):
    # bronze.customers.customer_id is copied to silver.customers, which silver.orders checks against.
    for change, kw in (("drop", {}), ("retype", {"new_type": "string"})):
        res = impact(proj, "bronze.customers", "customer_id", change, **kw)
        orders = item(res, "silver.orders")
        assert orders["severity"] == "check", change
        assert "its [[check]] on customer_id looks values up in silver.customers.customer_id" in why(
            res, "silver.orders")


# ---------------------------------------------------------------- expressions that can't be read


def test_an_expression_that_cant_be_read_is_never_silently_safe(proj):
    write(proj, "gold.segments", '[source]\nentity = "silver.customers"\n[load]\nmode = "full"\n'
          '[contract]\nkeys = ["customer_id"]\n[contract.columns]\ncustomer_id = "int64"\npicked = "string"\n'
          "[[derive]]\ncolumn = \"picked\"\nexpr = \"COLUMNS('^seg.*$')\"\n")
    proj.reload()
    res = impact(proj, "silver.customers", "segment", "drop")
    seg = item(res, "gold.segments")
    assert seg["severity"] == "check"
    assert "its expression for picked couldn't be read, so whether it uses segment isn't known" in seg["why"]
    assert "gold.segments" not in res["table_only"]
    # And further on: bronze.customers.segment reaches silver.customers.segment by a copy.
    res = impact(proj, "bronze.customers", "segment", "retype", new_type="int64")
    assert "whether it uses silver.customers.segment isn't known" in why(res, "gold.segments")


def test_an_expression_that_cant_be_read_in_the_table_itself_is_a_check(proj):
    patch(proj, "silver.orders", 'net_amount = "float64"\n', 'net_amount = "float64"\nodd = "float64"\n',
          append="\n[[derive]]\ncolumn = \"odd\"\nexpr = \"COLUMNS('^am.*$')\"\n")
    proj.reload()
    res = impact(proj, "silver.orders", "status", "retype", new_type="int64")
    assert "its expression for odd couldn't be read, so whether it uses status isn't known" in why(res, "silver.orders")


def test_quoted_and_cast_identifiers_are_read_like_bare_ones(proj):
    patch(proj, "gold.fact_sales", 'modified_at = "date"\n', 'modified_at = "date"\nq = "float64"\n',
          append='\n[[derive]]\ncolumn = "q"\nexpr = "CAST(\\"status\\" AS VARCHAR)"\n')
    proj.reload()
    res = impact(proj, "silver.orders", "status", "rename", new_name="state")
    assert "its expression for q uses status; once it's renamed to state, building gold.fact_sales fails" in why(
        res, "gold.fact_sales")


# ---------------------------------------------------------------- the entity's own file


def test_a_check_expression_on_the_column_fails_the_save_gate(proj):
    res = impact(proj, "silver.orders", "amount", "drop")
    assert ("a [[check]] expression (returns_are_negative) uses amount; the save gate refuses the change until "
            "you update it") in item(res, "silver.orders")["why"]
    res = impact(proj, "silver.orders", "status", "rename", new_name="state")
    assert "a [[check]] expression (returns_are_negative) uses status" in why(res, "silver.orders")


def test_batch_by_date_column_dedupe_order_and_catalog_name_the_column(proj):
    patch(proj, "gold.dim_customer", "[contract]", '[model]\ndate_column = "since"\n\n[catalog.columns.city]\n'
          'description = "Where they live"\n\n[contract]')
    patch(proj, "gold.dim_customer", 'email = "string"\n', 'email = "string"\nsince = "date"\n')
    patch(proj, "silver.orders", "[load]\n", '[load]\nbatch_by = "order_date"\n')
    patch(proj, "silver.orders", 'dedupe_keep = "newest:modified_at"', 'dedupe_keep = "newest:amount"')
    write(proj, "bronze.since", '[source]\nfiles = "landing/x/*.csv"\n[load]\nmode = "full"\n[contract]\n'
          'keys = ["customer_id"]\n[contract.columns]\ncustomer_id = "int64"\nsince = "date"\n')
    proj.reload()
    res = impact(proj, "silver.orders", "order_date", "drop")
    assert "order_date is the [load] batch_by column" in why(res, "silver.orders")
    res = impact(proj, "silver.orders", "amount", "drop")
    assert "a [[clean]] step names amount" in why(res, "silver.orders")
    res = impact(proj, "gold.dim_customer", "city", "rename", new_name="town")
    assert "the [catalog] describes city" in why(res, "gold.dim_customer")
    res = impact(proj, "gold.dim_customer", "since", "drop")
    assert "since is the [model] date_column" in why(res, "gold.dim_customer")
    # a date table's column must stay a date
    res = impact(proj, "gold.dim_customer", "since", "retype", new_type="string")
    assert item(res, "gold.dim_customer")["severity"] == "breaks"
    assert "since is the [model] date_column, which must be a date or datetime" in why(res, "gold.dim_customer")
    res = impact(proj, "gold.dim_customer", "since", "retype", new_type="datetime")
    assert not any(i["name"] == "gold.dim_customer" and i["severity"] == "breaks" for i in res["items"])


def test_a_column_the_table_still_reads_from_its_source_doesnt_break_its_own_expression(proj):
    # silver.orders reads bronze.orders; net_amount = amount - coalesce(discount, 0) still finds discount in
    # bronze.orders once silver.orders' contract leaves it out, so net_amount (and gold's copy) still build.
    res = impact(proj, "silver.orders", "discount", "drop")
    orders = item(res, "silver.orders")
    assert orders["severity"] == "check"
    assert any("still reads discount from bronze.orders" in w for w in orders["why"])
    assert "gold.fact_sales" not in {i["name"] for i in res["items"]}
    # A rename takes the name away from the source too, so the expression breaks.
    res = impact(proj, "silver.orders", "discount", "rename", new_name="rebate")
    assert item(res, "silver.orders")["severity"] == "breaks"
    # A file source only has its contract columns, so the save gate refuses the expression.
    patch(proj, "bronze.orders", 'modified_at = "date"\n', 'modified_at = "date"\nnet = "float64"\n',
          append='\n[[derive]]\ncolumn = "net"\nexpr = "amount - discount"\n')
    proj.reload()
    res = impact(proj, "bronze.orders", "discount", "drop")
    assert item(res, "bronze.orders")["severity"] == "breaks"


# ---------------------------------------------------------------- Python further on


def test_python_further_on_that_names_the_column_is_a_check(proj):
    (proj.root / "transforms").mkdir(exist_ok=True)
    (proj.root / "transforms" / "seg.py").write_text("def run(df, ctx):\n    return df.drop('segment')\n",
                                                     encoding="utf-8")
    (proj.root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    patch(proj, "gold.dim_customer", "[contract]", '[hooks]\nafter_clean = "transforms.seg:run"\n'
          'before_write = "transforms.plain:run"\n\n[contract]')
    proj.reload()
    # bronze.customers.segment -> silver.customers.segment -> gold.dim_customer.segment, whose hook names it
    res = impact(proj, "bronze.customers", "segment", "drop")
    hook = item(res, "transforms.seg:run", "python")
    assert hook["severity"] == "check" and hook["entity"] == "gold.dim_customer"
    assert "names segment (hook after_clean of gold.dim_customer), which is built from bronze.customers.segment" \
        in hook["why"][0]
    assert not any(i["name"] == "transforms.plain:run" for i in res["items"])


# ---------------------------------------------------------------- odd changes


def test_a_rename_to_the_same_name_or_a_new_type_that_is_the_same_changes_nothing(proj):
    res = impact(proj, "silver.orders", "customer_id", "rename", new_name="customer_id")
    assert res["items"] == [] and res["counts"]["breaks"] == res["counts"]["check"] == 0
    res = impact(proj, "silver.orders", "customer_id", "retype", new_type="int64")
    assert res["items"] == []
    res = impact(proj, "silver.orders", "customer_id", "retype", new_type=" INT64 ")
    assert res["items"] == []


def test_a_rename_to_a_name_the_table_already_has_breaks_the_table(proj):
    res = impact(proj, "silver.orders", "status", "rename", new_name="amount")
    assert item(res, "silver.orders")["severity"] == "breaks"
    assert "silver.orders already has a column amount" in why(res, "silver.orders")
    # a column the framework adds is taken too
    res = impact(proj, "gold.dim_customer", "city", "rename", new_name="valid_from")
    assert "gold.dim_customer already has a column valid_from" in why(res, "gold.dim_customer")


def test_a_column_the_entity_doesnt_write_names_both(proj):
    with pytest.raises(LookupError, match="^silver.orders doesn't write nope$"):
        impact(proj, "silver.orders", "nope")


# ---------------------------------------------------------------- several tables on


def chain_project(tmp_path):
    """Five layers: a.src -> b.ren (renames v to w) -> c.der (x = w * 2) -> d.cp (copies x) -> e.top (lookup
    on x in d.dim and derive y = x + 1), with d.dim a dimension built from c.der too (a diamond into e.top)."""
    root = tmp_path / "chain"
    (root / "metadata" / "entities").mkdir(parents=True)
    layers = "".join(f'[[layer]]\nname = "{n}"\nrequired_checks = []\n\n' for n in "abcde")
    (root / "layers.toml").write_text('[rules]\nsame_layer_reads = "dag"\n\n' + layers, encoding="utf-8")
    p = Project(root)

    def ent(name, text):
        write(p, name, text)

    ent("a.src", '[source]\nfiles = "landing/a/*.csv"\n[load]\nmode = "full"\n[contract]\nkeys = ["id"]\n'
        '[contract.columns]\nid = "int64"\nv = "int64"\nu = "string"\n')
    ent("b.ren", '[source]\nentity = "a.src"\nrename = { v = "w" }\n[load]\nmode = "full"\n[contract]\n'
        'keys = ["id"]\n[contract.columns]\nid = "int64"\nw = "int64"\nu = "string"\n')
    ent("c.der", '[source]\nentity = "b.ren"\n[load]\nmode = "full"\n[contract]\nkeys = ["id"]\n'
        '[contract.columns]\nid = "int64"\nx = "int64"\nu = "string"\n[[derive]]\ncolumn = "x"\nexpr = "w * 2"\n')
    ent("d.cp", '[source]\nentity = "c.der"\n[load]\nmode = "full"\n[contract]\nkeys = ["id"]\n'
        '[contract.columns]\nid = "int64"\nx = "int64"\n')
    ent("d.dim", '[source]\nentity = "c.der"\n[load]\nmode = "scd1"\n[scd]\nsurrogate_key = "dim_sk"\n'
        '[contract]\nkeys = ["x"]\n[contract.columns]\ndim_sk = "int64"\nx = "int64"\nu = "string"\n')
    ent("e.top", '[source]\nentity = "d.cp"\n[load]\nmode = "full"\n[contract]\nkeys = ["id"]\n'
        '[contract.columns]\nid = "int64"\ny = "int64"\ndim_sk = "int64"\n[[derive]]\ncolumn = "y"\n'
        'expr = "x + 1"\n[[lookup]]\ndim = "d.dim"\non = "x"\nas = "dim_sk"\n')
    p.reload()
    return p


def test_a_drop_follows_a_rename_and_a_derive_to_the_end_of_the_chain(tmp_path):
    p = chain_project(tmp_path)
    res = impact(p, "a.src", "v")
    assert item(res, "b.ren")["severity"] == "breaks"
    assert "reads v by name from a.src" in why(res, "b.ren")
    for name in ("c.der", "d.cp", "d.dim", "e.top"):
        assert item(res, name)["severity"] == "check", name
    top = item(res, "e.top")
    assert top["chain"][0] == "a.src.v" and top["chain"][1] == "b.ren.w"
    # the diamond: e.top is listed once, whichever way it's reached
    assert [i["name"] for i in res["items"]].count("e.top") == 1


def test_a_change_in_the_middle_of_a_chain_breaks_its_readers_and_spares_whats_before(tmp_path):
    p = chain_project(tmp_path)
    res = impact(p, "c.der", "x", "rename", new_name="x2")
    names = {i["name"] for i in res["items"] if i["kind"] == "table"}
    assert names == {"d.cp", "d.dim", "e.top"}
    # e.top reads x from d.cp (a copy) and matches on d.dim.x: both are further on, so a check
    assert item(res, "d.cp")["severity"] == "breaks" and item(res, "d.dim")["severity"] == "breaks"
    assert item(res, "e.top")["severity"] == "check"
    # dropped in d.dim directly: e.top's lookup matches on it, so it breaks
    res = impact(p, "d.dim", "x")
    assert item(res, "e.top")["severity"] == "breaks"
    assert "its [[lookup]] of d.dim matches rows on x" in why(res, "e.top")
    # a diamond with one path that breaks and one that is a check keeps the worst
    res = impact(p, "d.cp", "x")
    assert item(res, "e.top")["severity"] == "breaks"
    assert [i["name"] for i in res["items"]].count("e.top") == 1


def test_a_column_renamed_then_derived_carries_a_new_type_only_through_copies(tmp_path):
    p = chain_project(tmp_path)
    res = impact(p, "a.src", "v", "retype", new_type="string")
    assert "its contract column w takes a.src.v's type as it comes" in why(res, "b.ren")
    assert "its expression for x uses w; check it works with the new type" in why(res, "c.der")
    assert {i["name"] for i in res["items"] if i["kind"] == "table"} == {"b.ren", "c.der"}
    assert res["counts"]["breaks"] == 0


# ---------------------------------------------------------------- order


def test_the_answer_is_the_same_whatever_order_the_entities_are_in(proj):
    names = list(proj.entities)
    want = {(n, c, ch): impact(proj, n, c, ch, new_name="zz", new_type="string")
            for n in names for c in proj.entities[n].columns for ch in change_impact.CHANGES}
    rng = random.Random(7)
    for _ in range(3):
        order = names[:]
        rng.shuffle(order)
        proj.entities = {n: proj.entities[n] for n in order}
        for (n, c, ch), res in want.items():
            assert impact(proj, n, c, ch, new_name="zz", new_type="string") == res, (n, c, ch, order)


# ---------------------------------------------------------------- a brute-force reference on random projects

POOL = ["a", "b", "c", "d", "e", "f"]
TYPES = ["int64", "string", "float64", "date"]


def _term(rng, col):
    style = rng.random()
    if style < 0.08:
        return f"COLUMNS('^{col}.*$')"  # picks columns by pattern: never read for its columns
    if style < 0.3:
        return f'"{col}"'
    if style < 0.45:
        return f"CAST({col} AS VARCHAR)"
    if style < 0.6:
        return f"coalesce({col}, 0)"
    return col


def random_project(root, seed):
    """Four layers of two or three tables. Above the first, each copies some of what one earlier table writes
    (sometimes through [source] rename), derives columns from it, and may be a dimension, look one up, check
    values exist in another table or check an expression."""
    rng = random.Random(seed)
    (root / "metadata" / "entities").mkdir(parents=True)
    layers = "".join(f'[[layer]]\nname = "l{k}"\nrequired_checks = []\n\n' for k in range(4))
    (root / "layers.toml").write_text('[rules]\nsame_layer_reads = "dag"\n\n' + layers, encoding="utf-8")
    specs: dict[str, dict] = {}
    written: dict[str, dict[str, str]] = {}
    dims: list[tuple[str, str, str]] = []
    for k in range(4):
        for i in range(rng.randint(2, 3)):
            name = f"l{k}.t{i}"
            spec: dict = {"name": name, "owner": "Chris", "load": {"mode": "full"}}
            if k == 0:
                spec["source"] = {"files": f"landing/t{i}/*.csv"}
                cols = {"id": "int64", **{c: rng.choice(TYPES) for c in rng.sample(POOL, rng.randint(3, 5))}}
            else:
                up = rng.choice([n for n in specs if int(n[1]) < k])
                spec["source"] = {"entity": up}
                have = written[up]
                others = [c for c in have if c != "id"]
                cols = {"id": "int64", **{c: have[c] for c in rng.sample(others, rng.randint(1, len(others)))}}
                if rng.random() < 0.2 and len(cols) > 1:
                    old = rng.choice([c for c in cols if c != "id"])
                    spec["source"]["rename"] = {old: f"{old}_r"}
                    cols = {(f"{old}_r" if c == old else c): t for c, t in cols.items()}
                readable = list(have)
                for j in range(rng.randint(0, 2)):
                    col = f"x{k}{i}{j}"
                    terms = [_term(rng, c) for c in rng.sample(readable, min(len(readable), rng.randint(1, 2)))]
                    spec.setdefault("derive", []).append({"column": col, "expr": " + ".join(terms)})
                    cols[col] = "float64"
                    readable.append(col)
                earlier = [d for d in dims if int(d[0][1]) < k and d[1] in readable]
                if earlier and rng.random() < 0.5:
                    dim, key, sk = rng.choice(earlier)
                    spec["lookup"] = [{"dim": dim, "on": key, "as": sk}]
                    cols[sk] = "int64"
                if k < 3 and rng.random() < 0.3:
                    sk = f"{name.split('.')[1]}{k}_sk"
                    key = rng.choice(list(cols))
                    spec["load"] = {"mode": "scd1"}
                    spec["scd"] = {"surrogate_key": sk}
                    cols[sk] = "int64"
                    dims.append((name, key, sk))
                checks = []
                if rng.random() < 0.3:
                    target = rng.choice([n for n in specs if int(n[1]) < k])
                    common = [c for c in written[target] if c in cols]
                    if common:
                        col = rng.choice(common)
                        checks.append({"column": col, "exists_in": f"{target}.{col}", "on_fail": "warn"})
                if rng.random() < 0.3:
                    checks.append({"name": "has", "expr": f"{rng.choice(list(cols))} is not null", "on_fail": "warn"})
                if checks:
                    spec["check"] = checks
            key = next((d[1] for d in dims if d[0] == name), "id")
            spec["contract"] = {"keys": [key], "columns": cols}
            specs[name] = spec
            written[name] = cols
    for name, spec in specs.items():
        (root / "metadata" / "entities" / f"{name}.toml").write_text(_toml(spec), encoding="utf-8")
    return Project(root)


def _toml(spec):
    def val(v):
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{json.dumps(k)} = {val(x)}" for k, x in v.items()) + " }"
        return json.dumps(v)

    lines = [f"{k} = {val(v)}" for k, v in spec.items() if not isinstance(v, (dict, list))]
    for k, v in spec.items():
        if isinstance(v, dict):
            lines.append(f"[{k}]")
            lines += [f"{kk} = {val(vv)}" for kk, vv in v.items()]
    for k, v in spec.items():
        if isinstance(v, list):
            for block in v:
                lines.append(f"[[{k}]]")
                lines += [f"{kk} = {val(vv)}" for kk, vv in block.items()]
    return "\n".join(lines) + "\n"


def _errors(project):
    out: dict[str, set[str]] = {}
    for err in validate(project):
        ent = err.split(": ", 1)[0]
        out.setdefault(ent, set()).add(err)
    return out


def _broken(project, base):
    """Tables a changed project no longer builds: the save gate has something new to say about them, lineage
    finds a column they read gone, or a lookup's dimension no longer has the column it matches on (the
    engine reads the dimension's column named like ``on``). Returns (tables, their broken columns)."""
    now = _errors(project)
    tables = {n for n, errs in now.items() if errs - base.get(n, set())}
    cols: set[tuple[str, str]] = set()
    for n, errs in now.items():
        for err in errs - base.get(n, set()):
            m = re.match(r"^(\S+): derive (\S+): uses unknown column", err)
            if m:  # lineage can't read every expression; the save gate names the derive that fails
                cols.add((m[1], m[2]))
    for n in project.entities:
        for col, rec in lineage.entity_columns(project, n).items():
            if any(i.get("missing") for i in rec["inputs"]):
                cols.add((n, col))
        for lk in project.entities[n].spec.get("lookup", []):
            if lk["on"] not in lineage.entity_columns_names(project, lk["dim"]):
                cols.add((n, lk["as"]))
    return tables | {n for n, _ in cols}, cols


def _without(spec, col):
    """``spec`` without ``col`` and whatever makes it."""
    spec["contract"]["columns"].pop(col, None)
    spec["derive"] = [d for d in spec.get("derive", []) if d["column"] != col]
    spec["lookup"] = [lk for lk in spec.get("lookup", []) if lk["as"] != col]


def brute_force(project, base, name, change_spec):
    """Apply a change to ``name``'s spec, then take out every column that breaks, again and again: the
    tables that break first, and every table that breaks on the way."""
    saved = {n: copy.deepcopy(e.spec) for n, e in project.entities.items()}
    try:
        change_spec(project.entities[name].spec)
        first, cols = _broken(project, base)
        every = set(first)
        while cols:
            for n, col in cols:
                _without(project.entities[n].spec, col)
            more, new_cols = _broken(project, base)
            every |= more
            cols = {c for c in new_cols if c not in cols}
        return first, every
    finally:
        for n, spec in saved.items():
            project.entities[n].spec = spec


def reach(project, name, column):
    """Tables with a column that lineage traces back to ``name.column``, or that match a lookup on, or check
    values exist in, such a column."""
    built = {(n, c) for n in project.entities for c in lineage.entity_columns(project, n)
             if any((s["entity"], s["column"]) == (name, column) for s in lineage.upstream(project, n, c))}
    out = {n for n, _ in built if (n, _) != (name, column)}
    for n, e in project.entities.items():
        out |= {n for lk in e.spec.get("lookup", []) if (lk["dim"], lk["on"]) in built}
        out |= {n for c in e.spec.get("check", []) if "exists_in" in c
                and tuple(c["exists_in"].rsplit(".", 1)) in built}
    return out


def tables(res, severity=None):
    return {i["name"] for i in res["items"] if i["kind"] == "table" and severity in (None, i["severity"])}


SEEDS = range(24)


@pytest.mark.parametrize("seed", SEEDS)
def test_column_impact_agrees_with_a_brute_force_reference(tmp_path, seed):
    project = random_project(tmp_path / "p", seed)
    base = _errors(project)
    unknown = {n for n in project.entities for rec in lineage.entity_columns(project, n).values()
               if rec["label"] == "unknown"}
    # lineage shows no inputs for an expression it can't read, so what's built from one is out of its reach
    past_unknown = unknown | {d for n in unknown for d in entity_impact(project, n)}
    checked = 0
    for name, e in project.entities.items():
        for col in [c for c in e.columns if c not in e.mode_columns()]:
            # drop: what breaks first is what the save gate or lineage says breaks once the column is gone
            first, every = brute_force(project, base, name, lambda s, c=col: _without(s, c))
            res = impact(project, name, col, "drop")
            assert tables(res, "breaks") == first, (seed, name, col)
            # everything that breaks on the way is listed; anything else listed is this table (whose own
            # expressions still read the column from its source) or reads with an expression that can't be read
            assert every <= tables(res), (seed, name, col, every - tables(res))
            extra = tables(res) - every
            assert extra <= unknown | {name}, (seed, name, col, extra)
            assert all(item(res, n)["severity"] == "check" for n in extra)
            assert all(item(res, n)["severity"] == "check" for n in every - first)

            # rename: the same tables break first, apart from this one (its own file is fixed with it)
            new = f"{col}_new"

            def rename(s, c=col, n=new):
                s["contract"]["columns"] = {(n if k == c else k): v for k, v in s["contract"]["columns"].items()}
                for d in s.get("derive", []):
                    d["column"] = n if d["column"] == c else d["column"]

            first_r, _ = brute_force(project, base, name, rename)
            res_r = impact(project, name, col, "rename", new_name=new)
            assert tables(res_r, "breaks") - {name} == first_r - {name}, (seed, name, col)

            # retype: never a break here, and everything that reads the column by name is asked to check
            other = next(t for t in TYPES if t != e.columns[col])
            res_t = impact(project, name, col, "retype", new_type=other)
            assert res_t["counts"]["breaks"] == 0
            assert first - {name} <= tables(res_t), (seed, name, col)
            assert tables(res_t) <= reach(project, name, col) | past_unknown | {name}, (seed, name, col)

            # edit_impact gives the same answers for the same edits of the contract
            for edit, want in ((lambda s, c=col: _without(s, c), res), (rename, res_r),
                               (lambda s, c=col, t=other: s["contract"]["columns"].__setitem__(c, t), res_t)):
                spec = copy.deepcopy(e.spec)
                edit(spec)
                got = change_impact.edit_impact(project, name, spec)
                assert got == ([want] if want["items"] else []), (seed, name, col)
            checked += 1
    assert checked > 10


@pytest.mark.parametrize("seed", range(6))
def test_edit_impact_of_several_changes_is_each_changes_answer(tmp_path, seed):
    project = random_project(tmp_path / "p", seed)
    rng = random.Random(seed)
    for name, e in project.entities.items():
        cols = [c for c in e.spec["contract"]["columns"] if c not in e.mode_columns()]
        if len(cols) < 3:
            continue
        drop, retype = rng.sample(cols, 2)
        spec = copy.deepcopy(e.spec)
        spec["contract"]["columns"].pop(drop)
        new_type = next(t for t in TYPES if t != e.columns[retype])
        spec["contract"]["columns"][retype] = new_type
        want = [r for r in (impact(project, name, drop, "drop"), impact(project, name, retype, "retype", new_type=new_type))
                if r["items"]]
        assert change_impact.edit_impact(project, name, spec) == want


@pytest.mark.parametrize("seed", range(4))
def test_random_projects_answer_the_same_whatever_order_their_entities_are_in(tmp_path, seed):
    project = random_project(tmp_path / "p", seed)
    asks = [(n, c, ch) for n, e in project.entities.items() for c in e.columns for ch in change_impact.CHANGES]
    want = [impact(project, n, c, ch, new_name="zz", new_type="string") for n, c, ch in asks]
    order = list(project.entities)
    random.Random(seed).shuffle(order)
    project.entities = {n: project.entities[n] for n in reversed(order)}
    assert [impact(project, n, c, ch, new_name="zz", new_type="string") for n, c, ch in asks] == want
