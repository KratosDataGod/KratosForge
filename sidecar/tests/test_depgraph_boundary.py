"""Boundary kill test for depgraph (mutation testing, batch 167)."""
from __future__ import annotations

from fabric_desktop.depgraph import analyze


def test_open_with_only_a_path_reads_and_is_no_effect():
    out = analyze('text = open("/lakehouse/default/Files/a.txt").read()\n')
    assert not any("writes the file" in e["text"] for e in out["effects"])
    out = analyze('open("/lakehouse/default/Files/a.txt", "w").write("x")\n')
    assert any("writes the file" in e["text"] for e in out["effects"])
