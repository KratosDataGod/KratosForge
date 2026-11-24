"""Shared test setup."""

import errno
import faulthandler
import ipaddress
import json
import os
import socket
import sys
import threading
import time

import msal
import pytest


@pytest.fixture(autouse=True)
def no_real_event_log(monkeypatch):
    """Audit writes also record the trail's end in the Windows event log (audit_anchor.py). Tests don't
    add records to this PC's real log, and don't read ones other tests left; test_audit_anchor.py uses
    a fake log, and one marked Windows-only test the real one."""
    from fabric_desktop import audit_anchor
    monkeypatch.setattr(audit_anchor, "writer", audit_anchor._no_writer)
    monkeypatch.setattr(audit_anchor, "reader", audit_anchor._no_reader)
    monkeypatch.setattr(audit_anchor, "_off_until", 0.0)  # a failure another test caused doesn't carry over


class FakeEventLog:
    """An event log in memory: ``audit_anchor``'s writer and reader."""

    def __init__(self):
        self.records: list[str] = []
        self.fail = None  # an exception to raise on write and read

    def write(self, value):
        if self.fail:
            raise self.fail
        self.records.append(value)

    def read(self):
        if self.fail:
            raise self.fail
        return list(reversed(self.records))


@pytest.fixture
def event_log(monkeypatch):
    from fabric_desktop import audit_anchor
    log = FakeEventLog()
    monkeypatch.setattr(audit_anchor, "writer", log.write)
    monkeypatch.setattr(audit_anchor, "reader", log.read)
    monkeypatch.setattr(audit_anchor, "_off_until", 0.0)
    monkeypatch.setattr(audit_anchor, "_cache", {})
    return log


@pytest.fixture(autouse=True)
def duckdb_default_connection_left_clean():
    """After each test, DuckDB's default connection (duckdb.sql, spark without a session) holds no result
    and no failed transaction. A result a test left unread is released by the next query; if a Cancel
    test's interrupt() aborted it meanwhile, later tests' queries failed with "Current transaction is
    aborted". A connection that can't run SELECT 1 is replaced. It uses the real duckdb module, imported
    here before the test, since a test may put a fake or None in sys.modules["duckdb"]."""
    import duckdb

    yield
    try:
        duckdb.default_connection().execute("SELECT 1").fetchall()
    except duckdb.Error:
        duckdb.set_default_connection(duckdb.connect())


@pytest.fixture(autouse=True)
def no_interactive_sign_in(monkeypatch):
    """Sign-in needs no setup now, so a test that reaches it would open a browser and wait.
    Make it fail at once instead, as it does for someone who closes the sign-in window."""
    def refuse(self, scopes, **kwargs):
        return {"error": "access_denied", "error_description": "no interactive sign-in in tests"}
    monkeypatch.setattr(msal.PublicClientApplication, "acquire_token_interactive", refuse)


@pytest.fixture(autouse=True)
def no_remembered_secrets():
    """dbutils.secrets keeps the values it read for the process, so one test's secret would be
    redacted in the next test's output."""
    from fabric_desktop import secret_redaction
    secret_redaction.forget_all()
    yield
    secret_redaction.forget_all()


class _NoSignInService:
    """MSAL's HTTP client for tests. Building a PublicClientApplication asks the sign-in service for its
    settings (the authority's OpenID configuration), so every test that made an Authenticator needed the
    internet. This answers that question, and the one about which host names are the same cloud, the way
    login.microsoftonline.com does, and refuses anything else, so no test can reach Microsoft."""

    def get(self, url, params=None, headers=None, **kwargs):
        if url.endswith("/v2.0/.well-known/openid-configuration"):
            base = url[: -len("/v2.0/.well-known/openid-configuration")]
            body = {"issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
                    "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
                    "token_endpoint": f"{base}/oauth2/v2.0/token",
                    "device_authorization_endpoint": f"{base}/oauth2/v2.0/devicecode"}
            return _Reply(200, json.dumps(body))
        if url.endswith("/common/discovery/instance"):  # which host names are one cloud (for cached accounts)
            hosts = ["login.microsoftonline.com", "login.windows.net", "login.microsoft.com", "sts.windows.net"]
            return _Reply(200, json.dumps({"metadata": [{"preferred_network": hosts[0], "preferred_cache": hosts[1],
                                                         "aliases": hosts}]}))
        raise AssertionError(f"a test reached the sign-in service: GET {url}")

    def post(self, url, params=None, data=None, headers=None, **kwargs):
        raise AssertionError(f"a test reached the sign-in service: POST {url}")

    def close(self):
        pass


class _Reply:
    def __init__(self, status_code, text):
        self.status_code, self.text, self.headers = status_code, text, {}

    def raise_for_status(self):
        pass


# No test reaches the network. A connect to anything but this PC (loopback, or a local socket file) is refused
# and remembered, and the test that made it fails, naming the address. Refused rather than only recorded, so code
# that retries or swallows errors can't make the call anyway; remembered, so a test whose code catches the error
# still fails. Child processes a test starts aren't covered.
_outbound: list[str] = []
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(sock: socket.socket, address) -> bool:
    if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
        return True  # a local socket file (AF_UNIX), or a family that never leaves this PC
    host = str(address[0]).split("%")[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a host name other than localhost


def _refuse_outbound(address) -> OSError:
    _outbound.append(repr(address))
    return ConnectionRefusedError(f"tests don't reach the network: refused a connect to {address!r}")


def _guarded_connect(self, address):
    if not _is_local(self, address):
        raise _refuse_outbound(address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(self, address):
        _refuse_outbound(address)
        return errno.ECONNREFUSED
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def no_network():
    _outbound.clear()
    yield
    if _outbound:
        tried = sorted(set(_outbound))
        _outbound.clear()
        pytest.fail(f"this test tried to reach the network: {', '.join(tried)}", pytrace=False)


@pytest.fixture(autouse=True)
def no_sign_in_service(monkeypatch):
    # The desktop's sign-in, and a service principal's (credentials.py) for agents.
    for name in ("PublicClientApplication", "ConfidentialClientApplication"):
        real = getattr(msal, name)

        class Offline(real):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("http_client", _NoSignInService())
                super().__init__(*args, **kwargs)
        monkeypatch.setattr(msal, name, Offline)


@pytest.fixture(autouse=True)
def cells_in_the_engine_unless_a_test_asks(request, monkeypatch):
    """Notebooks run in a process of their own by default (PB-136), but most tests fake OneLake,
    Fabric or a token by patching this process, and a cell in a process of its own doesn't see
    those patches (it would reach the real service). So tests run cells inside the engine unless
    they turn kernel_process on themselves, or are marked ``real_kernel_default``
    (test_kernel_default.py checks the default itself)."""
    if request.node.get_closest_marker("real_kernel_default") is None:
        from fabric_desktop import app_settings
        monkeypatch.setitem(app_settings.DEFAULTS, "kernel_process", False)


@pytest.fixture(autouse=True)
def saved_policy_in_tmp(monkeypatch, tmp_path):
    """Reading a policy file keeps a copy of it (app_settings.saved_policy_path); keep it out of the
    real profile, so one test's copy can't stand in for another test's broken file."""
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "saved_policy_path", lambda: tmp_path / "saved-policy" / "policy-last-good.json")
    # Test policy files are the test account's own; the check for that has tests of its own.
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: False)
    monkeypatch.setattr(app_settings, "policy_file_untrusted", lambda: None)
    monkeypatch.setattr(app_settings, "registry_key_untrusted", lambda lists: None)


@pytest.fixture(autouse=True)
def no_lakehouse_left_attached(monkeypatch):
    """The attached lakehouse is the engine's one module global (lakehouse._attached). A test that
    attaches one would otherwise leave it for every later test, so a notebook test passed or failed
    depending on which file ran before it."""
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)
    # Likewise the maintenance guard a Service sets: without the app, OPTIMIZE, VACUUM and RESTORE fail closed.
    monkeypatch.setattr(lakehouse, "maintenance_guard", None)
    # And the Prod typed-name guard a Service sets (prod_writes_service.py): without the app, nothing is marked Prod.
    monkeypatch.setattr(lakehouse, "prod_guard", None)


@pytest.fixture(autouse=True)
def no_databricks_tokens_refused_by_earlier_tests(monkeypatch):
    """Tokens a workspace refused are remembered for the process (databricks_auth._REFUSED); each test
    starts with none, so a stand-in token one test refused can't be withheld in the next."""
    from fabric_desktop.engines import databricks_auth
    monkeypatch.setattr(databricks_auth, "_REFUSED", {})


@pytest.fixture(autouse=True)
def runtime_grants_on_record_once_per_worker(request, monkeypatch, tmp_path_factory):
    """Windows only. The exercise runtime's grants are persistent ACEs on this machine's Python
    install, and grant_runtime skips re-granting when the data folder's grants record already
    lists them. Each test has its own tmp data folder, so without this every box start re-granted
    about 116 paths with icacls (14.5 s a time on the CI runner, 70% of the Academy files' time).
    Here each test's data folder starts with this worker's record. Within a worker the first
    grant is still a real one, and a later one trusts the record only after grant_runtime has
    checked the interpreter's own ACE (or granted in this process). Paths the record doesn't list
    yet are granted as before. Tests marked machine_wide_grants (they remove
    the grants, run on their own, and check a cold grant and its record) keep their own records."""
    if sys.platform != "win32" or request.node.get_closest_marker("machine_wide_grants"):
        return
    import shutil

    from fabric_desktop.academy import appcontainer
    shared = tmp_path_factory.getbasetemp() / "shared-runtime-grants.json"
    real = appcontainer.grant_runtime

    def grant_runtime(api, cap_string, lib_roots=None, data_root=None):
        record = appcontainer._grants_file(data_root) if data_root else None
        if record is not None and shared.exists() and not record.exists():
            record.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(shared, record)
        real(api, cap_string, lib_roots, data_root)
        if record is not None and record.exists():
            shutil.copyfile(record, shared)
    monkeypatch.setattr(appcontainer, "grant_runtime", grant_runtime)


# A test that hangs would otherwise hold CI until the job's 6-hour limit, with no output (logs only
# come back once the job ends). After TEST_TIME_LIMIT seconds, print which test it was and every
# thread's stack, then end pytest so the job fails with that in its log.
# Under pytest-xdist (CI) os._exit ends only that worker: xdist reports the test as crashed, starts
# another worker and the run still fails, with the stacks in the log.
TEST_TIME_LIMIT = float(os.environ.get("FD_TEST_TIME_LIMIT", "300"))


def _over_time(item, capman) -> None:
    if capman is not None:
        capman.suspend_global_capture(in_=True)
    sys.stderr.write(f"\n\n{item.nodeid} ran over {TEST_TIME_LIMIT:g} s; stacks of every thread:\n")
    sys.stderr.flush()
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    os._exit(1)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    capman = item.config.pluginmanager.getplugin("capturemanager")
    timer = threading.Timer(TEST_TIME_LIMIT, _over_time, args=(item, capman))
    timer.daemon = True
    timer.start()
    # Backstop in C for a hang that holds the GIL, where the timer thread can't run.
    faulthandler.dump_traceback_later(TEST_TIME_LIMIT + 60, exit=True, file=sys.__stderr__)
    try:
        yield
    finally:
        timer.cancel()
        faulthandler.cancel_dump_traceback_later()


def pytest_terminal_summary(terminalreporter, config):
    """With --durations, also list the test files that take longest in all (setup, call and teardown
    of every test in them), so a slow fixture shows even when no single test is in the slowest list.
    Under pytest-xdist the times are each worker's, added up."""
    if not config.getoption("durations", None):
        return
    files: dict[str, list[float]] = {}
    for reports in terminalreporter.stats.values():
        for report in reports:
            when, nodeid = getattr(report, "when", None), getattr(report, "nodeid", "")
            if when not in ("setup", "call", "teardown") or not nodeid:
                continue
            spent = files.setdefault(nodeid.split("::")[0], [0.0, 0.0, 0])
            spent[0 if when == "setup" else 1] += float(getattr(report, "duration", 0.0) or 0.0)
            spent[2] += when == "call"
    if not files:
        return
    total = sum(s[0] + s[1] for s in files.values())
    terminalreporter.write_sep("=", f"slowest {min(25, len(files))} test files of {len(files)} ({total:.0f}s of test time in all)")
    for path, (setup, rest, count) in sorted(files.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:25]:
        terminalreporter.write_line(f"{setup + rest:8.1f}s  setup {setup:7.1f}s  {count:4d} tests  {path}")


# A test id carries each parametrized value as text. A long value (a 1,025-column CREATE TABLE, a 5,000-digit
# number) made ids of many KB, and a 20 MB one wedged a Windows runner while xdist and the JUnit report passed
# it around. A text or bytes value over PARAM_ID_LIMIT characters is named by its start, its length and a hash
# of the whole, so ids stay short and still unique; and collection stops on any id over TEST_ID_LIMIT.
PARAM_ID_LIMIT = 60
TEST_ID_LIMIT = 200


def pytest_make_parametrize_id(config, val, argname):
    if not isinstance(val, (str, bytes)) or len(val) <= PARAM_ID_LIMIT:
        return None  # pytest's own id
    import hashlib
    import re
    raw = val.encode("utf-8", "surrogatepass") if isinstance(val, str) else val
    start = re.sub(r"[^A-Za-z0-9_.,:=+-]", "_", (val if isinstance(val, str) else val.decode("latin-1"))[:24])
    return f"{start}...{len(val)}chars-{hashlib.sha256(raw).hexdigest()[:10]}"


def pytest_collection_modifyitems(session, config, items):
    long = [item.nodeid for item in items
            if hasattr(item, "callspec") and len(item.callspec.id) > TEST_ID_LIMIT]
    if long:
        shown = "\n".join(f"  {nodeid[:300]}" for nodeid in long[:20])
        raise pytest.UsageError(f"{len(long)} test id(s) are over {TEST_ID_LIMIT} characters; give the "
                                f"parametrize call short ids= (tests/conftest.py TEST_ID_LIMIT):\n{shown}")


@pytest.fixture
def sleep_jump(monkeypatch):
    """time.monotonic, which a call moves forward by ``seconds``, as waking from sleep does. Timed
    waits (Event.wait, so threading.Timer too) are counted on that clock, as on Windows, where a wait
    whose time passed during sleep ends on waking; elsewhere the OS's own monotonic clock stops
    during sleep, so a plain jump wouldn't show the bug."""
    real = time.monotonic
    offset = [0.0]
    active = [True]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])
    from fabric_desktop import awake_timer
    monkeypatch.setattr(awake_timer, "_monotonic", lambda: real() + offset[0])  # its own binding
    monkeypatch.setattr(awake_timer, "_UNBIASED", None)  # on Windows, so the jumped clock is the one read
    event_wait = threading.Event.wait

    def windows_wait(self, timeout=None):
        if timeout is None:
            return event_wait(self)
        deadline = time.monotonic() + timeout
        while True:
            if not active[0]:  # the test is over and the clock back: end early, as a wait may
                return event_wait(self, 0.05)
            left = deadline - time.monotonic()
            if left <= 0:
                return self.is_set()
            if event_wait(self, min(left, 0.05)):
                return True
    monkeypatch.setattr(threading.Event, "wait", windows_wait)

    def jump(seconds: float) -> None:
        offset[0] += seconds
    yield jump
    active[0] = False
