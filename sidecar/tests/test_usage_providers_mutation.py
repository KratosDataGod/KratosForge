"""usage/providers.py paths that mutation testing (batch 52, docs/quality/mutation-testing.md) showed no
test pinned: a layout can't be changed after it's made, the base provider refuses to read rows, a tie
between two layouts keeps the first one, and a column with no header is never listed as left out.
"""

import dataclasses

import pytest

from fabric_desktop.usage import providers


def _layout(id_: str, columns: dict[str, tuple[str, ...]], required: tuple[str, ...]) -> providers.Layout:
    return providers.Layout(id=id_, label=id_.title(), columns=columns, required=required, timed=True)


class _Two(providers.Provider):
    id = "two"
    label = "Two layouts"
    layouts = (
        _layout("first", {"cu": ("CU",), "item": ("Item",)}, ("cu",)),
        _layout("second", {"cu": ("CU",), "start": ("Start",)}, ("cu",)),
    )


def test_a_layout_is_frozen():
    layout = _Two.layouts[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.id = "changed"  # type: ignore[misc]


def test_the_base_provider_does_not_read_rows():
    ctx = providers.Context(file_name="f.csv", imported_at=None, options=providers.Options(),  # type: ignore[arg-type]
                            tz=None, decimal=None, date_order=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        providers.Provider().row({}, _Two.layouts[0], ctx)


def test_when_two_layouts_fit_equally_the_first_one_is_kept():
    # Both layouts find CU and one more column, so both score (True, 2).
    got = providers.match(_Two(), ["CU", "Item", "Start"])
    assert got["score"] == (True, 2)
    assert got["layout"].id == "first"
    assert got["ignored"] == ["Start"]


def test_a_column_with_no_header_is_not_listed_as_left_out():
    got = providers.match(_Two(), ["CU", "", "Item", "Other"])
    assert got["layout"].id == "first" and got["index"] == {"cu": 0, "item": 2}
    assert got["ignored"] == ["Other"]
