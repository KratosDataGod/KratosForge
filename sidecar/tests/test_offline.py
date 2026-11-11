"""An offline PC, or one whose network blocks PyPI, GitHub or the update host: every step that goes
online fails within its timeout with a line that says what happened and what to do. Never a hang,
and never "didn't answer in time" or "no such package" when the truth is that nothing was reachable.

Nothing here leaves the machine: names under .invalid never resolve, and a port nobody listens on
refuses the connection.
"""

from __future__ import annotations

import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import urllib3.exceptions

from fabric_desktop import environments, http_session, net_trust
from fabric_desktop.engines import dbconnect as dbc
from fabric_desktop.environments import Environments
from test_corporate_network import NETWORK_ENV
from test_environments_package_source import wait

UPDATER = Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "src"


@pytest.fixture
def no_proxy(monkeypatch):
    for name in NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", dict)  # nor the proxy this machine is set to
    # urlopen keeps the opener it first builds, with the proxies of that moment: one made here
    # must not outlive the test.
    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setattr(requests.sessions, "get_environ_proxies", lambda *a, **k: {})


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- the engine's HTTPS (requests: Fabric, OneLake, Graph, telemetry) -------------------------------

def test_a_name_that_doesnt_resolve_says_the_pc_couldnt_reach_the_server(no_proxy):
    started = time.monotonic()
    with pytest.raises(requests.exceptions.ConnectionError) as caught:
        http_session.new().get("https://kratosforge-offline-test.invalid/v1", timeout=10)
    assert time.monotonic() - started < 10
    assert net_trust.explain(caught.value) == net_trust.OFFLINE_HINT


def test_a_refused_connection_says_the_same_not_that_it_timed_out(no_proxy):
    with pytest.raises(requests.exceptions.ConnectionError) as caught:
        http_session.new().get(f"https://127.0.0.1:{closed_port()}/v1", timeout=10)
    assert net_trust.explain(caught.value) == net_trust.OFFLINE_HINT


def test_a_real_connect_timeout_still_says_it_timed_out():
    pool = urllib3.HTTPSConnectionPool("example.invalid")
    cause = urllib3.exceptions.ConnectTimeoutError(pool, "Connection timed out (connect timeout=0.5)")
    error = requests.exceptions.ConnectTimeout(urllib3.exceptions.MaxRetryError(pool, "/", cause))
    assert net_trust.explain(error) == net_trust.TIMEOUT_HINT


def test_a_failure_that_isnt_the_network_gets_no_network_line():
    assert net_trust.explain(ConnectionRefusedError("the kernel's port")) is None  # no HTTP library involved
    assert net_trust.explain(ValueError("bad table name")) is None


# --- the update check (urllib, before the engine is installed) ---------------------------------------

@pytest.fixture
def updater(monkeypatch):
    monkeypatch.syspath_prepend(str(UPDATER))
    import updater as module
    return module


def test_the_update_check_offline_says_it_couldnt_reach_github(updater, no_proxy, monkeypatch):
    monkeypatch.setattr(updater, "release_url", lambda tag="": "https://kratosforge-offline-test.invalid/releases")
    started = time.monotonic()
    info = updater.latest_release()
    assert time.monotonic() - started < 15  # 5 s per try at most, and a name that doesn't resolve fails at once
    assert info["commit"] is None and info["error"].startswith(updater.OFFLINE_HINT), info


def test_the_update_check_names_each_cause(updater):
    refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
    unreachable = urllib.error.URLError(OSError(10051, "A socket operation was attempted to an unreachable network"))
    no_name = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    for exc in (refused, unreachable, no_name):
        assert updater.explain(exc, "detail").startswith(updater.OFFLINE_HINT), exc
    assert updater.explain(urllib.error.URLError(TimeoutError("timed out")), "d").startswith(updater.TIMEOUT_HINT)
    assert updater.explain(ValueError("x"), "detail") == "detail"


# --- packages (pip), from PyPI or the organization's own index --------------------------------------

PIP_OFFLINE = """\
WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='pypi.org', port=443): Failed to resolve 'pypi.org' ([Errno 11001] getaddrinfo failed)")': /simple/polars/
ERROR: Could not find a version that satisfies the requirement polars (from versions: none)
ERROR: No matching distribution found for polars
"""
PIP_MISSING = """\
ERROR: Could not find a version that satisfies the requirement not-a-real-package (from versions: none)
ERROR: No matching distribution found for not-a-real-package
"""


def test_pip_that_couldnt_reach_the_index_is_told_apart_from_a_missing_package():
    line = net_trust.pip_unreachable(PIP_OFFLINE, "Ask IT.")
    assert line and line.startswith("pip couldn't reach the package index") and line.endswith("Ask IT.")
    assert net_trust.pip_unreachable(PIP_MISSING, "Ask IT.") is None
    proxy = "WARNING: Retrying ... after connection broken by 'ProxyError('Unable to connect to proxy')': /simple/x/"
    assert net_trust.pip_unreachable(proxy, "Ask IT.").startswith("The proxy refused pip's connection")
    cert = "Could not fetch URL https://pypi.org/simple/x/: ... [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    assert "root certificate" in net_trust.pip_unreachable(cert, "Ask IT.")
    answered = ("Could not fetch URL https://pkgs.contoso.com/simple/x/: 403 Client Error: Forbidden for url: "
                "https://pkgs.contoso.com/simple/x/ - skipping\n" + PIP_MISSING)
    assert net_trust.pip_unreachable(answered, "Ask IT.") is None  # the index answered, so it was reached


def test_a_notebook_environment_install_offline_says_so(tmp_path, no_proxy, monkeypatch):
    """Real pip, pointed at a package index whose name never resolves: it gives up after its two
    retries, and the job's log ends with what happened and what IT can do."""
    monkeypatch.setenv("PIP_INDEX_URL", "https://kratosforge-offline-test.invalid/simple")
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    env = Environments(tmp_path / "data", python=sys.executable)
    env.create("A")
    started = time.monotonic()
    job = wait(env, env.start("A", ["kratosforge-offline-test-pkg==1.0"]), timeout=120)
    assert job["state"] == "failed", job["log"]
    assert time.monotonic() - started < 90
    last = job["log"].rstrip().splitlines()[-1]
    assert last.startswith("pip couldn't reach the package index from this PC") and "notebook_package_folder" in last


def test_pip_is_told_to_give_up_sooner_than_its_default(tmp_path):
    cmd = environments.install_commands("py", tmp_path / "t", tmp_path / "c.txt", ["a==1"])[0]
    assert cmd[cmd.index("--retries") + 1] == str(environments.PIP_RETRIES) == "2"
    r = dbc.Runtimes(tmp_path, python="py")
    runtime_cmd = r.install_command("16.4")
    assert runtime_cmd[runtime_cmd.index("--retries") + 1] == "2"


def test_a_databricks_connect_install_offline_names_the_policy_index(tmp_path):
    r = dbc.Runtimes(tmp_path, python="py", run=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr=PIP_OFFLINE))
    out = r.install("16.4", {})
    assert not out["ok"] and "dbconnect_index_url" in out["log"].splitlines()[-1]
    missing = dbc.Runtimes(tmp_path, python="py", run=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr=PIP_MISSING))
    assert missing.install("16.4", {})["log"].rstrip().endswith("not-a-real-package")
