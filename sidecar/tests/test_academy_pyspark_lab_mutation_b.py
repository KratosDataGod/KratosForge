"""Mutation-testing follow-ups for academy/pyspark_lab.py (batch 459): how each kind of failed run reads, an
exit code's wording at its edges, what is wrong with a table's rows in counts only, an exercise's notebooks and
data files and where they go. No learner process runs here: the runs are given as their results."""
from __future__ import annotations

import pytest

from fabric_desktop.academy import pyspark_lab as pl


# ---- a failed run, in words


@pytest.mark.parametrize("run, text", [
    ({"status": "time_limit"}, "Your code was stopped after 120 seconds."),
    ({"status": "memory_limit"}, "Your code used more memory than the exercise allows and was stopped."),
    ({"status": "refused"}, "Your code wasn't run: the app couldn't start it inside its limits."),
    ({"status": "error", "stderr": "", "exit_code": 0}, "Your code stopped with an error: no message"),
    ({"status": "error", "stderr": "  \n\n", "exit_code": None}, "Your code stopped with an error: no message"),
    ({"status": "error", "stderr": "Fatal Python error: Aborted\nFile x\n"}, "Your code crashed: Fatal Python error: Aborted."),
    ({"status": "error", "stderr": "Windows fatal exception: access violation\n", "exit_code": 5},
     "Your code crashed: Windows fatal exception: access violation (exit code 5)."),
    ({"status": "error", "stderr": "Traceback\n  ValueError: bad  \n\n", "exit_code": 1},
     "Your code stopped with an error: ValueError: bad"),
])
def test_a_failed_run_reads_as_what_happened(run, text):
    assert pl._error(run) == text


@pytest.mark.parametrize("code, text", [(0, ""), (None, ""), ("3", ""), (1, "exit code 1"), (255, "exit code 255"),
                                        (256, "exit code 0x00000100"), (-1, "signal 1"), (-9, "signal 9")])
def test_an_exit_code_reads_as_posix_or_windows_reports_it(code, text):
    assert pl._exit_code(code) == text


def test_lower_first_lowers_only_the_first_letter():
    assert pl._lower_first("Your Code") == "your Code"
    assert pl._lower_first("") == ""


def test_a_count_says_one_or_many():
    assert pl._n(1, "row", "rows") == "1 row"
    assert pl._n(0, "row", "rows") == "0 rows"
    assert pl._n(1234, "row", "rows") == "1,234 rows"


# ---- what is wrong with the rows


@pytest.mark.parametrize("got, want, text", [
    ([(1,), (1,), (2,)], [(1,), (2,)],
     "t on x has 3 rows where 2 are expected: some rows appear more than once."),
    ([(1,)], [(1,), (2,)],
     "t on x has 1 row where 2 are expected; 0 rows aren't right and 1 expected row is missing."),
    ([(1,), (3,), (4,)], [(1,), (2,)],
     "t on x has 3 rows where 2 are expected; 2 rows aren't right and 1 expected row is missing."),
    ([(9,), (8,), (7,)], [(1,), (2,)],
     "t on x has 3 rows where 2 are expected; 3 rows aren't right and 2 expected rows are missing."),
    ([(1,), (1,), (2,)], [(1,), (2,), (2,)], "t on x has the right rows but some appear a different number of times."),
    ([(1,), (3,)], [(1,), (2,)], "t on x has the right number of rows, but 1 of them isn't right."),
    ([(4,), (3,)], [(1,), (2,)], "t on x has the right number of rows, but 2 of them aren't right."),
])
def test_a_row_difference_is_told_in_counts_only(got, want, text):
    assert pl._row_difference("t", "on x", got, want) == text


# ---- notebooks and data files


def test_good_assets_have_no_problems():
    assert pl.asset_problems({}) == []
    assert pl.asset_problems({"notebooks": ["clean_orders.py"], "files": ["a.csv"], "files_folder": "landing/orders"}) == []


@pytest.mark.parametrize("ex", [{"notebooks": "a.py"}, {"files": "a.csv"}])
def test_assets_that_arent_lists_are_one_problem(ex):
    assert pl.asset_problems(ex) == ["notebooks and files are lists of file names in the exercise's folder"]


@pytest.mark.parametrize("name", ["clean.txt", "bad name.py", ".py", 5, "-lead.py", "a" * 64 + ".py"])
def test_a_notebook_not_named_like_one_is_a_problem(name):
    assert pl.asset_problems({"notebooks": [name, "ok.py"]}) == [
        f"notebook {name!r} is a .py file named like a notebook (clean_orders.py)"]


@pytest.mark.parametrize("folder", ["", "a//b", "../up", "with space", 5, "/lead"])
def test_a_files_folder_not_under_files_is_a_problem(folder):
    assert pl.asset_problems({"files_folder": folder}) == [
        "files_folder is a folder under Files/, like landing or landing/orders"]


def test_assets_go_to_notebooks_and_files_or_their_folder(tmp_path):
    (tmp_path / "clean.py").write_bytes(b"print(1)\n")
    (tmp_path / "a.csv").write_bytes(b"x\n1\n")
    assert pl.assets({"notebooks": ["clean.py"], "files": ["a.csv"]}, tmp_path) == {
        "notebooks/clean.py": b"print(1)\n", "Files/a.csv": b"x\n1\n"}
    assert pl.assets({"files": ["a.csv"], "files_folder": "landing/orders"}, tmp_path) == {
        "Files/landing/orders/a.csv": b"x\n1\n"}
    assert pl.assets({}, tmp_path) == {}


# ---- the workspace and the package


def test_a_workspace_file_that_is_a_folder_is_refused(tmp_path):
    (tmp_path / "answer.py").mkdir()
    with pytest.raises(pl.ws.Refused, match=r"^answer.py isn't a plain file; reset the exercise\.$"):
        pl._put(tmp_path, "answer.py", "x = 1\n")


def test_a_plain_workspace_file_is_written(tmp_path):
    pl._put(tmp_path, "answer.py", "x = 1\n")
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "x = 1\n"


def test_tables_read_back_are_at_most_256_mb():
    assert pl.MAX_TABLE_BYTES == 256 * 1024 * 1024 == 268_435_456


def test_cleanup_removes_the_built_package_once(tmp_path, monkeypatch):
    pkg = tmp_path / "kf-learner-x"
    (pkg / "kf_learner").mkdir(parents=True)
    monkeypatch.setattr(pl, "_PKG", pkg)
    pl.cleanup_package()
    assert not pkg.exists() and pl._PKG is None
    pl.cleanup_package()   # nothing built: nothing to do
    assert pl._PKG is None


def test_prepare_refuses_an_asset_whose_place_is_a_folder(tmp_path):
    (tmp_path / "notebooks" / "clean.py").mkdir(parents=True)
    with pytest.raises(pl.ws.Refused, match=r"^notebooks/clean.py isn't a plain file; reset the exercise\.$"):
        pl.prepare(tmp_path, "SELECT 1", {"notebooks/clean.py": b"print(1)\n"})


def test_prepare_writes_its_assets_and_tables(tmp_path):
    names = pl.prepare(tmp_path, "CREATE SCHEMA s; CREATE TABLE s.t AS SELECT 1 AS id;",
                       {"notebooks/clean.py": b"print(1)\n", "Files/in/a.csv": b"x\n"})
    assert names == ["s.t"] and (tmp_path / "notebooks" / "clean.py").read_bytes() == b"print(1)\n"
    assert (tmp_path / "Files" / "in" / "a.csv").read_bytes() == b"x\n"


# ---- reading a table back


def _table_dir(tmp_path):
    pl.prepare(tmp_path, "CREATE SCHEMA s; CREATE TABLE s.t AS SELECT 1 AS id;")
    return pl.table_path(tmp_path, "s.t")


def test_a_data_file_the_log_lists_that_isnt_a_file_is_refused(tmp_path):
    for data in _table_dir(tmp_path).glob("*.parquet"):
        data.unlink()
        data.mkdir()
    with pytest.raises(pl.ws.Refused, match=r"^s.t's Delta log lists a data file that isn't there\.$"):
        pl._rows(tmp_path, "s.t")


def test_loading_a_table_that_wasnt_written_says_so(tmp_path):
    import duckdb
    _table_dir(tmp_path)
    con = duckdb.connect(":memory:")
    try:
        assert pl._load(con, tmp_path, "s.missing") is False
        assert pl._load(con, tmp_path, "s.t") is True
        assert con.execute('SELECT id FROM "s"."t"').fetchall() == [(1,)]
    finally:
        con.close()


def test_a_table_over_the_byte_limit_names_the_limit_in_mb(tmp_path, monkeypatch):
    pl.prepare(tmp_path, "CREATE SCHEMA s; CREATE TABLE s.t AS SELECT md5(i::VARCHAR) || md5((i + 7)::VARCHAR) AS v "
                         "FROM range(60000) r(i);")
    monkeypatch.setattr(pl, "MAX_TABLE_BYTES", 1024 * 1024)
    with pytest.raises(pl.ws.Refused, match=r"^s.t is bigger than 1 MB; "):
        pl._rows(tmp_path, "s.t")


def test_cleanup_of_a_package_folder_already_gone_is_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_PKG", tmp_path / "gone")
    pl.cleanup_package()
    assert pl._PKG is None


def test_a_reference_run_keeps_the_last_4000_characters_of_its_output(tmp_path):
    pl.prepare(tmp_path, "CREATE SCHEMA s; CREATE TABLE s.t AS SELECT 1 AS id;")
    run = pl.run_reference(tmp_path, "import sys\nprint('x' * 5000, end='')\nsys.stderr.write('e' * 5000)\n")
    assert run["status"] == "ok" and run["stdout"] == "x" * 4000 and run["stderr"] == "e" * 4000


def test_tables_can_be_prepared_again_in_the_same_workspace(tmp_path):
    assert pl.prepare(tmp_path, SETUP) == ["s.t"]
    assert pl.prepare(tmp_path, SETUP) == ["s.t"]
    assert pl._rows(tmp_path, "s.t") == (["id"], [(1,)])


# ---- grading, with the runs stood in for


SETUP = "CREATE SCHEMA s; CREATE TABLE s.t AS SELECT 1 AS id;"
FAILED = {"status": "error", "exit_code": 1, "stderr": "Traceback\nValueError: bad"}


def _write(work, ids, name="s.out"):
    import deltalake
    import pyarrow as pa
    path = pl.table_path(work, name)
    path.mkdir(parents=True, exist_ok=True)
    deltalake.write_deltalake(str(path), pa.table({"id": pa.array(ids, pa.int64())}), mode="overwrite")


def _runner(plan):
    """``plan(code, work, call)`` writes what that run leaves and returns its result, or None for ok."""
    calls: dict[str, int] = {}

    def run(work, code):
        calls[str(work)] = calls.get(str(work), 0) + 1
        return plan(code, work, calls[str(work)]) or {"status": "ok", "exit_code": 0, "stdout": "", "stderr": ""}
    return run


def _grade(validation, plan, hidden=None):
    return pl.grade({"id": "X1", "validation": validation}, SETUP, hidden, "answer", "solution", _runner(plan))


def _writes(answer_ids, solution_ids=(1,)):
    def plan(code, work, call):
        _write(work, list(answer_ids if code == "answer" else solution_ids))
    return plan


def test_a_failed_first_run_fails_every_check():
    out = _grade([{"kind": "result_matches", "table": "s.out"}, {"kind": "idempotent", "table": "s.out"}],
                 lambda code, work, call: FAILED if code == "answer" else _write(work, [1]))
    assert [r["passed"] for r in out["results"]] == [False, False]
    assert {r["detail"] for r in out["results"]} == {"Your code stopped with an error: ValueError: bad"}


def test_a_failure_on_hidden_data_fails_its_check():
    def plan(code, work, call):
        if code == "answer" and work.name == "hidden":
            return FAILED
        _write(work, [1])
    out = _grade([{"kind": "result_matches", "table": "s.out", "data": "hidden"}], plan, hidden=SETUP)
    assert out["results"] == [{"kind": "result_matches", "passed": False, "detail": pl.HIDDEN_FAILED}]


def test_a_reference_solution_that_fails_is_an_error_of_the_exercise():
    def plan(code, work, call):
        if code == "solution":
            return FAILED
        _write(work, [1])
    with pytest.raises(RuntimeError, match="^X1: the reference solution failed: Your code stopped with an error"):
        _grade([{"kind": "result_matches", "table": "s.out"}], plan)


def test_the_score_is_rounded_to_two_places():
    out = _grade([{"kind": "result_matches", "table": "s.out"}, {"kind": "table_shape", "table": "s.none"},
                  {"kind": "table_shape", "table": "s.nada"}], _writes([1]))
    assert [r["passed"] for r in out["results"]] == [True, False, False] and out["score"] == 0.33


def test_wrong_rows_are_told_in_counts():
    out = _grade([{"kind": "result_matches", "table": "s.out"}], _writes([1, 2], [1, 3]))
    assert out["results"][0]["detail"] == "s.out on the exercise data has the right number of rows, but 1 of them isn't right."


def test_a_second_run_that_fails_says_it_was_the_second():
    def plan(code, work, call):
        if call == 2:
            return FAILED
        _write(work, [1])
    out = _grade([{"kind": "idempotent", "table": "s.out"}], plan)
    assert out["results"][0] == {"kind": "idempotent", "passed": False,
                                 "detail": "Run a second time, your code stopped with an error: ValueError: bad"}


def test_a_rerun_that_writes_the_table_first_counts_from_zero():
    def plan(code, work, call):
        if call == 2:
            _write(work, [1])
    out = _grade([{"kind": "idempotent", "table": "s.out"}], plan)
    assert out["results"][0]["detail"].startswith("Running your code again changed s.out (0 rows, then 1).")


def test_a_rerun_that_removes_the_table_counts_to_zero():
    import shutil

    def plan(code, work, call):
        if call == 1:
            _write(work, [1])
        else:
            shutil.rmtree(pl.table_path(work, "s.out"))
    out = _grade([{"kind": "idempotent", "table": "s.out"}], plan)
    assert out["results"][0]["detail"].startswith("Running your code again changed s.out (1 rows, then 0).")
