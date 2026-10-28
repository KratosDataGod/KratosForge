"""Warehouse connections retry a warehouse that is waking up or busy, before anything is sent (#946)."""

import pytest

from fabric_desktop import warehouse


class DriverError(Exception):
    pass


def connector(failures):
    """A connect that raises each of ``failures`` in turn, then connects; records the tokens it got."""
    seen = []

    def connect(server, database, token, login_timeout=60):
        seen.append(token)
        if failures:
            raise failures.pop(0)
        return "connection"
    return connect, seen


def tokens():
    n = iter(range(100))
    return lambda: f"tok{next(n)}"


def test_a_warehouse_that_is_resuming_is_tried_again_with_a_fresh_token(monkeypatch):
    connect, seen = connector([DriverError("[Microsoft][ODBC Driver 18] Database 'WH' on server is not currently available. (40613)"),
                               DriverError("Login timeout expired (HYT00)")])
    monkeypatch.setattr(warehouse, "connect", connect)
    waits = []
    assert warehouse.open_connection("s.datawarehouse.fabric.microsoft.com", "WH", tokens(), sleep=waits.append) == "connection"
    assert seen == ["tok0", "tok1", "tok2"] and waits == [5, 10]


def test_every_try_failing_gives_a_plain_message_with_the_last_error(monkeypatch):
    connect, seen = connector([DriverError("The service is currently busy. (40501)") for _ in range(4)])
    monkeypatch.setattr(warehouse, "connect", connect)
    waits = []
    with pytest.raises(ConnectionError) as exc:
        warehouse.open_connection("s", "WH", tokens(), sleep=waits.append)
    assert len(seen) == warehouse.CONNECT_TRIES and sum(waits) < 60
    assert "didn't accept a connection after 4 tries" in str(exc.value) and "Last error 40501." in str(exc.value)


@pytest.mark.parametrize("message", ["Login failed for user '<token-identified principal>'. (18456)",
                                     "Cannot open server requested by the login. The login failed. (40532)",
                                     "Invalid object name 'dbo.nope'. (208)"])
def test_a_refused_login_or_any_other_error_is_not_retried(monkeypatch, message):
    connect, seen = connector([DriverError(message)])
    monkeypatch.setattr(warehouse, "connect", connect)
    with pytest.raises(DriverError):
        warehouse.open_connection("s", "WH", tokens(), sleep=lambda s: pytest.fail("waited"))
    assert len(seen) == 1


def test_nothing_is_retried_once_a_statement_has_been_sent(monkeypatch, tmp_path):
    connects = []

    class Cursor:
        description = None
        rowcount = 1

        def execute(self, q):
            raise DriverError("The service is currently busy. (40501)")

        def nextset(self):
            return False

    class Connection:
        timeout = 0

        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    def connect(*a, **k):
        connects.append(1)
        return Connection()
    monkeypatch.setattr(warehouse, "connect", connect)
    job = {"query": "UPDATE dbo.t SET a = 1", "writes": True, "out": str(tmp_path / "out.arrow"),
           "warehouse": {"server": "s", "database": "WH"}}
    with pytest.raises(DriverError):
        warehouse.run(job, lambda: "tok", lambda *a, **k: None)
    assert connects == [1], "a statement that failed is reported, not sent again"
