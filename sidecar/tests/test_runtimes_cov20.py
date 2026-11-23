"""Locked runtimes: the path the other tests leave out (coverage pass 20). Another package in the
runtime's folder isn't taken for the runtime's own distribution."""

from __future__ import annotations

import importlib

from test_runtimes import locks, runtimes_for  # noqa: F401 - locks is a fixture


def _dist(site, name, version):
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


def test_installed_skips_other_packages_and_finds_its_own(tmp_path, locks):  # noqa: F811
    r = runtimes_for(tmp_path, locks)
    site = r.folder("1.2")
    _dist(site, "other-package", "9.9")
    assert r.installed("1.2") is None          # only another package there
    _dist(site, "Thing", "1.2.3")
    # importlib.metadata caches a folder's listing by its modified time, which on Windows can read the
    # same right after a new entry is made, so the listing is read afresh here as a later start would.
    importlib.invalidate_caches()
    assert r.installed("1.2") == "1.2.3"       # its own, whatever the name's case

