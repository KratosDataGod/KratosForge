"""conftest's network guard (no test reaches the network) stays one guard with one list when a test
imports conftest again, as test_test_ids does with `from tests import conftest`. Before, the second import
put in a second guard with its own list, so no_network checked a list nothing wrote to, and a test that
swallowed the refused connect passed."""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest


def _load_conftest_again():
    spec = importlib.util.spec_from_file_location("conftest_loaded_again", Path(__file__).with_name("conftest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_importing_conftest_again_keeps_one_guard_and_one_list():
    guard = socket.socket.connect
    again = _load_conftest_again()
    assert socket.socket.connect is guard
    assert again._outbound is guard.kf_outbound and again._own is guard.kf_own


def test_a_swallowed_connect_after_a_second_import_is_still_seen():
    guard = socket.socket.connect
    _load_conftest_again()
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError, match="don't reach the network"):
        sock.connect(("192.0.2.1", 443))  # TEST-NET-1: refused here before any packet leaves
    assert guard.kf_outbound == ["('192.0.2.1', 443)"]
    guard.kf_outbound.clear()  # seen, so this test itself doesn't fail at no_network's check


def test_a_udp_connect_that_only_picks_a_route_is_allowed():
    # test_integrations_limits finds this PC's own address this way; no packet leaves
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("10.255.255.255", 1))
        own = sock.getsockname()[0]
    assert socket.socket.connect.kf_outbound == []
    # the address the route lookup gave is this PC's own, so a connect to it stays on this PC
    assert own in socket.socket.connect.kf_own
