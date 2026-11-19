"""Databricks Connect edges the main tests don't pin down (mutation batch 132,
docs/quality/mutation-testing.md): the runtime lines it knows, the connect wait, gRPC's root
certificates, and what a session refuses."""

from __future__ import annotations

import inspect

import pytest

from fabric_desktop.engines import dbconnect as dbc
from fabric_desktop.engines.contract import Unsupported

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
M2M = {"platform": "databricks", "host": HOST,
       "credential": {"method": "oauth-m2m", "client_id": "c", "secret": "env:KF_SP_SECRET"}}


def test_connecting_waits_up_to_fifteen_minutes_for_a_cluster_to_start():
    assert dbc.START_SECONDS == 15 * 60
    assert inspect.signature(dbc.DatabricksConnectSession).parameters["start_seconds"].default == 900


@pytest.mark.parametrize("version, python", [("13.3.x-scala2.12", "3.10"), ("14.3.x-scala2.12", "3.10"),
                                             ("15.4.x-scala2.12", "3.11")])
def test_runtimes_13_to_15_say_which_python_they_need(version, python):
    problem = dbc.check(version, None, python="3.12")[0]
    assert f"runs Python {python}; KratosForge's Python is 3.12" in problem


def test_runtime_19_is_known_and_20_is_newer():
    problems = " ".join(dbc.check("19.0.x-scala2.13", None, python="3.12"))
    assert "newer" not in problems and "13.3 and later" not in problems
    assert "newer than this version" in dbc.check("20.0.x-scala2.13", None, python="3.12")[0]
    assert max(dbc.RUNTIME_PYTHON) == 19 and min(dbc.RUNTIME_PYTHON) == 13


@pytest.fixture
def m2m_env(monkeypatch):
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: None)
    return lambda **kw: dbc.worker_env(M2M, "c-1", base=kw.pop("base", {}), environ={"KF_SP_SECRET": "s"}, **kw)


def test_grpc_trusts_windows_roots_when_a_folder_is_given(m2m_env, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(dbc.net_trust, "windows_roots_pem",
                        lambda folder: asked.append(folder) or folder / "windows-roots.pem")
    env = m2m_env(roots_dir=tmp_path)
    assert env["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == str(tmp_path / "windows-roots.pem") and asked == [tmp_path]


def test_grpc_roots_are_left_alone_without_a_folder_or_when_already_set_or_none_found(m2m_env, monkeypatch, tmp_path):
    monkeypatch.setattr(dbc.net_trust, "windows_roots_pem", lambda folder: pytest.fail("roots written"))
    assert "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH" not in m2m_env()
    env = m2m_env(base={"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": "/mine.pem"}, roots_dir=tmp_path)
    assert env["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == "/mine.pem"
    monkeypatch.setattr(dbc.net_trust, "windows_roots_pem", lambda folder: None)
    assert "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH" not in m2m_env(roots_dir=tmp_path)


def test_a_session_refuses_variables_plans_and_the_catalog_with_the_descriptors_notes():
    session = dbc.DatabricksConnectSession.__new__(dbc.DatabricksConnectSession)
    for call, cap in ((session.variables, "variables"), (lambda: session.plan("x"), "plan"),
                      (session.catalog, "catalog")):
        with pytest.raises(Unsupported) as exc:
            call()
        assert str(exc.value) == dbc.DATABRICKS_CONNECT.capability(cap).note


def test_the_adapter_describes_databricks_connect(tmp_path):
    adapter = dbc.DatabricksConnectAdapter(runtimes=dbc.Runtimes(tmp_path), cluster_of=lambda c, i: {})
    assert adapter.describe() is dbc.DATABRICKS_CONNECT
