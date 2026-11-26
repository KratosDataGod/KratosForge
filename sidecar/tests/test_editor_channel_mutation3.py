"""editor_channel.py: the mutants that test_editor_channel_mutation.py and the module's other tests leave alive.
The user check is given port 0 when the channel has no port, a refused one-byte body is read while one without
a read flag is left, and a refused body is still read with half a second left."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import editor_channel as ec


def _discard(length):
    reads = []

    class Rfile:
        def read1(self, n):
            reads.append(n)
            return b"x" * n

    fake = SimpleNamespace(_body_read=False, headers={"Content-Length": str(length)}, rfile=Rfile(), timeout=5,
                           connection=SimpleNamespace(settimeout=lambda s: None))
    ec._Handler._discard_body(fake)
    return reads


def test_the_user_check_is_given_port_0_when_the_channel_has_no_port(monkeypatch):
    asked = []
    monkeypatch.setattr(ec, "peer", SimpleNamespace(checked=lambda: True,
                                                    same_user=lambda a, b: asked.append((a, b)) or True))
    fake = SimpleNamespace(client_address=("127.0.0.1", 50000), owner=SimpleNamespace(port=None),
                           headers={"Host": "127.0.0.1:None"})
    ec._Handler._guard(fake)
    assert asked == [(50000, 0)]


def test_a_refused_one_byte_body_is_read_and_one_without_a_read_flag_is_left():
    assert _discard(1) == [1]
    reads = []
    fake = SimpleNamespace(headers={"Content-Length": "10"}, rfile=SimpleNamespace(read1=reads.append),
                           timeout=5, connection=SimpleNamespace(settimeout=lambda s: None))
    ec._Handler._discard_body(fake)  # no _body_read at all: taken as already read
    assert reads == []


def test_a_refused_body_is_still_read_with_half_a_second_left(monkeypatch):
    clock = iter([0.0, 1.5, 1.9])
    monkeypatch.setattr(ec, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    assert _discard(10) == [10]
