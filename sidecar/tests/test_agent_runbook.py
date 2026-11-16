"""docs/agent-runbook.md quotes the messages admins see. Each quoted message must still be in the
code it names, so a reworded message fails here until the runbook says the new words too."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "agent-runbook.md"
SOURCE = ROOT / "sidecar" / "fabric_desktop"
QUOTED = re.compile(r"^- \*\*Message\*\* \(`([^`]+)`[^)]*\): `([^`]+)`$", re.M)


def _code_text(path: Path) -> str:
    """The file with adjacent string literals joined, the way Python joins them: "a " f"b" is "a b"."""
    text = path.read_text(encoding="utf-8").replace('\\"', '"')
    return re.sub(r"""["']\s*\n?\s*[fr]?["']""", "", text)


def _quoted() -> list[tuple[str, str]]:
    return QUOTED.findall(RUNBOOK.read_text(encoding="utf-8"))


def test_the_runbook_quotes_messages():
    assert len(_quoted()) >= 40


def test_every_quoted_message_is_still_in_the_code_it_names():
    missing = []
    for file, message in _quoted():
        code = _code_text(SOURCE / file)
        # <angle brackets> stand for values filled in when the message is written
        pieces = [p for p in re.split(r"<[^>]+>", message) if len(p.strip()) >= 4]
        assert pieces, message
        missing += [f"{file}: {p.strip()!r} (from {message!r})" for p in pieces if p not in code and p.strip() not in code]
    assert not missing, "the runbook quotes text the code no longer writes:\n" + "\n".join(missing)


def _anchors() -> set[str]:
    """The runbook's headings as the docs site links to them: lower case, punctuation dropped, spaces as -."""
    headings = re.findall(r"^#{2,4} (.+)$", RUNBOOK.read_text(encoding="utf-8"), re.M)
    return {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in headings}


def test_every_reason_the_agent_stops_at_start_has_a_runbook_section():
    from fabric_desktop import agent_start
    assert set(agent_start.STAGES) == set(agent_start.RUNBOOK)
    missing = {stage: a for stage, a in agent_start.RUNBOOK.items() if a not in _anchors()}
    assert not missing, f"agent-start.log stages whose runbook section is gone: {missing}"


def test_every_stage_the_cli_records_is_one_the_runbook_covers():
    from fabric_desktop import agent_start
    cli = (SOURCE / "cli.py").read_text(encoding="utf-8")
    used = set(re.findall(r"""(?:stopped|agent_start\.record)\([^,()]+, "(\w+)\"""", cli))
    assert used and used <= set(agent_start.STAGES), used - set(agent_start.STAGES)
    assert used == set(agent_start.STAGES)  # a stage nothing records any more goes from RUNBOOK too


def test_each_start_log_entry_names_its_runbook_section(tmp_path):
    from fabric_desktop import agent_start
    for stage, anchor in agent_start.RUNBOOK.items():
        agent_start.record(tmp_path, stage, "why", 2)
        assert f"stage={stage} exit=2" in (tmp_path / "logs" / agent_start.START_LOG).read_text(encoding="utf-8")
        assert (tmp_path / "logs" / agent_start.START_LOG).read_text(encoding="utf-8").endswith(
            f"  see docs/agent-runbook.md#{anchor}\n  why\n")
