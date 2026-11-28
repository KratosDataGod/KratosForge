"""conftest's network guard (no test reaches the network) stays one guard with one list when a test
imports conftest again, as test_test_ids does with `from tests import conftest`. Before, the second import
put in a second guard with its own list, so no_network checked a list nothing wrote to, and a test that
swallowed the refused connect passed."""

from __future__ import annotations

import importlib.util
import ipaddress
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


def _seen_and_cleared(expected: str) -> None:
    guard = socket.socket.connect
    assert guard.kf_outbound == [expected]
    guard.kf_outbound.clear()  # seen, so this test itself doesn't fail at no_network's check


def test_a_datagram_sent_off_this_pc_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
            pytest.raises(ConnectionRefusedError, match="refused a datagram to"):
        sock.sendto(b"x", ("192.0.2.1", 9))
    _seen_and_cleared("('192.0.2.1', 9)")


def test_a_send_on_a_udp_socket_connected_off_this_pc_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))  # only picks a route, so allowed
        with pytest.raises(ConnectionRefusedError, match="refused a datagram to"):
            sock.send(b"x")
    _seen_and_cleared("('192.0.2.1', 9)")


@pytest.mark.skipif(not hasattr(socket.socket, "sendmsg"), reason="Windows sockets have no sendmsg")
def test_a_sendmsg_off_this_pc_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
            pytest.raises(ConnectionRefusedError, match="refused a datagram to"):
        sock.sendmsg([b"x"], [], 0, ("192.0.2.1", 9))
    _seen_and_cleared("('192.0.2.1', 9)")


def test_a_datagram_to_loopback_is_sent():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as there, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as here:
        there.bind(("127.0.0.1", 0))
        here.sendto(b"x", there.getsockname())
        here.connect(there.getsockname())
        here.send(b"y")
        assert there.recv(8) == b"x" and there.recv(8) == b"y"
    assert socket.socket.connect.kf_outbound == []


def test_a_host_name_lookup_is_refused_but_local_names_resolve():
    with pytest.raises(socket.gaierror, match="refused a lookup of"):
        socket.getaddrinfo("example.com", 443)
    _seen_and_cleared("'example.com'")
    for host in ("localhost", "127.0.0.1", "::1", None):
        assert socket.getaddrinfo(host, 80)


def test_a_tcp_connect_to_this_pcs_own_address_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("10.255.255.255", 1))
        own = route.getsockname()[0]
    if ipaddress.ip_address(own).is_loopback or ipaddress.ip_address(own).is_unspecified:
        pytest.skip("this PC has no address but loopback")
    with socket.socket() as server, socket.socket() as client:
        server.bind((own, 0))
        server.listen(1)
        client.connect(server.getsockname())  # not loopback, but this PC's own address: stays here
    assert socket.socket.connect.kf_outbound == []


def test_a_name_under_invalid_fails_to_resolve_without_a_lookup():
    # test_offline uses one to check what the user is told when a name doesn't resolve
    for name in ("kratosforge-offline-test.invalid", "x.INVALID."):
        with pytest.raises(socket.gaierror) as caught:
            socket.getaddrinfo(name, 443)
        assert caught.value.errno == socket.EAI_NONAME
    assert socket.socket.connect.kf_outbound == []
