"""Mutation batch 484: the small rules of notebook_sync's merge that the large-notebook tests
don't pin (the id of a cell kept both ways, where an added cell goes, what counts as a change)."""
import hashlib
import json
import re

import pytest

from fabric_desktop.notebook_sync import SyncBases, _copy_id, merge_cells


def cell(cid, source, **extra):
    return {"cell_id": cid, "type": "code", "source": source, **extra}


def ids(out):
    return [c["cell_id"] for c in out["cells"]]


def test_the_second_copy_of_a_cell_gets_the_first_free_theirs_id():
    assert _copy_id("a", set()) == "a-theirs"
    assert _copy_id("a", {"a-theirs"}) == "a-theirs2"
    assert _copy_id("a", {"a-theirs", "a-theirs2"}) == "a-theirs3"


def test_the_copy_id_keeps_only_id_characters_and_at_most_40_of_them():
    assert _copy_id("a b.c", set()) == "abc-theirs"
    assert _copy_id("!! ..", set()) == "cell-theirs"
    assert _copy_id("x" * 50, set()) == "x" * 40 + "-theirs"


def test_when_every_theirs_id_is_taken_the_copy_gets_a_random_16_character_id():
    taken = {"a-theirs"} | {f"a-theirs{n}" for n in range(2, 1000)}
    new = _copy_id("a", taken)
    assert re.fullmatch(r"[0-9a-f]{16}", new)


def test_a_cell_with_an_empty_or_non_text_id_is_refused():
    for bad in ("", 5):
        with pytest.raises(ValueError, match="cell id"):
            merge_cells([{"cell_id": bad, "source": ""}], [], [])


def test_a_change_to_a_cells_settings_alone_is_a_change():
    base = [cell("a", "1", meta={"n": 1})]
    out = merge_cells(base, [cell("a", "1", meta={"n": 2})], base)
    assert out["resolved"] and out["cells"][0]["meta"] == {"n": 2}


def test_cells_added_on_both_sides_without_a_move_follow_the_other_programs_after_the_same_neighbour():
    out = merge_cells([cell("a", "1")], [cell("a", "1"), cell("m1", "m")], [cell("a", "1"), cell("t1", "t")])
    assert ids(out) == ["a", "t1", "m1"]


def test_a_cell_added_first_in_the_other_program_goes_first():
    out = merge_cells([cell("a", "1")], [cell("a", "1")], [cell("t0", "t"), cell("a", "1")])
    assert ids(out) == ["t0", "a"]


def test_a_base_written_under_another_notebooks_name_is_no_base(tmp_path):
    bases = SyncBases(tmp_path)
    folder = bases._folder("nb")  # noqa: SLF001
    folder.mkdir(parents=True)
    (folder / "stamp0001.json").write_text(json.dumps({"name": "other", "cells": []}), encoding="utf-8")
    assert bases.find("nb", "stamp0001") is None
    (folder / "stamp0002.json").write_text(json.dumps({"name": "nb", "cells": "x"}), encoding="utf-8")
    assert bases.find("nb", "stamp0002") is None
    (folder / "stamp0003.json").write_text(json.dumps({"name": "nb", "cells": []}), encoding="utf-8")
    assert bases.find("nb", "stamp0003") == []


def test_a_cell_added_after_the_other_programs_first_cell_goes_after_it():
    base = [cell("a", "1"), cell("b", "2")]
    out = merge_cells(base, base, [cell("a", "1"), cell("t1", "t"), cell("b", "2")])
    assert ids(out) == ["a", "t1", "b"]


def test_bases_already_on_disk_are_found_in_their_notebooks_folder(tmp_path):
    folder = tmp_path / hashlib.sha256("nb".encode("utf-8")).hexdigest()[:24]
    folder.mkdir()
    (folder / "stamp0001.json").write_text(json.dumps({"name": "nb", "cells": [cell("a", "1")]}), encoding="utf-8")
    assert SyncBases(tmp_path).find("nb", "stamp0001") == [cell("a", "1")]
