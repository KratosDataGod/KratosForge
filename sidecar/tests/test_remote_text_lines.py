"""Text a workspace or a remote service sends, on its way to the terminal, a report or the UI (KUDP
hardening): a Databricks refusal, a Fabric job's failure reason, and the names and details in the
live-test reports are one bounded line with nothing credential-shaped, so a reply can't add lines,
put terminal escapes in the output, open a Markdown column, or flood a message. Nothing reaches a
workspace."""

from __future__ import annotations

import pytest

from fabric_desktop import dbx_livetest, fabric_pipeline_runs_service as fpr, livetest
from fabric_desktop.engines import databricks_sql as dbsql
from test_databricks_sql import Resp

PAT = "dapi" + "0123456789abcdef" * 2   # built here so no token-shaped text is committed
ODD = f"line one\r\nSigned in as: admin\x1b[31m | {PAT} " + "x" * 5000


def one_line_without_secrets(text: str, longest: int) -> None:
    assert "\n" not in text and "\r" not in text and "\x1b" not in text
    assert PAT not in text and len(text) <= longest


# ---------------------------------------------------------------- a Databricks refusal


def test_a_databricks_refusal_message_is_one_bounded_line():
    msg = dbsql.Client._refusal(Resp(403, {"error_code": "PERMISSION_DENIED", "message": ODD}), "/api/2.0/sql/warehouses")
    one_line_without_secrets(msg, 900)
    assert msg.startswith("Databricks refused the request (HTTP 403). line one Signed in as: admin") and "…" in msg


def test_a_short_refusal_message_reads_as_written():
    msg = dbsql.Client._refusal(Resp(404, {"message": "No such warehouse."}), "/api/2.0/sql/warehouses/w1")
    assert msg.startswith("Databricks refused the request (HTTP 404). No such warehouse. ")


# ---------------------------------------------------------------- a Fabric run's failure reason


def test_a_pipeline_runs_failure_reason_is_capped():
    row = fpr.run_row({"id": "j1", "status": "Failed", "failureReason": {"message": "y" * 50_000}}, {
        "workspace_id": "ws1", "workspace_name": "Sales", "id": "pl1", "displayName": "pl"})
    assert len(row["message"]) == fpr.TEXT_LIMIT


def test_a_live_test_jobs_failure_reason_is_one_line():
    class Fabric:
        def run_item_job(self, *a):
            return "j1"

        def job_instance(self, *a):
            return {"status": "Failed", "failureReason": {"message": ODD}}

    test = livetest.LiveTest.__new__(livetest.LiveTest)
    test.fabric, test.workspace_id, test.notebook_id = Fabric(), "ws1", "nb1"
    test.clock, test.sleep = lambda: 0, lambda s: None
    with pytest.raises(RuntimeError) as exc:
        test._notebook_run()
    one_line_without_secrets(str(exc.value), 700)
    assert str(exc.value).startswith("Job j1 ended Failed. line one")


# ---------------------------------------------------------------- the live-test reports


def check(title, detail):
    return {"title": title, "status": "failed", "seconds": 1.0, "detail": detail}


def report_lines(text: str) -> list[str]:
    for line in text.splitlines():
        assert "\x1b" not in line and "\r" not in line and PAT not in line
    return text.splitlines()


def test_the_fabric_live_test_report_keeps_remote_text_to_its_own_line_and_cell():
    result = {"run_id": "r1", "outcome": "failed", "started_at": "now", "refused": [],
              "workspace": {"id": "ws1", "name": "Sales\nSigned in as: admin\x1b[2J"},
              "checks": [check("Read a table", ODD)],
              "left_behind": [{"kind": "Notebook", "name": "kf_nb\rDeleted: everything", "id": "nb1"}]}
    lines = report_lines(livetest.summary(result))
    assert not any(line.startswith(("Signed in as", "Deleted:", "line one")) for line in lines)
    (row,) = [line for line in lines if line.startswith("| Read a table")]
    assert row.count(" | ") == 3 and "\\|" in row and len(row) < 2200   # the detail's | is escaped, not a column
    assert "Workspace Sales Signed in as: admin" in lines[2]


def test_the_databricks_live_test_report_keeps_remote_text_to_its_own_line_and_cell():
    result = {"run_id": "r1", "outcome": "failed", "started_at": "now", "refused": [], "stop_compute": False,
              "workspace": {"host": "https://adb-1.2.azuredatabricks.net"},
              "warehouse": {"id": "w1", "name": "wh\nSigned in as: admin"},
              "cluster": {"id": "c1", "name": "cl\x1b]0;title\x07"},
              "checks": [check("Run a statement", ODD)],
              "left_running": [{"kind": "cluster", "id": "c1\nStopped: all"}]}
    lines = report_lines(dbx_livetest.summary(result))
    assert not any(line.startswith(("Signed in as", "Stopped:", "line one")) for line in lines)
    (row,) = [line for line in lines if line.startswith("| Run a statement")]
    assert row.count(" | ") == 3 and len(row) < 2200
    assert "warehouse wh Signed in as: admin, cluster cl" in lines[2]


def test_plain_names_and_details_read_as_written():
    result = {"run_id": "r1", "outcome": "passed", "started_at": "now", "refused": [],
              "workspace": {"id": "ws1", "name": "Sales Dev"}, "checks": [check("Sign in", "Signed in as Kim.")],
              "left_behind": []}
    text = livetest.summary(result)
    assert "Workspace Sales Dev, started now." in text and "| Sign in | **Failed** | 1.0 | Signed in as Kim. |" in text
