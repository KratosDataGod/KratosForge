"""Test ids stay short (tests/conftest.py): a long parametrized value is named by its start, length and hash,
and collection stops on an id that is still too long."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import conftest


def test_a_short_value_keeps_pytests_own_id():
    assert conftest.pytest_make_parametrize_id(None, "x" * conftest.PARAM_ID_LIMIT, "sql") is None
    assert conftest.pytest_make_parametrize_id(None, 12345, "n") is None


def test_a_long_value_is_named_short_and_two_alike_stay_apart():
    a = "SELECT 1 AS c0, " + ", ".join(f"{i} AS c{i}" for i in range(1, 1025))
    b = a.replace("c1024", "c1025")
    ida = conftest.pytest_make_parametrize_id(None, a, "sql")
    idb = conftest.pytest_make_parametrize_id(None, b, "sql")
    assert len(ida) <= conftest.PARAM_ID_LIMIT and ida != idb and f"...{len(a)}chars-" in ida
    # the same on every xdist worker, or the workers' collections wouldn't match
    assert conftest.pytest_make_parametrize_id(None, a, "sql") == ida
    raw = conftest.pytest_make_parametrize_id(None, b"\x00\xff" * 2500, "raw")
    assert len(raw) <= conftest.PARAM_ID_LIMIT and raw.isascii() and "...5000chars-" in raw


def test_collection_stops_on_an_id_that_is_still_too_long():
    item = SimpleNamespace(nodeid="tests/test_x.py::test_y[" + "z" * 300 + "]",
                           callspec=SimpleNamespace(id="z" * 300))
    with pytest.raises(pytest.UsageError, match="1 test id"):
        conftest.pytest_collection_modifyitems(None, None, [item, SimpleNamespace(nodeid="ok")])
    conftest.pytest_collection_modifyitems(None, None, [SimpleNamespace(nodeid="ok", callspec=SimpleNamespace(id="a"))])
