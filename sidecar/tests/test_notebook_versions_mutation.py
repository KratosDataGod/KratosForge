"""notebook_versions.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4) showed
no test pinned. The coalescing window, how many versions and checkpoints are kept and for how long, the
label length, what the index holds after a delete, rebuilding the index, renaming onto existing versions,
and the refusals. Notebooks are written to a temporary folder."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import notebook_versions as V

T0 = 1_800_000_000.0
DAY = 86400


def nb(*sources, markdown=0):
    cells = [{"cell_type": "code", "source": s} for s in sources]
    cells += [{"cell_type": "markdown", "source": f"m{i}"} for i in range(markdown)]
    return {"cells": cells}


def kinds(root, name="NB"):
    return [v["kind"] for v in V.listing(root, name, None)["versions"]]


def test_an_automatic_version_within_five_minutes_replaces_the_last_and_one_after_doesnt(tmp_path):
    V.record(tmp_path, "NB", nb("a"), now=T0)
    out = V.record(tmp_path, "NB", nb("b"), now=T0 + 299)
    assert len(kinds(tmp_path)) == 1
    index = json.loads((tmp_path / "NB" / V.INDEX).read_text())
    assert [e["id"] for e in index["versions"]] == [out["id"]]  # the index names the new version, not the old
    assert len(list((tmp_path / "NB").glob("2*.json"))) == 1  # the replaced file is gone too
    V.record(tmp_path, "NB", nb("c"), now=T0 + 299 + 300)
    assert len(kinds(tmp_path)) == 2


def test_automatic_versions_are_kept_30_days_to_the_second(tmp_path):
    V.record(tmp_path, "NB", nb("old"), now=T0)
    V.record(tmp_path, "NB", nb("edge"), now=T0 + 30 * DAY)
    assert len(kinds(tmp_path)) == 2  # exactly 30 days old: kept
    V.record(tmp_path, "NB", nb("new"), kind="checkpoint", label="later", now=T0 + 30 * DAY + 1)
    assert kinds(tmp_path) == ["checkpoint", "auto"]  # the first is now over 30 days old


def test_at_most_100_automatic_versions_are_kept(tmp_path):
    for i in range(101):
        V.record(tmp_path, "NB", nb(str(i)), now=T0 + i * 301)
    assert len(kinds(tmp_path)) == 100
    assert len(list((tmp_path / "NB").glob("2*.json"))) == 100


def test_200_checkpoints_and_no_more_but_saves_still_keep_versions(tmp_path):
    for i in range(200):
        V.record(tmp_path, "NB", nb("x"), kind="checkpoint", label=f"c{i}", now=T0 + i)
    with pytest.raises(ValueError, match="has 200 checkpoints"):
        V.record(tmp_path, "NB", nb("x"), kind="checkpoint", label="one more", now=T0 + 500)
    assert V.record(tmp_path, "NB", nb("y"), now=T0 + 600)["kind"] == "auto"


def test_a_label_is_cut_to_80_characters(tmp_path):
    out = V.record(tmp_path, "NB", nb("a"), kind="checkpoint", label="x" * 100, now=T0)
    assert out["label"] == "x" * 80


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError, match="kind is one of auto, checkpoint"):
        V.record(tmp_path, "NB", nb("a"), kind="sometimes", now=T0)


def test_a_version_that_isnt_kept_is_refused(tmp_path):
    with pytest.raises(ValueError, match="isn't kept any more"):
        V.get(tmp_path, "NB", "20260101000000-0123abcd")


def test_the_summary_counts_cells_and_code_cells(tmp_path):
    out = V.record(tmp_path, "NB", nb("a", "b", markdown=1), now=T0)
    assert (out["cells"], out["code"]) == (3, 2)


def test_a_cell_source_as_lines_or_none_hashes_as_its_text():
    lines = {"cells": [{"cell_type": "code", "source": ["a\n", "b"]}]}
    text = {"cells": [{"cell_type": "code", "source": "a\nb"}]}
    assert V.content_hash(lines) == V.content_hash(text)
    assert V.content_hash({"cells": [{"cell_type": "code", "source": None}]}) == \
        V.content_hash({"cells": [{"cell_type": "code", "source": ""}]})
    assert V.content_hash(text) != V.content_hash({"cells": [{"cell_type": "code", "source": "a"}]})


def test_delete_leaves_the_others_in_the_index_and_makes_no_folder_for_a_notebook_without_versions(tmp_path):
    a = V.record(tmp_path, "NB", nb("a"), kind="checkpoint", label="a", now=T0)
    b = V.record(tmp_path, "NB", nb("b"), kind="checkpoint", label="b", now=T0 + 1)
    V.delete(tmp_path, "NB", a["id"])
    index = json.loads((tmp_path / "NB" / V.INDEX).read_text())
    assert [e["id"] for e in index["versions"]] == [b["id"]]
    V.delete(tmp_path, "NB", "20260101000000-0123abcd")  # not on disk: nothing to do
    V.delete(tmp_path, "Other", "20260101000000-0123abcd")
    assert not (tmp_path / "Other").exists()


def test_a_rebuilt_index_lists_newest_first(tmp_path):
    ids = [V.record(tmp_path, "NB", nb(str(i)), kind="checkpoint", label=str(i), now=T0 + i)["id"] for i in range(3)]
    (tmp_path / "NB" / V.INDEX).unlink()
    assert [v["id"] for v in V.listing(tmp_path, "NB", None)["versions"]] == ids[::-1]


@pytest.mark.parametrize("bad", ["not an entry", {"id": "x"}])
def test_an_index_with_a_bad_entry_is_rebuilt(tmp_path, bad):
    ids = [V.record(tmp_path, "NB", nb(str(i)), kind="checkpoint", label=str(i), now=T0 + i)["id"] for i in range(2)]
    path = tmp_path / "NB" / V.INDEX
    index = json.loads(path.read_text())
    index["versions"][0] = bad
    path.write_text(json.dumps(index))
    assert [v["id"] for v in V.listing(tmp_path, "NB", None)["versions"]] == ids[::-1]


def test_keep_copy_makes_the_folder_then_reuses_it(tmp_path):
    src = tmp_path / "broken.ipynb"
    src.write_text("<<<<<<< HEAD")
    first = V.keep_copy(tmp_path / "root", "NB", src)
    second = V.keep_copy(tmp_path / "root", "NB", src)
    assert first.read_text() == second.read_text() == "<<<<<<< HEAD" and first.parent == second.parent


def test_rename_moves_the_versions_or_joins_them_without_replacing_any(tmp_path):
    a = V.record(tmp_path, "A", nb("a"), now=T0)
    V.rename(tmp_path, "A", "B")
    assert not (tmp_path / "A").exists() and (tmp_path / "B" / f"{a['id']}.json").is_file()
    V.rename(tmp_path, "B", "Folder/Sub/B")  # into folders that aren't there yet
    assert (tmp_path / "Folder" / "Sub" / "B" / f"{a['id']}.json").is_file()
    V.rename(tmp_path, "Folder/Sub/B", "B")

    c = V.record(tmp_path, "C", nb("c"), now=T0 + 1000)
    (tmp_path / "C" / f"{a['id']}.json").write_text("same id, other file")  # an id already kept under B
    V.rename(tmp_path, "C", "B")
    assert (tmp_path / "B" / f"{c['id']}.json").is_file()
    assert json.loads((tmp_path / "B" / f"{a['id']}.json").read_text())["id"] == a["id"]  # not replaced


def test_rename_of_a_notebook_that_shares_a_folder_moves_only_its_own_files(tmp_path):
    v = V.record(tmp_path, "Sales", nb("s"), now=T0)
    (tmp_path / "Sales" / "Child").mkdir()  # another notebook's versions, in folder Sales
    V.rename(tmp_path, "Sales", "Archive/Deep")
    assert (tmp_path / "Archive" / "Deep" / f"{v['id']}.json").is_file()
    assert (tmp_path / "Sales" / "Child").is_dir()
