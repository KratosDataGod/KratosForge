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
