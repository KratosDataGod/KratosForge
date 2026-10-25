"""One test for which cells a notebook run runs, shared by every run path (KF-DE-001)."""

import pytest

from fabric_desktop import notebook_cells


@pytest.mark.parametrize("cell,runs,runs_from_elsewhere", [
    ({"type": "code", "source": "x = 1"}, True, True),
    ({"source": "x = 1"}, True, True),  # a cell without a type is code
    ({"type": "markdown", "source": "# Title"}, False, False),
    ({"type": "code", "source": "   \n"}, False, False),
    ({"type": "code", "source": None}, False, False),
    ({"type": "code", "source": "x = 1", "meta": {"frozen": True}}, False, False),
    ({"type": "code", "source": "spark.sql('...')", "meta": {"fabric_step": True}}, True, False),
])
def test_which_cells_a_run_runs(cell, runs, runs_from_elsewhere):
    assert notebook_cells.runs(cell) is runs
    assert notebook_cells.runs(cell, skip_fabric_step=True) is runs_from_elsewhere


def test_every_run_path_uses_the_shared_test():
    """No run path writes its own frozen-cell test again (they disagreed before: notebook.run ran
    frozen cells)."""
    from pathlib import Path
    root = Path(notebook_cells.__file__).parent
    paths = ["validate_service.py", "prove_service.py", "side_by_side_service.py", "integrations/services.py",
             "schedule_service.py", "pipeline_notebooks.py", "notebook.py"]
    for path in paths:
        text = (root / path).read_text(encoding="utf-8")
        assert "notebook_cells.runs(" in text, path
        assert '.get("frozen")]' not in text, path
