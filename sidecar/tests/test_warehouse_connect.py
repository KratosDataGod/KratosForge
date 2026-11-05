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
    assert len(seen) == warehouse.CONNECT_TRIES and waits == [5, 10, 20]
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


def driver_error(text):
    """A connect failure as mssql-python raises it: no error number in the message."""
    from mssql_python.exceptions import OperationalError
    return OperationalError(driver_error="Communication link failure", ddbc_error=f"[Microsoft][SQL Server]{text}")


@pytest.mark.parametrize("text, number", [
    ("Database 'WH' on server 'x' is not currently available.  Please retry the connection later.", 40613),
    ("The service is currently busy. Retry the request after 10 seconds.", 40501),
    ("Cannot process request. Not enough resources to process request.", 49918),
    ("Cannot process request. Too many create or update operations in progress for subscription \"x\".", 49919),
    ("Cannot process request. Too many operations in progress for subscription \"x\".", 49920),
    ("The service has encountered an error processing your request. Please try again.", 40197),
    ("Resource ID : 1. The request limit for the database is 200 and has been reached.", 10928),
    ("Resource ID : 1. The request minimum guarantee is 10, maximum limit is 200.", 10929),
    ("Login to read-secondary failed due to long wait on 'HADR_DATABASE_WAIT_FOR_TRANSITION_TO_VERSIONING'.", 4221),
])
def test_mssql_python_errors_are_read_by_their_text(text, number):
    exc = driver_error(text)
    assert "(" not in str(exc).split("DDBC Error:")[1][:20] and warehouse.transient_login_error(exc) == number


@pytest.mark.parametrize("text", ["Login failed for user '<token-identified principal>'.",
                                  "Cannot open server 'x' requested by the login. The login failed."])
def test_a_refused_mssql_python_login_isnt_retried(text):
    assert warehouse.transient_login_error(driver_error(text)) is None


def test_every_try_and_wait_fits_the_deadline_even_when_each_login_times_out(monkeypatch):
    now = [0.0]
    timeouts = []

    def connect(server, database, token, login_timeout=60):
        timeouts.append(login_timeout)
        now[0] += login_timeout  # each login runs to its timeout
        raise driver_error("Login timeout expired")
    monkeypatch.setattr(warehouse, "connect", connect)
    with pytest.raises(ConnectionError) as exc:
        warehouse.open_connection("s", "WH", tokens(), sleep=lambda s: now.__setitem__(0, now[0] + s),
                                  clock=lambda: now[0])
    assert now[0] <= warehouse.CONNECT_DEADLINE_S and timeouts[0] == warehouse.LOGIN_TIMEOUT_S
    assert "The login timed out." in str(exc.value) and isinstance(exc.value.__cause__, Exception)
