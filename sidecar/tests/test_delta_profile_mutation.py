"""Mutation-testing pins for delta_profile (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import delta_profile as P


def _table(reader=1, writer=2, config=None):
    protocol = SimpleNamespace(min_reader_version=reader, min_writer_version=writer,
                               reader_features=None, writer_features=None)
    return SimpleNamespace(protocol=lambda: protocol,
                           metadata=lambda: SimpleNamespace(configuration=config or {}))


def test_writer_version_2_is_inside_the_profile_and_3_is_not():
    assert P.problems(_table(writer=2)) == []
    assert P.problems(_table(writer=3)) == ["writer version 3 > 2"]


def test_reader_version_1_is_inside_the_profile_and_2_is_not():
    assert P.problems(_table(reader=1)) == []
    assert P.problems(_table(reader=2)) == ["reader version 2 > 1"]
