"""LX-05: the Learn page's engine methods. They read the bundled content and work in a lab under the
data folder; nothing reaches Fabric."""

from __future__ import annotations

import pytest

from fabric_desktop import audit, server
from fabric_desktop.learn_service import METHODS, LearnMixin


class _Svc(LearnMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path):
    return _Svc(tmp_path)


def test_methods_are_served_and_the_reads_are_not_audited():
    assert METHODS <= server.METHODS
    for m in ("learn_catalog", "learn_module", "learn_lab_run", "learn_lab_check", "learn_lab_hint"):
        assert not audit.audited(m)
    for m in ("learn_lab_start", "learn_lab_save", "learn_lab_reset"):
        assert audit.audited(m)


def test_catalog_lists_paths_and_modules_with_lab_state(svc):
    cat = svc.learn_catalog()
    by_id = {m["id"]: m for m in cat["modules"]}
    assert {"LM-01", "LAB-02"} <= set(by_id)
    assert by_id["LM-01"]["started"] is False
    assert any(p["id"] == "data-fundamentals" for p in cat["paths"])
    svc.learn_lab_start("LM-01")
    assert {m["id"]: m for m in svc.learn_catalog()["modules"]}["LM-01"]["started"] is True


def test_module_gives_the_lesson_but_never_the_solution(svc):
    m = svc.learn_module("LM-01")
    assert m["lesson"].startswith("# ")
    assert m["lab"]["started"] is False
    ex = m["exercises"][0]
    assert set(ex) == {"id", "prompt", "engine", "hints", "checks"}
    assert "solution" not in str(m["exercises"])
    with pytest.raises(ValueError):
        svc.learn_module("../LM-01")


def test_run_shows_rows_and_undoes_changes(svc):
    svc.learn_lab_start("LM-01")
    out = svc.learn_lab_run("LM-01", "SELECT order_id, amount FROM orders ORDER BY order_id LIMIT 2")
    assert out["columns"] == ["order_id", "amount"] and len(out["rows"]) == 2 and out["more"] is False
    assert all(isinstance(v, (int, float)) for row in out["rows"] for v in row)
    before = svc.learn_lab_run("LM-01", "SELECT count(*) AS n FROM orders")["rows"]
    svc.learn_lab_run("LM-01", "DELETE FROM orders")
    assert svc.learn_lab_run("LM-01", "SELECT count(*) AS n FROM orders")["rows"] == before
    with pytest.raises(ValueError):
        svc.learn_lab_run("LM-01", "BEGIN; DELETE FROM orders; COMMIT;")


def test_run_has_no_file_or_network_access(svc, tmp_path):
    svc.learn_lab_start("LM-01")
    (tmp_path / "secret.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(Exception):
        svc.learn_lab_run("LM-01", f"SELECT * FROM read_csv('{(tmp_path / 'secret.csv').as_posix()}')")
    with pytest.raises(Exception):
        svc.learn_lab_run("LM-01", "SET enable_external_access = true")


def test_run_needs_a_started_lab_and_caps_rows(svc):
    with pytest.raises(ValueError, match="Start LM-01 first"):
        svc.learn_lab_run("LM-01", "SELECT 1")
    svc.learn_lab_start("LM-01")
    out = svc.learn_lab_run("LM-01", "SELECT * FROM range(500)")
    assert len(out["rows"]) == 200 and out["more"] is True


def test_save_check_hint_and_reset(svc):
    svc.learn_lab_start("LM-01")
    ex = svc.learn_module("LM-01")["exercises"][0]["id"]
    svc.learn_lab_save("LM-01", ex, "SELECT 1 AS x")
    assert svc.learn_module("LM-01")["lab"]["answers"][ex] == "SELECT 1 AS x"
    assert svc.learn_lab_check("LM-01", ex)["passed"] is False
    good = "SELECT order_id, customer, amount FROM orders WHERE region = 'North' AND amount > 100 ORDER BY amount DESC"
    assert svc.learn_lab_check("LM-01", ex, good)["passed"] is True
    assert svc.learn_lab_hint("LM-01", ex, 1)["of"] == 0  # LM-01 has no hints
    svc.learn_lab_reset("LM-01")
    assert svc.learn_module("LM-01")["lab"]["answers"][ex] != "SELECT 1 AS x"


def test_a_runaway_query_is_stopped_and_memory_is_capped(svc, monkeypatch):
    from fabric_desktop.learn import labs
    monkeypatch.setattr(labs, "TIME_LIMIT", 1)
    svc.learn_lab_start("LM-01")
    huge = "SELECT count(*) FROM range(100000000) a, range(100000000) b"
    with pytest.raises(ValueError, match="Stopped after 1 seconds"):
        svc.learn_lab_run("LM-01", huge)
    ex = svc.learn_module("LM-01")["exercises"][0]["id"]
    out = svc.learn_lab_check("LM-01", ex, huge)
    assert out["passed"] is False and "Stopped after 1 seconds" in out["results"][0]["detail"]
    assert svc.learn_lab_run("LM-01", "SELECT count(*) AS n FROM orders")["rows"] == [[7]]  # the lab still works
    assert "MiB" in svc.learn_lab_run("LM-01", "SELECT current_setting('memory_limit') AS m")["rows"][0][0]
    with pytest.raises(Exception):
        svc.learn_lab_run("LM-01", "SET memory_limit = '64GB'")
