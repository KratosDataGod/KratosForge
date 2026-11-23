"""Follow-ups to the reader hardening: symlinked and device-named Ossie paths, notebook cells whose
source isn't text, raw cells, non-UTF-8 pushes, audit folders, negative anchors, lookalike secret keys."""
from __future__ import annotations

import json
import os
import random

import pytest

from fabric_desktop import audit_anchor, connprofiles, notebook_files
from fabric_desktop.audit import AuditLog
from fabric_desktop.engines import databricks_workspace_sync as sync
from fabric_desktop.notebook import NotebookStore, merge_saved, to_ipynb
from tests.test_model_ossie import _ossie, svc  # noqa: F401  (svc is a fixture)


def _symlink(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks aren't permitted here")


# ---------------------------------------------------------------- 1, 2: Ossie paths


def test_an_ossie_save_through_a_symlinked_folder_is_refused(svc, tmp_path):  # noqa: F811
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(svc._project().root / "linked", outside)
    with pytest.raises(ValueError, match="because a link in it points outside the project"):
        svc.meta_model_ossie_save("linked/Sales.ossie.yaml", _ossie())
    assert list(outside.iterdir()) == []


def test_an_ossie_read_through_a_symlinked_folder_is_refused(svc, tmp_path):  # noqa: F811
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Sales.ossie.yaml").write_text(_ossie(), encoding="utf-8")
    _symlink(svc._project().root / "linked", outside)
    with pytest.raises(ValueError, match="because a link in it points outside the project"):
        svc.meta_model_ossie_read("linked/Sales.ossie.yaml")


def test_a_symlink_that_stays_inside_the_project_still_works(svc):  # noqa: F811
    root = svc._project().root
    (root / "semantic").mkdir(exist_ok=True)
    _symlink(root / "alias", root / "semantic")
    assert svc.meta_model_ossie_save("alias/Sales.ossie.yaml", _ossie())["path"] == "alias/Sales.ossie.yaml"


@pytest.mark.parametrize("path", ["semantic/CON.ossie.yaml", "semantic/nul.ossie.json", "Com1/Sales.ossie.yaml",
                                  "semantic/lpt9.x.ossie.yml", "aux/Sales.ossie.yaml", "semantic/PRN.ossie.yaml",
                                  # Windows drops a trailing space or dot, so these are the device too
                                  "CON .ossie.yaml", "nul./x.ossie.yaml", "semantic/aux. .ossie.yaml", "prn /x.ossie.yaml"])
def test_windows_device_names_are_refused_in_ossie_paths(svc, path):  # noqa: F811
    with pytest.raises(ValueError, match="because Windows reserves names"):
        svc.meta_model_ossie_save(path, _ossie())
    with pytest.raises(ValueError, match="because Windows reserves names"):
        svc.meta_model_ossie_read(path)


def test_a_name_that_only_starts_like_a_device_is_fine(svc):  # noqa: F811
    assert svc.meta_model_ossie_save("semantic/CONsole.ossie.yaml", _ossie())["path"] == "semantic/CONsole.ossie.yaml"


# ---------------------------------------------------------------- 3: the app's own notebook files


def _nb(tmp_path, source):
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
          "cells": [{"cell_type": "code", "source": source, "metadata": {}, "id": "a"}]}
    (tmp_path / "nb.ipynb").write_text(json.dumps(nb), encoding="utf-8")
    return nb


@pytest.mark.parametrize("source", [[1, 2], 5, {"a": 1}, None])
def test_load_refuses_a_cell_whose_source_isnt_text_by_name(tmp_path, source):
    _nb(tmp_path, source)
    with pytest.raises(ValueError, match=r"nb\.ipynb can't be opened, because cell 1's source isn't text"):
        NotebookStore(tmp_path).load("nb")


def test_save_over_a_file_whose_cell_source_isnt_text_is_refused_and_leaves_it(tmp_path):
    _nb(tmp_path, [1, 2])
    before = (tmp_path / "nb.ipynb").read_bytes()
    with pytest.raises(ValueError, match=r"nb\.ipynb wasn't saved, because cell 1's source isn't text in the file on disk"):
        NotebookStore(tmp_path).save("nb", [{"type": "code", "source": "x"}], compute="local", workspace={})
    with pytest.raises(ValueError, match="wasn't saved, because .* in the file on disk"):
        NotebookStore(tmp_path).save("nb", [{"type": "code", "source": "x"}])
    assert (tmp_path / "nb.ipynb").read_bytes() == before


def test_merge_saved_refuses_a_cell_whose_source_isnt_text(tmp_path):
    before = _nb(tmp_path, [1, 2])
    with pytest.raises(ValueError, match="The notebook wasn't saved, because cell 1's source isn't text in the file on disk"):
        merge_saved(to_ipynb([{"type": "code", "source": "x"}], None), before)


# ---------------------------------------------------------------- 4: raw cells opened from a file


def test_notebook_files_read_notes_a_raw_cell():
    nb = {"nbformat": 4, "metadata": {}, "cells": [{"cell_type": "markdown", "source": "# t"},
                                                    {"cell_type": "raw", "source": "plain"}]}
    got = notebook_files.read(nb)
    assert got["cells"][1]["type"] == "code"
    assert "Cell 2. The cell's type (raw) isn't code or markdown, so it comes in as a code cell." in got["summary"]["notes"]


def test_notebook_files_read_has_no_type_note_for_code_and_markdown():
    nb = {"nbformat": 4, "metadata": {}, "cells": [{"cell_type": "code", "source": "1"}]}
    assert not any("isn't code or markdown" in n for n in notebook_files.read(nb)["summary"]["notes"])


# ---------------------------------------------------------------- 5: a push of a file that isn't UTF-8


@pytest.mark.parametrize("name", ["job.py", "nb.ipynb"])
def test_a_push_of_a_file_that_isnt_utf8_is_refused_by_name(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"x = '\xff\xfe'\n")
    with pytest.raises(ValueError, match=rf"{name} wasn't pushed, because it isn't UTF-8 text") as got:
        sync._content(path)
    assert not isinstance(got.value, UnicodeDecodeError)


def test_a_push_of_a_damaged_ipynb_is_refused_by_name_and_its_notes_never_crash(tmp_path):
    """The push path reads .ipynb through parse_ipynb: every damaged notebook is a ValueError there, never a crash,
    and the loss notes shown before a push say it can't be read instead of raising."""
    rng = random.Random(3176)
    good = json.dumps({"nbformat": 4, "cells": [
        {"cell_type": "code", "source": ["x = 1\n", "print(x)"], "outputs": []},
        {"cell_type": "markdown", "source": "# T", "metadata": {"tags": ["a"]}},
        {"cell_type": "raw", "source": "plain"}]})
    shapes = [5, None, "x", [1, None], {"a": [1]}, "\ud800", [{"cell_type": "code", "source": 5}]]
    path = tmp_path / "nb.ipynb"
    for _ in range(300):
        text = good
        kind = rng.randrange(4)
        if kind == 0:
            text = text[:rng.randrange(len(text))]
        elif kind == 1:
            at = rng.randrange(len(text))
            text = text[:at] + chr(rng.randrange(32, 127)) + text[at + 1:]
        else:
            nb = json.loads(good)
            target = nb if kind == 2 else rng.choice(nb["cells"])
            target[rng.choice(list(target) + ["cells", "source", "cell_type"])] = rng.choice(shapes)
            text = json.dumps(nb)
        path.write_text(text, encoding="utf-8", errors="surrogatepass")
        try:
            sync._content(path)
        except ValueError:
            pass
        notes = sync._loss_notes(path)
        assert isinstance(notes, list), text


# ---------------------------------------------------------------- 6: a folder named like an audit file


def test_a_folder_named_like_an_audit_file_is_skipped(tmp_path):
    """main's audit_chain.files() reads only regular files, so the folder never crashes a reader."""
    log = AuditLog(tmp_path)
    log.record("run", "ok", 0.1)
    (tmp_path / "audit-1999-01.jsonl").mkdir()
    assert [e["action"] for e in log.entries()] == ["run"]
    result = log.verify()
    assert result["lines"] == 1 and result["ok"]


# ---------------------------------------------------------------- 7: event log anchors


def test_an_anchor_with_a_negative_line_count_is_refused():
    assert audit_anchor.parse(audit_anchor.text(audit_anchor.Anchor("f" * 32, "h" * 64, -3))) is None
    assert audit_anchor.parse(audit_anchor.text(audit_anchor.Anchor("f" * 32, "h" * 64, 0))) is not None


# ---------------------------------------------------------------- 8: lookalike secret key names


@pytest.mark.parametrize("key", ["ｓｅｃｒｅｔ", "ｐａｓｓｗｏｒｄ", "ＰＷＤ", "client_ｓｅｃｒｅｔ"])
def test_a_secret_key_in_lookalike_letters_must_be_a_reference(key):
    problems = connprofiles.secret_problems({"credential": {key: "plain-value"}})
    assert problems and "must be a reference" in problems[0]
    assert connprofiles.secret_problems({"credential": {key: "env:" + "MY_REF"}}) == []
