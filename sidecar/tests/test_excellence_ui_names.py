"""The Excellence screen names every evidence kind a control needs, so Gaps and the evidence lists
never show a raw id like "lakehouse.properties"."""

from __future__ import annotations

import re
from pathlib import Path

from fabric_desktop.excellence import catalog

UI = Path(__file__).resolve().parents[2] / "app" / "ui" / "optimize-excellence.js"


def _named() -> set[str]:
    block = re.search(r"const EXC_KIND = \{(.*?)\};", UI.read_text(encoding="utf-8"), re.S)
    assert block, "EXC_KIND not found"
    return set(re.findall(r'"([a-z]+\.[a-z_]+)":', block.group(1)))


def test_every_evidence_kind_a_control_needs_has_a_name_on_screen():
    needed = {k for c in catalog.registry().controls() for k in c.evidence_kinds}
    assert needed - _named() == set()
