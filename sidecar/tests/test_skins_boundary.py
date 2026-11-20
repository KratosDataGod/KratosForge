"""skins.py at its exact edge (mutation batch 164, a boundary re-score of batch 24,
docs/quality/mutation-testing.md): a color pair with exactly the minimum contrast is readable."""

from fabric_desktop import skins


def _problems(monkeypatch, ratio):
    monkeypatch.setattr(skins, "contrast", lambda a, b: ratio)
    fg, bg = skins.CONTRAST_PAIRS[0]
    problems: list[str] = []
    skins._tokens({fg: "#111111"}, "light", problems, {fg: "#000000", bg: "#ffffff"})
    return problems


def test_exactly_the_minimum_contrast_is_enough(monkeypatch):
    assert _problems(monkeypatch, skins.CONTRAST_MIN) == []
    [problem] = _problems(monkeypatch, skins.CONTRAST_MIN - 0.01)
    assert "it needs 4.5:1 to be readable" in problem
