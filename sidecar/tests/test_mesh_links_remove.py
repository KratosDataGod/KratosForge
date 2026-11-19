"""Removing a link another entity still reads is refused in words that say what to do (UX copy)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.central.service import PlatformMixin
from fabric_desktop.meta.project import GateError


def _svc(tmp_path, readers):
    (tmp_path / "metadata" / "links").mkdir(parents=True)
    (tmp_path / "metadata" / "links" / "regions.toml").write_text('alias = "regions"\n', encoding="utf-8")
    entities = {n: SimpleNamespace(name=n, spec={"source": {"link": "regions"}}) for n in readers}
    entities["silver.other"] = SimpleNamespace(name="silver.other", spec={"source": {"link": "elsewhere"}})
    project = SimpleNamespace(root=tmp_path, entities=entities)
    return SimpleNamespace(_project=lambda: project, mesh_overview=lambda: {"links": []})


@pytest.mark.parametrize(("readers", "names"), [
    (["silver.a"], "silver.a"),
    (["silver.a", "silver.b"], "silver.a and silver.b"),
    (["silver.a", "silver.b", "gold.c"], "silver.a, silver.b and gold.c"),
])
def test_a_link_still_read_is_kept_and_the_refusal_names_who_reads_it(tmp_path, readers, names):
    svc = _svc(tmp_path, readers)
    with pytest.raises(GateError) as err:
        PlatformMixin.mesh_delete_link(svc, "regions")
    assert err.value.errors == [(f"Link regions can't be removed, because {names} read it. "
                                "Point them at another source first, then remove the link.")]
    assert (tmp_path / "metadata" / "links" / "regions.toml").exists()


def test_a_link_nobody_reads_is_removed(tmp_path):
    svc = _svc(tmp_path, [])
    assert PlatformMixin.mesh_delete_link(svc, "regions") == {"links": []}
    assert not (tmp_path / "metadata" / "links" / "regions.toml").exists()
