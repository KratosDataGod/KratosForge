"""Entity and notebook changes by meaning: against Git, against what an environment runs, cell by cell."""

import json
import subprocess

import pytest

from fabric_desktop.meta import diff
from fabric_desktop.service import Service

ORDERS = '''name = "silver.orders"
owner = "me"

[source]
entity = "bronze.orders"

[load]
mode = "incremental"
watermark = "modified_at"

[contract]
keys = ["order_id"]

[contract.columns]
order_id = "int64"
amount = "float64"
status = "string"

[[derive]]
column = "net"
expr = "amount"

[[check]]
column = "order_id"
not_null = true
on_fail = "stop"

[[check]]
name = "returns_are_negative"
expr = "status <> 'returned' or amount < 0"
on_fail = "quarantine"
'''


def texts(changes):
    return [c["text"] for c in changes]


def test_comments_order_and_spacing_are_not_changes():
    edited = ("# Orders, cleaned\n" + ORDERS).replace('owner = "me"\n', "").replace('name = "silver.orders"\n',
                                                                                     'name = "silver.orders"\nowner   =   "me"  # who\n')
    assert diff.entity_changes(diff.parse(ORDERS), diff.parse(edited)) == []


def test_contract_columns_keys_and_settings_read_in_their_own_terms():
    edited = (ORDERS.replace('amount = "float64"', 'amount = "decimal(18,2)"')
              .replace('status = "string"\n', 'channel = "string"\n')
              .replace('keys = ["order_id"]', 'keys = ["order_id", "channel"]')
              .replace('watermark = "modified_at"', 'watermark = "updated_at"')
              .replace('entity = "bronze.orders"', 'entity = "bronze.orders"\nnew_files_only = false')
              .replace('owner = "me"', 'owner = "sales-data"'))
    changes = diff.entity_changes(diff.parse(ORDERS), diff.parse(edited))
    assert texts(changes) == [  # what changes the table first, columns in contract order
        "column amount changed type from float64 to decimal(18,2)",
        "column status removed (was string)",
        "column channel added (string)",
        "contract keys changed from [order_id] to [order_id, channel]",
        "load.watermark changed from modified_at to updated_at",
        "owner changed from me to sales-data",
        "source.new_files_only set to false",
    ]
    by = {c["text"]: c for c in changes}
    assert by["column amount changed type from float64 to decimal(18,2)"]["kind"] == "column_retyped"
    assert by["column channel added (string)"]["logic"] and by["column status removed (was string)"]["logic"]
    assert by["contract keys changed from [order_id] to [order_id, channel]"]["logic"]
    assert by["load.watermark changed from modified_at to updated_at"]["logic"]
    assert not by["source.new_files_only set to false"]["logic"]  # how it reads, not what it writes
    assert not by["owner changed from me to sales-data"]["logic"]


def test_list_items_are_matched_so_an_edit_reads_as_changed():
    edited = (ORDERS.replace('on_fail = "quarantine"', 'on_fail = "stop"')
              .replace('expr = "amount"', 'expr = "amount * 1.2"')
              .replace('[[check]]\ncolumn = "order_id"\nnot_null = true\non_fail = "stop"\n\n', ""))
    edited += '\n[[check]]\ncolumn = "status"\nnot_null = true\n'
    changes = texts(diff.entity_changes(diff.parse(ORDERS), diff.parse(edited)))
    assert "derived column net changed expr from amount to amount * 1.2" in changes
    assert "check returns_are_negative changed on_fail from quarantine to stop" in changes
    assert "check order_id removed" in changes and "check status added" in changes
    assert len(changes) == 4


def test_reordering_list_items_is_reported_once():
    a = '[[derive]]\ncolumn = "a"\nexpr = "1"\n\n[[derive]]\ncolumn = "b"\nexpr = "2"\n'
    b = '[[derive]]\ncolumn = "b"\nexpr = "2"\n\n[[derive]]\ncolumn = "a"\nexpr = "1"\n'
    assert texts(diff.entity_changes(diff.parse(a), diff.parse(b))) == ["derived columns reordered"]


def test_project_changes_cover_new_removed_and_unreadable_files():
    out = diff.project_changes({"a": ORDERS, "b": ORDERS, "c": ORDERS},
                               {"a": ORDERS, "c": "not = [valid", "d": ORDERS})
    assert [(d["entity"], d["state"]) for d in out] == [("b", "removed"), ("c", "unreadable"), ("d", "new")]
    assert "isn't valid TOML" in out[1]["problem"]


# ---- through the service


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.meta_set_environment("test", target="local")
    return s


def edit(svc, name, old, new):
    text = svc.meta_entity(name)["text"]
    assert old in text
    svc.meta_save_entity(name, text.replace(old, new, 1))


def test_against_the_last_commit(svc, tmp_path):
    root = tmp_path / "crm"
    assert "isn't in Git" in svc.meta_diff("head")["problem"]
    git(root, "init", "-q")
    git(root, "-c", "user.email=t@e.st", "-c", "user.name=T", "add", "-A")
    git(root, "-c", "user.email=t@e.st", "-c", "user.name=T", "commit", "-q", "-m", "start")
    assert svc.meta_diff("head") == {"against": "head", "entities": [], "problem": None}

    edit(svc, "silver.orders", 'net_amount = "float64"', 'net_amount = "float64"\nchannel = "string"')
    (root / "metadata" / "entities" / "gold.fact_sales.toml").unlink()
    svc._project().reload()
    out = {d["entity"]: d for d in svc.meta_diff("head")["entities"]}
    assert texts(out["silver.orders"]["changes"]) == ["column channel added (string)"]
    assert out["gold.fact_sales"]["state"] == "removed"


def test_against_an_environment_and_in_the_promote_impact(svc):
    assert "Nothing has been promoted to Test" in svc.meta_diff("test")["problem"]
    svc.meta_promote("dev", "test")
    assert svc.meta_diff("test")["entities"] == []
    edit(svc, "silver.orders", 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    edit(svc, "silver.orders", 'owner = "me"', 'owner = "sales"')
    out = svc.meta_diff("test")["entities"]
    assert [(d["entity"], d["state"], d["logic"]) for d in out] == [("silver.orders", "changed", True)]
    assert texts(out[0]["changes"]) == ["derived column net_amount changed expr from amount - coalesce(discount, 0) to amount",
                                        "owner changed from me to sales"]
    impact = {t["entity"]: t for t in svc.meta_promote_impact("dev", "test")["tables"]}
    assert texts(impact["silver.orders"]["changes"]) == texts(out[0]["changes"])
    assert impact["silver.customers"]["changes"] == []
    with pytest.raises(ValueError, match="head or one of"):
        svc.meta_diff("elsewhere")


# ---- notebooks


def nb(*cells):
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
            "cells": [{"id": i, "cell_type": t, "source": s, "metadata": {}, "outputs": [{"text": "x"}], "execution_count": 3}
                      for i, t, s in cells]}


def test_notebook_cells_compare_by_id_and_source_only():
    old = nb(("a", "code", "x = 1\ny = 2\n"), ("b", "markdown", "# Title"), ("c", "code", "print(x)"))
    new = nb(("b", "markdown", "# Title"), ("a", "code", "x = 1\ny = 3\n"), ("d", "code", "z = 0"))
    new["cells"][0]["outputs"] = []  # outputs and counts don't matter
    new["cells"][0]["execution_count"] = None
    out = diff.notebook_changes(old, new)
    kinds = {(c["kind"], c["cell"]) for c in out}
    assert kinds == {("changed", "a"), ("added", "d"), ("removed", "c"), ("reordered", None)}
    changed = next(c for c in out if c["cell"] == "a")
    assert "-y = 2" in changed["diff"] and "+y = 3" in changed["diff"] and changed["position"] == 2
    assert diff.notebook_changes(old, json.loads(json.dumps(old))) == []


def test_notebook_diff_against_another_notebook_and_git(svc):
    svc.save_notebook("one", [{"cell_id": "a", "type": "code", "source": "x = 1"}])
    svc.save_notebook("two", [{"cell_id": "a", "type": "code", "source": "x = 2"}])
    out = svc.notebook_diff("two", other="one")
    assert [(c["kind"], c["cell"]) for c in out["cells"]] == [("changed", "a")]
    assert "isn't in a Git commit" in svc.notebook_diff("one")["problem"]
